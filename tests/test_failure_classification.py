"""Result classification — six failure classes + ok payload + disclosures.

Pure classify()/_ok_payload() tests over synthetic outputs; nothing spawns.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SERVICES = Path(__file__).resolve().parent.parent / "services"
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from node_fable import runner

ACCT = "a@x.com"


def _ok_stdout(result="Answer text.", model="claude-fable-5-1", session="s-1", cost=0.557):
    return json.dumps({
        "session_id": session,
        "total_cost_usd": cost,
        "duration_ms": 108000,
        "modelUsage": {model: {"inputTokens": 10}},
        "permission_denials": [],
        "terminal_reason": "end_turn",
        "is_error": False,
        "result": result,
        "num_turns": 1,
    })


def test_timeout_class():
    out = runner.classify(-1, "", "timeout after 900s", True, account=ACCT)
    assert out["failure_class"] == "timeout"


def test_malformed_output_non_json_no_retry():
    out = runner.classify(0, "not json at all {", "", False, account=ACCT)
    assert out["failure_class"] == "malformed_output"


def test_subprocess_error_nonzero_exit_non_json():
    out = runner.classify(2, "", "SessionError: account is the active login", False, account=ACCT)
    assert out["failure_class"] == "subprocess_error"
    assert out["returncode"] == 2


def test_rate_limited_attributed_7d():
    stdout = json.dumps({
        "is_error": True,
        "api_error_status": 429,
        "result": "Rate limit reached for the seven_day window",
        "session_id": "s-9",
    })
    out = runner.classify(1, stdout, "", False, account=ACCT)
    assert out["failure_class"] == "rate_limited"
    assert out["window_attribution"] == "7d"
    assert out["failover_eligible"] is True


def test_rate_limited_unattributable_429_also_failover_eligible():
    stdout = json.dumps({
        "is_error": True,
        "api_error_status": 429,
        "result": "Too many requests",
        "session_id": "s-10",
    })
    out = runner.classify(1, stdout, "", False, account=ACCT)
    assert out["failure_class"] == "rate_limited"
    # never assumed 7d — but still failover-eligible on the current account
    assert out["window_attribution"] == "unattributable"
    assert out["failover_eligible"] is True


def test_rate_limited_detected_from_non_json_stderr():
    out = runner.classify(1, "", "HTTP 429 too many requests", False, account=ACCT)
    assert out["failure_class"] == "rate_limited"


def test_refused_heuristic_positive():
    out = runner.classify(0, _ok_stdout(result="I can't help with that request."), "", False, account=ACCT)
    assert out["failure_class"] == "refused"
    assert out["session_id"] == "s-1"


def test_refused_heuristic_negative_terse_legit_result():
    # Terse but substantive: no first-person refusal opener → ok.
    out = runner.classify(0, _ok_stdout(result="Yes — line 42 is the bug."), "", False, account=ACCT)
    assert out["status"] == "ok"


def test_refused_heuristic_negative_long_result_with_code():
    long = "I cannot say this is trivial.\n```python\nprint(1)\n```\n" + "x" * 700
    out = runner.classify(0, _ok_stdout(result=long), "", False, account=ACCT)
    assert out["status"] == "ok"


def test_ok_payload_fable_no_disclosure():
    cls = runner.classify(0, _ok_stdout(), "", False, account=ACCT)
    payload = runner._ok_payload(cls["data"], role="plan", effort="high", account=ACCT)
    assert payload["status"] == "ok"
    assert payload["model_used"] == "claude-fable-5-1"
    assert payload["fallback_disclosure"] is None
    assert payload["cost_usd"] == 0.557
    assert payload["session_id"] == "s-1"
    assert payload["result"] == "Answer text."


def test_ok_payload_opus_loud_disclosure_prepended():
    cls = runner.classify(0, _ok_stdout(model="claude-opus-5"), "", False, account=ACCT)
    payload = runner._ok_payload(cls["data"], role="plan", effort="high", account=ACCT)
    banner = payload["fallback_disclosure"]
    assert banner is not None
    assert banner.startswith("⚠️ FALLBACK MODEL ANSWERED: claude-opus-5")
    assert "NOT Fable" in banner
    assert payload["result"].startswith(banner)


def test_ok_payload_opus_4_8_disclosure_names_tier():
    cls = runner.classify(0, _ok_stdout(model="claude-opus-4-8"), "", False, account=ACCT)
    payload = runner._ok_payload(cls["data"], role="plan", effort="high", account=ACCT)
    assert "claude-opus-4-8" in payload["fallback_disclosure"]


# ── closing-review fixes (2026-09-07): tier naming, primary-model pick, ──────
# ── armed-stop disclosure symmetry ──────────────────────────────────────────

def test_refused_names_the_tier():
    """The caller-driven ladder depends on knowing WHICH tier refused —
    the refused payload names it in both a field and the message."""
    stdout = json.dumps({
        "session_id": "s-r", "is_error": False,
        "result": "I cannot help with that request.",
        "modelUsage": {"claude-opus-5": {"costUSD": 0.05}},
    })
    out = runner.classify(0, stdout, "", False,
                          account="a@x.com", model="claude-opus-5")
    assert out["failure_class"] == "refused"
    assert out["refused_tier"] == "claude-opus-5"
    assert out["model"] == "claude-opus-5"
    assert "claude-opus-5" in out["message"]


def test_error_payloads_carry_the_requested_model():
    for rc, stdout, stderr, timed_out, klass in (
        (-1, "", "", True, "timeout"),
        (3, "", "boom", False, "subprocess_error"),
        (0, "not json", "", False, "malformed_output"),
    ):
        out = runner.classify(rc, stdout, stderr, timed_out,
                              account="a@x.com", model="claude-fable-5-1")
        assert out["failure_class"] == klass
        assert out["model"] == "claude-fable-5-1"


def test_model_used_picks_primary_by_cost_not_first_key():
    """an earlier measurement measured real Opus calls list a claude-haiku side-call
    FIRST in modelUsage — first-key would banner the WRONG model on
    exactly the fallback calls the loud disclosure exists for."""
    data = {
        "modelUsage": {
            "claude-haiku-4-5-20251001": {"costUSD": 0.000957},
            "claude-opus-5": {"costUSD": 0.0670255},
        },
        "result": "PONG", "session_id": "s-o",
    }
    assert runner._model_used(data) == "claude-opus-5"
    payload = runner._ok_payload(data, role="plan", effort="high", account="a@x.com")
    assert payload["model_used"] == "claude-opus-5"
    assert "claude-opus-5" in payload["fallback_disclosure"]
    assert "haiku" not in payload["fallback_disclosure"]
