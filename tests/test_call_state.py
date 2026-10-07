"""Call-state store — resume rules (a)-(d), cooldowns, fail-closed reads."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SERVICES = Path(__file__).resolve().parent.parent / "services"
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from node_fable import call_state
from node_fable.errors import FableError


def _store(tmp_path):
    return call_state.CallStateStore(tmp_path / "call-state.json")


def test_record_and_get_roundtrip(tmp_path):
    store = _store(tmp_path)
    store.record_result("s-1", role="implement", account="a@x.com",
                        terminal_reason="end_turn", failure_class=None,
                        status="ok")
    row = store.get("s-1")
    assert row["role"] == "implement"
    assert row["account"] == "a@x.com"
    assert row["status"] == "ok"
    assert row["failure_class"] is None
    assert row["created_ts"] <= row["last_ts"]


def test_record_pending_then_overwritten_by_record_result(tmp_path):
    """The row a fresh mint gets BEFORE its subprocess ever spawns — status
    'pending', no terminal_reason/failure_class yet — and what happens to
    it once the real outcome lands. This is the row shape a killed/timed-
    out attempt now leaves behind instead of nothing (an earlier finding
    2026-09-08 23:19 EDT)."""
    store = _store(tmp_path)
    store.record_pending("s-mint", role="plan", account="a@x.com")
    row = store.get("s-mint")
    assert row["status"] == "pending"
    assert row["role"] == "plan"
    assert row["account"] == "a@x.com"
    assert row["terminal_reason"] is None
    assert row["failure_class"] is None
    # A timeout terminal result overwrites the SAME row rather than
    # creating a second one.
    store.record_result("s-mint", role="plan", account="a@x.com",
                        terminal_reason=None, failure_class="timeout",
                        status="error")
    row = store.get("s-mint")
    assert row["status"] == "error"
    assert row["failure_class"] == "timeout"
    assert row["created_ts"] <= row["last_ts"]


def test_record_pending_ignores_empty_session_id(tmp_path):
    store = _store(tmp_path)
    store.record_pending("", role="plan", account="a@x.com")
    assert store.get("") is None


@pytest.mark.parametrize("role", ["adversarial_review", "review_diff"])
def test_rule_a_review_role_resume_rejected_as_self_review(tmp_path, role):
    store = _store(tmp_path)
    store.record_result("s-1", role="implement", account="a@x.com",
                        terminal_reason="end_turn", failure_class=None)
    with pytest.raises(FableError) as ei:
        call_state.validate_resume(store, "s-1", role)
    assert ei.value.error_class == "self_review"


def test_rule_b_refused_session_refused_for_resume(tmp_path):
    store = _store(tmp_path)
    store.record_result("s-2", role="plan", account="a@x.com",
                        terminal_reason=None, failure_class="refused")
    with pytest.raises(FableError) as ei:
        call_state.validate_resume(store, "s-2", "implement")
    assert ei.value.error_class == "refused_session"


def test_rule_c_unknown_session_fail_closed(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(FableError) as ei:
        call_state.validate_resume(store, "never-seen", "implement")
    assert ei.value.error_class == "unknown_session"


def test_rule_a_adversarial_review_resumes_own_prior_session(tmp_path):
    store = _store(tmp_path)
    store.record_result("s-5", role="adversarial_review", account="a@x.com",
                        terminal_reason="end_turn", failure_class=None)
    row = call_state.validate_resume(store, "s-5", "adversarial_review")
    assert row["account"] == "a@x.com"


def test_rule_a_review_diff_still_rejects_unconditionally(tmp_path):
    store = _store(tmp_path)
    # Same stored session adversarial_review is now allowed to resume —
    # review_diff must still refuse it. Proves the scope boundary is real,
    # not merely a name that happens to differ from adversarial_review.
    store.record_result("s-6", role="adversarial_review", account="a@x.com",
                        terminal_reason="end_turn", failure_class=None)
    with pytest.raises(FableError) as ei:
        call_state.validate_resume(store, "s-6", "review_diff")
    assert ei.value.error_class == "self_review"


def test_rule_b_refused_session_rejection_untouched_for_both_review_roles(tmp_path):
    store = _store(tmp_path)
    store.record_result("s-7", role="adversarial_review", account="a@x.com",
                        terminal_reason=None, failure_class="refused")
    # adversarial_review resuming its OWN role's refused session: same-role
    # provenance match does not bypass the refused-session gate.
    with pytest.raises(FableError) as ei:
        call_state.validate_resume(store, "s-7", "adversarial_review")
    assert ei.value.error_class == "refused_session"
    # review_diff against the identical refused row: still unconditional
    # self-review — the refused-session gate never even applies to it.
    with pytest.raises(FableError) as ei2:
        call_state.validate_resume(store, "s-7", "review_diff")
    assert ei2.value.error_class == "self_review"


def test_docstring_carries_two_part_adversarial_review_resume_criterion():
    doc = call_state.__doc__
    assert "material changes were made since it" in doc
    assert "Fable will benefit from the prior context" in doc


def test_rule_d_resume_returns_recorded_account_pin(tmp_path):
    store = _store(tmp_path)
    store.record_result("s-3", role="diagnose", account="pinned@x.com",
                        terminal_reason="end_turn", failure_class=None)
    row = call_state.validate_resume(store, "s-3", "implement")
    assert row["account"] == "pinned@x.com"


def test_cooldown_set_read_and_expiry(tmp_path):
    store = _store(tmp_path)
    store.set_cooldown("a@x.com", 600)
    assert store.in_cooldown("a@x.com") is True
    assert "a@x.com" in store.cooldowns()
    # an already-expired cooldown is pruned on read
    store.set_cooldown("b@x.com", 0.0001)
    import time
    time.sleep(0.01)
    assert store.in_cooldown("b@x.com") is False


def test_corrupt_state_file_fails_closed(tmp_path):
    path = tmp_path / "call-state.json"
    path.write_text("{not json", encoding="utf-8")
    store = call_state.CallStateStore(path)
    with pytest.raises(FableError) as ei:
        store.get("anything")
    assert ei.value.error_class == "state_corrupt"


def test_missing_account_in_row_fails_closed(tmp_path):
    store = _store(tmp_path)
    store.record_result("s-4", role="plan", account="", terminal_reason=None,
                        failure_class=None)
    # record_result with empty account still writes the row; validate refuses
    with pytest.raises(FableError) as ei:
        call_state.validate_resume(store, "s-4", "plan")
    assert ei.value.error_class == "state_corrupt"
