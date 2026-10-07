"""consult() orchestration — selector loop, failover, resume pinning, and
the scrub chokepoint on EVERY spawn path. subprocess + cswap fully mocked."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SERVICES = Path(__file__).resolve().parent.parent / "services"
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from node_fable import accounts, call_state, config, mcp_grant, runner, scrub
from node_fable.errors import FableError


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv(config.ENV_STATE_DIR, str(tmp_path / "state"))
    monkeypatch.setenv(config.ENV_ACCOUNTS, "a@x.com,b@x.com")
    # Task G: --mcp-config/--settings are now generated from disk state.
    # These orchestration tests don't assert on that content — isolate it
    # anyway so a real census/registration file on the host can never make
    # this suite's outcome depend on live machine state.
    monkeypatch.setenv(mcp_grant.ENV_CENSUS_PATH, str(tmp_path / "no-census.json"))
    monkeypatch.setenv(mcp_grant.ENV_MCP_JSON_PATH, str(tmp_path / "no-mcp.json"))
    monkeypatch.setenv(mcp_grant.ENV_CLAUDE_JSON_PATH, str(tmp_path / "no-claude.json"))
    # Never read the machine's live default login: a test that cares about the
    # active account overrides this stub itself.
    monkeypatch.setattr(runner, "_active_account_email", lambda snapshot=None: "nobody@x.com")
    return tmp_path


def _rows(*emails):
    # gate_ok=True: these orchestration tests exercise failover/resume/
    # arming behavior downstream of the selector, not the 5h/7d gate itself
    # (that is accounts.py's own test_accounts.py) — every fixture row must
    # clear the gate explicitly, since select() fails closed on a missing
    # "gate_ok" key (a gate that could not be verified is not passed).
    return [
        {
            "email": e,
            "number": str(i + 1),
            "is_active": False,
            "windows": [],
            "five_h_avail": 90.0 - i,
            "seven_d_avail": 90.0 - i,
            "fable_headroom": 90.0 - i,
            "gate_ok": True,
            "usage_age_s": 5.0,
            "in_cooldown": False,
            "cooldown_until": None,
            "disabled": False,
        }
        for i, e in enumerate(emails)
    ]


def _ok_stdout(session="s-ok", model="claude-fable-5-1"):
    return json.dumps({
        "session_id": session,
        "total_cost_usd": 0.5,
        "duration_ms": 1000,
        "modelUsage": {model: {}},
        "permission_denials": [],
        "terminal_reason": "end_turn",
        "is_error": False,
        "result": "Substantive answer with detail.",
        "num_turns": 1,
    })


_RATE_STDOUT = json.dumps({
    "is_error": True,
    "api_error_status": 429,
    "result": "rate limit reached (seven_day)",
    "session_id": "s-429",
})


class SpawnRecorder:
    def __init__(self, results):
        self.results = list(results)
        self.calls: list[dict] = []

    def __call__(self, argv, brief, cwd, timeout):
        self.calls.append({"argv": argv, "brief": brief, "cwd": cwd})
        return self.results.pop(0)


class ScrubCounter:
    def __init__(self):
        self.count = 0

    def __call__(self, text):
        self.count += 1
        return None


def _wire(monkeypatch, spawn, rows=("a@x.com", "b@x.com"), scrub_counter=None):
    monkeypatch.setattr(runner, "_spawn", spawn)
    monkeypatch.setattr(
        accounts, "account_rows",
        lambda snapshot=None, cooldowns=None, oauth_mod=None: _rows(*rows),
    )
    if scrub_counter is not None:
        monkeypatch.setattr(scrub, "scan_or_refuse", scrub_counter)


def test_fresh_success_first_candidate(env, monkeypatch):
    spawn = SpawnRecorder([(0, _ok_stdout(), "", False)])
    counter = ScrubCounter()
    _wire(monkeypatch, spawn, scrub_counter=counter)
    out = runner.consult(role="plan", brief="plan the refactor")
    assert out["status"] == "ok"
    assert out["account"] == "a@x.com"
    assert out["effort"] == "high"
    assert len(spawn.calls) == 1
    assert counter.count == 1  # scrub ran before the (single) spawn
    argv = spawn.calls[0]["argv"]
    assert "a@x.com" in argv
    assert spawn.calls[0]["brief"] == "plan the refactor"
    # A fresh call mints its OWN session_id and passes it via --session-id
    # BEFORE spawn — never the CLI fixture's own "s-ok" claim (workspace
    # finding, 2026-09-08: a timed-out call never gets to read the CLI's
    # JSON at all, so the caller's own minted id has to be authoritative on
    # every path, not just the ones where stdout happens to parse).
    assert "--session-id" in argv
    minted = argv[argv.index("--session-id") + 1]
    assert minted != "s-ok"
    assert out["session_id"] == minted
    store = call_state.CallStateStore(config.call_state_path())
    row = store.get(minted)
    assert row["account"] == "a@x.com"
    assert row["status"] == "ok"


def test_fresh_timeout_leaves_an_addressable_row(env, monkeypatch):
    """The core of an earlier finding: before this
    fix, a killed subprocess's CLI JSON never printed, classify()'s timeout
    branch carried no session_id at all, and _record's `if sid:` guard then
    silently skipped writing ANY row — a timed-out consult was invisible to
    call-state entirely, with no id anywhere to name the orphaned
    transcript. Now: the mint happens BEFORE spawn, survives the kill, and
    lands in both the returned payload and a real call-state row."""
    spawn = SpawnRecorder([(-1, "", "killed after 900s", True)])
    _wire(monkeypatch, spawn, rows=("a@x.com",))
    out = runner.consult(role="plan", brief="plan something huge")
    assert out["status"] == "error"
    assert out["failure_class"] == "timeout"
    minted = out["session_id"]
    assert minted  # never None/empty post-fix
    argv = spawn.calls[0]["argv"]
    assert argv[argv.index("--session-id") + 1] == minted
    store = call_state.CallStateStore(config.call_state_path())
    row = store.get(minted)
    assert row is not None
    assert row["status"] == "error"
    assert row["failure_class"] == "timeout"
    assert row["account"] == "a@x.com"


def test_timeout_then_resume_launches_from_the_identical_scratch_cwd(env, monkeypatch):
    """A timed-out session is now a real resume candidate: the scratch cwd is
    keyed by session_id (runner._scratch_dir_for), not a random tempdir, so a
    later -r on the same id launches from the SAME cwd the original attempt
    used -- the cwd Claude's own persisted transcript is filed under. Before
    this fix, every call (fresh or resume) got a brand-new random tempdir, so
    a resume attempt hashed to a directory the timed-out attempt never wrote
    to and would have found nothing there, resumable transcript or not."""
    import os

    existed_at_spawn: list[bool] = []

    class _RecordingSpawn(SpawnRecorder):
        def __call__(self, argv, brief, cwd, timeout):
            # Checked HERE, before the caller's own finally block rmtree's
            # the scratch dir on return -- a post-hoc check after both
            # consult() calls complete would always see it already cleaned
            # up regardless of whether _scratch_dir_for ever ran.
            existed_at_spawn.append(os.path.isdir(cwd))
            return super().__call__(argv, brief, cwd, timeout)

    spawn = _RecordingSpawn([
        (-1, "", "killed after 900s", True),
        (0, _ok_stdout(session="placeholder"), "", False),
    ])
    _wire(monkeypatch, spawn, rows=("a@x.com",))
    timed_out = runner.consult(role="plan", brief="plan something huge")
    minted = timed_out["session_id"]
    assert minted

    resumed = runner.consult(role="plan", brief="continue", session_id=minted)
    assert resumed["status"] == "ok"

    fresh_cwd = spawn.calls[0]["cwd"]
    resume_cwd = spawn.calls[1]["cwd"]
    assert fresh_cwd == resume_cwd, (
        "resume must launch from the identical scratch cwd the timed-out "
        "attempt used, or Claude's cwd-keyed session lookup cannot find the "
        "persisted transcript"
    )
    assert existed_at_spawn == [True, True], (
        "the scratch dir must be a real directory at spawn time on BOTH "
        "calls, not just a deterministic-looking string"
    )


def test_rate_limited_fails_over_and_cools_down(env, monkeypatch):
    spawn = SpawnRecorder([
        (1, _RATE_STDOUT, "", False),
        (0, _ok_stdout(session="s-b"), "", False),
    ])
    counter = ScrubCounter()
    _wire(monkeypatch, spawn, scrub_counter=counter)
    out = runner.consult(role="diagnose", brief="why is it slow")
    assert out["status"] == "ok"
    assert out["account"] == "b@x.com"
    assert len(spawn.calls) == 2
    # scrub chokepoint ran before EVERY spawn — once per account attempt
    assert counter.count == 2
    store = call_state.CallStateStore(config.call_state_path())
    assert store.in_cooldown("a@x.com") is True
    assert store.in_cooldown("b@x.com") is False


def test_non_rate_limit_failure_returns_immediately(env, monkeypatch):
    spawn = SpawnRecorder([(3, "", "boom: launcher exploded", False)])
    _wire(monkeypatch, spawn)
    out = runner.consult(role="plan", brief="anything")
    assert out["failure_class"] == "subprocess_error"
    assert len(spawn.calls) == 1  # no failover on non-429


def test_all_candidates_rate_limited_aggregate(env, monkeypatch):
    spawn = SpawnRecorder([
        (1, _RATE_STDOUT, "", False),
        (1, _RATE_STDOUT, "", False),
    ])
    _wire(monkeypatch, spawn)
    out = runner.consult(role="plan", brief="anything")
    assert out["failure_class"] == "rate_limited"
    assert len(out["attempts"]) == 2
    store = call_state.CallStateStore(config.call_state_path())
    assert store.in_cooldown("a@x.com") and store.in_cooldown("b@x.com")


def test_at_most_one_attempt_per_candidate(env, monkeypatch):
    # N candidates → at most N spawns even when everything 429s.
    spawn = SpawnRecorder([(1, _RATE_STDOUT, "", False)] * 5)
    _wire(monkeypatch, spawn, rows=("a@x.com", "b@x.com"))
    runner.consult(role="plan", brief="anything")
    assert len(spawn.calls) == 2


def test_resume_targets_pinned_account_and_skips_selector(env, monkeypatch):
    store = call_state.CallStateStore(config.call_state_path())
    store.record_result("s-pin", role="diagnose", account="b@x.com",
                        terminal_reason="end_turn", failure_class=None)
    spawn = SpawnRecorder([(0, _ok_stdout(session="s-pin"), "", False)])
    counter = ScrubCounter()
    monkeypatch.setattr(runner, "_spawn", spawn)
    monkeypatch.setattr(scrub, "scan_or_refuse", counter)

    def _no_selector(*a, **kw):
        raise AssertionError("resume must NEVER run the account selector")

    monkeypatch.setattr(accounts, "account_rows", _no_selector)
    out = runner.consult(role="implement", brief="apply the fix",
                         session_id="s-pin", confirmed=True)
    assert out["status"] == "ok"
    assert out["account"] == "b@x.com"
    argv = spawn.calls[0]["argv"]
    assert "b@x.com" in argv
    assert argv[-2:] == ["-r", "s-pin"]
    assert counter.count == 1  # scrub runs on the resume path too


def test_resume_rate_limited_cools_down_no_cross_account(env, monkeypatch):
    store = call_state.CallStateStore(config.call_state_path())
    store.record_result("s-pin", role="diagnose", account="a@x.com",
                        terminal_reason="end_turn", failure_class=None)
    spawn = SpawnRecorder([(1, _RATE_STDOUT, "", False)])
    monkeypatch.setattr(runner, "_spawn", spawn)
    monkeypatch.setattr(
        accounts, "account_rows",
        lambda **kw: (_ for _ in ()).throw(AssertionError("no selector on resume")),
    )
    out = runner.consult(role="implement", brief="x", session_id="s-pin")
    assert out["failure_class"] == "rate_limited"
    assert len(spawn.calls) == 1  # account-pinned: never a cross-account resume
    assert store.in_cooldown("a@x.com")


def test_invalid_model_rejected_before_any_launch(env, monkeypatch):
    spawn = SpawnRecorder([])
    _wire(monkeypatch, spawn)
    with pytest.raises(FableError) as ei:
        runner.consult(role="plan", brief="x", model="claude-fable-5-0")
    assert ei.value.error_class == "invalid_model"
    assert spawn.calls == []


def test_invalid_role_rejected(env, monkeypatch):
    spawn = SpawnRecorder([])
    _wire(monkeypatch, spawn)
    with pytest.raises(FableError) as ei:
        runner.consult(role="orchestrate", brief="x")
    assert ei.value.error_class == "invalid_role"
    assert spawn.calls == []


def test_secret_in_brief_refuses_before_spawn(env, monkeypatch):
    spawn = SpawnRecorder([])
    monkeypatch.setattr(runner, "_spawn", spawn)
    monkeypatch.setattr(
        accounts, "account_rows",
        lambda snapshot=None, cooldowns=None, oauth_mod=None: _rows("a@x.com"),
    )
    planted = "AKIA" + "IOSFODNN7EXAMPLE"
    with pytest.raises(FableError) as ei:
        runner.consult(role="plan", brief=f"key is {planted}")
    assert ei.value.error_class == "secret_refused"
    assert planted not in str(ei.value.payload())
    assert spawn.calls == []


def test_scrub_runs_per_tier_across_separate_calls(env, monkeypatch):
    counter = ScrubCounter()
    # Task F: claude-opus-5/claude-opus-4-8 now resolve their account via
    # the direct-invoke path (_active_account_email), not the selector this
    # test wires up below — stub it so those two iterations don't reach
    # live cswap. claude-fable-5-1 never calls this function, so the stub
    # is inert for that iteration.
    monkeypatch.setattr(runner, "_active_account_email", lambda: "a@x.com")
    for model in ("claude-fable-5-1", "claude-opus-5", "claude-opus-4-8"):
        spawn = SpawnRecorder([(0, _ok_stdout(model=model), "", False)])
        _wire(monkeypatch, spawn, rows=("a@x.com",), scrub_counter=counter)
        out = runner.consult(role="plan", brief="ladder step", model=model)
        assert out["status"] == "ok"
    assert counter.count == 3  # one scrub per tier's spawn


def test_refused_result_blocks_later_resume(env, monkeypatch):
    refused_stdout = json.dumps({
        "session_id": "s-ref",  # the CLI's own claim — must be overridden by
        "is_error": False,      # the caller's minted id, never trusted alone
        "modelUsage": {"claude-fable-5-1": {}},
        "result": "I can't help with that.",
        "num_turns": 1,
    })
    spawn = SpawnRecorder([(0, refused_stdout, "", False)])
    _wire(monkeypatch, spawn, rows=("a@x.com",))
    out = runner.consult(role="plan", brief="x")
    assert out["failure_class"] == "refused"
    minted = out["session_id"]
    assert minted != "s-ref"  # our own mint wins over the CLI's own JSON
    with pytest.raises(FableError) as ei:
        runner.consult(role="plan", brief="x", session_id=minted)
    assert ei.value.error_class == "refused_session"


def test_no_headroom_when_zero_candidates(env, monkeypatch):
    """The ONLY row is active AND fails the 5h/7d gate, so nothing is
    eligible by any route — not the cswap pool, not the active-account
    fallback below. ``is_active`` alone is no longer sufficient to make this
    assertion: an active row that CLEARS the gate is now a candidate via the
    direct path, which is what the maintainer's rule says ("use the ENABLED
    account that has the greatest available fable amount" — it says nothing
    about active). Pinning the empty case on ``is_active`` alone would pass
    for the wrong reason and would silently re-assert the excluded-active
    behavior this fallback exists to remove."""
    spawn = SpawnRecorder([])
    monkeypatch.setattr(runner, "_spawn", spawn)
    monkeypatch.setattr(
        accounts, "account_rows",
        lambda snapshot=None, cooldowns=None, oauth_mod=None: [
            {**_rows("a@x.com")[0], "is_active": True, "gate_ok": False},
        ],
    )
    with pytest.raises(FableError) as ei:
        runner.consult(role="plan", brief="x")
    assert ei.value.error_class == "no_headroom"
    assert spawn.calls == []


def test_active_candidate_launches_through_the_direct_argv(env, monkeypatch):
    """``is_active`` selects the LAUNCH PATH, never eligibility.

    The active account is an ordinary candidate (rule: not
    disabled, 5h avail > 0, 7d avail > 0, greatest Fable avail). What its
    active-ness decides is only how the spawn is built: ``cswap run
    --require-session`` refuses to swap into the login it is already on, so
    that candidate runs through Task F's direct, no-wrapper argv.

    The argv assertion is the substance: no ``cswap`` prefix and no
    ``--require-session``. Asserting only that a spawn happened would pass
    against the cswap-wrapped path, which is exactly the call cswap would
    refuse."""
    spawn = SpawnRecorder([(0, _ok_stdout(), "", False)])
    monkeypatch.setattr(runner, "_spawn", spawn)
    monkeypatch.setattr(
        accounts, "account_rows",
        lambda snapshot=None, cooldowns=None, oauth_mod=None: [
            {**_rows("a@x.com")[0], "is_active": True},
        ],
    )
    out = runner.consult(role="plan", brief="x")
    assert out["status"] == "ok"
    assert out["account"] == "a@x.com"
    assert len(spawn.calls) == 1
    argv = spawn.calls[0]["argv"]
    assert argv[0] == "claude"
    assert "cswap" not in argv
    assert "--require-session" not in argv


def test_active_account_still_subject_to_every_other_exclusion(env, monkeypatch):
    """Removing the is_active exclusion removed ONLY that one. A cooled-down
    or cswap-disabled active account is still refused end to end — otherwise
    the direct path would become a way around the cooldown a 429 just set."""
    for field in ("in_cooldown", "disabled"):
        spawn = SpawnRecorder([])
        monkeypatch.setattr(runner, "_spawn", spawn)
        monkeypatch.setattr(
            accounts, "account_rows",
            lambda snapshot=None, cooldowns=None, oauth_mod=None, _f=field: [
                {**_rows("a@x.com")[0], "is_active": True, _f: True},
            ],
        )
        with pytest.raises(FableError) as ei:
            runner.consult(role="plan", brief="x")
        assert ei.value.error_class == "no_headroom", field
        assert spawn.calls == [], field


def test_empty_brief_rejected(env, monkeypatch):
    spawn = SpawnRecorder([])
    _wire(monkeypatch, spawn)
    with pytest.raises(FableError) as ei:
        runner.consult(role="plan", brief="   ")
    assert ei.value.error_class == "invalid_brief"
    assert spawn.calls == []


def test_armed_rate_limited_never_fails_over(env, monkeypatch):
    """ARMED Stop (an earlier measurement / the forbidden-behavior list): a 429 during a
    confirmed=True call may land AFTER writing began — it is a Stop for
    that call, never an auto-retry on another account against a
    possibly-dirty tree. Exactly ONE spawn; the second candidate is never
    touched; the payload says to inspect the tree."""
    spawn = SpawnRecorder([
        (1, _RATE_STDOUT, "", False),
        (0, _ok_stdout(session="s-should-never-run"), "", False),
    ])
    counter = ScrubCounter()
    _wire(monkeypatch, spawn, scrub_counter=counter)
    out = runner.consult(role="implement", brief="apply the fix", confirmed=True)
    assert out["failure_class"] == "rate_limited"
    assert out.get("armed_stop") is True
    assert ("git " + "status") in out["message"]
    assert len(spawn.calls) == 1  # the forbidden failover never happened
    store = call_state.CallStateStore(config.call_state_path())
    assert store.in_cooldown("a@x.com") is True


def test_armed_resume_error_carries_armed_stop(env, monkeypatch):
    """Disclosure symmetry (closing review): arming by RESUME is the
    primary armed path — a timeout there gets the same armed-Stop
    disclosure the fresh path already carried. No retry either way."""
    store = call_state.CallStateStore(config.call_state_path())
    store.record_result("s-armed", role="implement", account="a@x.com",
                        terminal_reason="end_turn", failure_class=None)
    spawn = SpawnRecorder([(-1, "", "", True)])  # timeout
    _wire(monkeypatch, spawn)
    out = runner.consult(role="implement", brief="apply it",
                         session_id="s-armed", confirmed=True)
    assert out["failure_class"] == "timeout"
    assert out.get("armed_stop") is True
    assert ("git " + "status") in out["message"]
    assert len(spawn.calls) == 1


# ── Task F: Opus-fallback direct-invoke account routing ─────────────────


def test_direct_invoke_path_for_opus_tiers_skips_selector(env, monkeypatch):
    """model=claude-opus-5/claude-opus-4-8 runs DIRECT — no
    accounts.account_rows() call at all — against whatever account cswap
    reports as the active default login. Still passes the scrub chokepoint
    and the write guard exactly like every other path."""
    spawn = SpawnRecorder([(0, _ok_stdout(model="claude-opus-5"), "", False)])
    counter = ScrubCounter()
    monkeypatch.setattr(runner, "_spawn", spawn)
    monkeypatch.setattr(scrub, "scan_or_refuse", counter)
    monkeypatch.setattr(runner, "_active_account_email", lambda: "active@x.com")

    def _no_selector(*a, **kw):
        raise AssertionError("Opus direct-invoke must never run the account selector")

    monkeypatch.setattr(accounts, "account_rows", _no_selector)
    out = runner.consult(role="implement", brief="apply the fix",
                         model="claude-opus-5", confirmed=True)
    assert out["status"] == "ok"
    assert out["account"] == "active@x.com"
    assert counter.count == 1  # scrub chokepoint ran
    argv = spawn.calls[0]["argv"]
    assert argv[0] == "claude"
    assert "cswap" not in argv
    assert "run" not in argv
    assert "--require-session" not in argv
    for tool in ("Edit", "Write", "NotebookEdit", "Bash"):
        assert tool in argv  # write guard: confirmed=True armed the direct path too
    assert "--session-id" in argv
    minted = argv[argv.index("--session-id") + 1]
    assert minted != "s-ok"
    assert out["session_id"] == minted
    store = call_state.CallStateStore(config.call_state_path())
    assert store.get(minted)["account"] == "active@x.com"


def test_direct_invoke_rate_limited_sets_cooldown_and_armed_stop(env, monkeypatch):
    """The active account is NOT exempt from 429 cooldowns or the armed-Stop
    disclosure (Task F, Explicitly Forbidden) — same treatment as any
    selector-path account, just reached via the direct-invoke mechanism,
    and there is nothing to fail over to (no candidate list on this path)."""
    spawn = SpawnRecorder([(1, _RATE_STDOUT, "", False)])
    monkeypatch.setattr(runner, "_spawn", spawn)
    monkeypatch.setattr(runner, "_active_account_email", lambda: "active@x.com")

    def _no_selector(*a, **kw):
        raise AssertionError("Opus direct-invoke must never run the account selector")

    monkeypatch.setattr(accounts, "account_rows", _no_selector)
    out = runner.consult(role="implement", brief="apply the fix",
                         model="claude-opus-4-8", confirmed=True)
    assert out["failure_class"] == "rate_limited"
    assert out.get("armed_stop") is True
    assert len(spawn.calls) == 1
    store = call_state.CallStateStore(config.call_state_path())
    assert store.in_cooldown("active@x.com") is True


def test_resume_with_opus_model_stays_direct_no_cswap_wrapper(env, monkeypatch):
    """A resumed session_id pins the ACCOUNT (call_state rule d), but the
    caller's model param decides the launch mechanism for THIS call
    (Task F) — resuming under an opus tier must stay direct-invoke, never
    wrap the pinned (already-active) account in `cswap run --require-
    session`, which would refuse it outright (Fixed Inputs: --require-
    session raises SessionError for the CURRENT default-login account).

    The active account is mocked to MATCH the pinned row — this is the
    positive control for the account-pinning check added below (adversarial
    findings, SIGNIFICANT x2): a matching active account must still resume
    normally, not be caught by the new mismatch refusal."""
    store = call_state.CallStateStore(config.call_state_path())
    store.record_result("s-opus", role="implement", account="active@x.com",
                        terminal_reason="end_turn", failure_class=None)
    spawn = SpawnRecorder([
        (0, _ok_stdout(session="s-opus", model="claude-opus-5"), "", False),
    ])
    monkeypatch.setattr(runner, "_spawn", spawn)
    monkeypatch.setattr(runner, "_active_account_email", lambda: "active@x.com")

    def _no_selector(*a, **kw):
        raise AssertionError("resume must never run the account selector")

    monkeypatch.setattr(accounts, "account_rows", _no_selector)
    out = runner.consult(role="implement", brief="continue",
                         session_id="s-opus", model="claude-opus-5",
                         confirmed=True)
    assert out["status"] == "ok"
    assert out["account"] == "active@x.com"
    argv = spawn.calls[0]["argv"]
    assert argv[0] == "claude"
    assert "cswap" not in argv
    assert "--require-session" not in argv
    assert argv[-2:] == ["-r", "s-opus"]


def test_resume_with_opus_model_refuses_on_account_mismatch(env, monkeypatch):
    """SIGNIFICANT finding (x2): call_state.py's own documented rule (d) —
    'the returned row carries the pinned account — the ONLY account a
    resume may target' — was never enforced on the direct-invoke path.
    build_direct_argv() takes no account parameter and never consults
    _active_account_email(), so the subprocess would run against whatever
    is the ambient default login RIGHT NOW, silently ignoring the pin.
    This models a session that was itself minted direct-invoke (pinned to
    whichever account was active AT MINT, per the fresh-mint branch): its
    transcript lives in the ONE shared ~/.claude/projects/ store rather
    than an isolated per-account profile, so a mismatched resume would NOT
    fail — it would reach that same transcript under the WRONG account's
    credentials (verified via claude_swap.session.session_dir_for() +
    SessionManager.run()'s --require-session refusal, which is what
    forces every cswap-wrapped mint into an isolated profile instead).
    The session is pinned to 'active@x.com'; the live active account has
    since become 'someone-else@x.com' — the resume must refuse BEFORE any
    spawn, never silently proceed under the wrong account."""
    store = call_state.CallStateStore(config.call_state_path())
    store.record_result("s-opus", role="implement", account="active@x.com",
                        terminal_reason="end_turn", failure_class=None)
    spawn = SpawnRecorder([
        (0, _ok_stdout(session="s-opus", model="claude-opus-5"), "", False),
    ])
    monkeypatch.setattr(runner, "_spawn", spawn)
    monkeypatch.setattr(runner, "_active_account_email", lambda: "someone-else@x.com")

    def _no_selector(*a, **kw):
        raise AssertionError("resume must never run the account selector")

    monkeypatch.setattr(accounts, "account_rows", _no_selector)
    with pytest.raises(FableError) as ei:
        runner.consult(role="implement", brief="continue",
                       session_id="s-opus", model="claude-opus-5",
                       confirmed=True)
    assert ei.value.error_class == "account_mismatch"
    assert "active@x.com" in ei.value.message
    assert "someone-else@x.com" in ei.value.message
    assert spawn.calls == []  # refused BEFORE any spawn — no cost incurred
    # the mismatch is never recorded as a terminal result against either
    # account (nothing to record — the call never reached _attempt)
    assert store.get("s-opus")["terminal_reason"] == "end_turn"


def test_fable_resume_of_a_non_active_account_stays_cswap_wrapped(env, monkeypatch):
    """A fable-5-1 session pinned to an account that is NOT the current
    login still resumes through the cswap wrapper, which enforces the pin
    structurally. The account_mismatch refusal above stays scoped to the
    Opus direct tiers — a pin cswap CAN honor is never refused."""
    store = call_state.CallStateStore(config.call_state_path())
    store.record_result("s-pin", role="diagnose", account="b@x.com",
                        terminal_reason="end_turn", failure_class=None)
    spawn = SpawnRecorder([(0, _ok_stdout(session="s-pin"), "", False)])
    monkeypatch.setattr(runner, "_spawn", spawn)
    monkeypatch.setattr(runner, "_active_account_email", lambda: "someone-else@x.com")
    out = runner.consult(role="diagnose", brief="continue", session_id="s-pin")
    assert out["status"] == "ok"
    assert out["account"] == "b@x.com"
    argv = spawn.calls[0]["argv"]
    assert argv[0] == "cswap"
    assert "--require-session" in argv


def test_fable_resume_of_the_active_account_launches_direct(env, monkeypatch):
    """The counterpart, and the reason the launch path can no longer be
    decided by model tier alone.

    Now that `is_active` is not a selector exclusion, a fable-5-1 session
    can be MINTED on the active account — through the direct argv, because
    that is the only way cswap will launch it. Resuming it wrapped would hit
    `--require-session`'s refusal for exactly the same reason and break
    every such resume. The pinned account being the live login is what
    selects the direct path, not the model."""
    store = call_state.CallStateStore(config.call_state_path())
    store.record_result("s-act", role="diagnose", account="a@x.com",
                        terminal_reason="end_turn", failure_class=None)
    spawn = SpawnRecorder([(0, _ok_stdout(session="s-act"), "", False)])
    monkeypatch.setattr(runner, "_spawn", spawn)
    monkeypatch.setattr(runner, "_active_account_email", lambda: "a@x.com")
    out = runner.consult(role="diagnose", brief="continue", session_id="s-act")
    assert out["status"] == "ok"
    assert out["account"] == "a@x.com"
    argv = spawn.calls[0]["argv"]
    assert argv[0] == "claude"
    assert "cswap" not in argv
    assert "--require-session" not in argv
