"""Account selector — allowlist intersection, exclusions, the 5h/7d gate,
and Fable-headroom ranking.

All cswap access is mocked: a fake oauth module mimicking the documented
relevant_windows contract and simple namespace snapshots. ZERO live cswap.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SERVICES = Path(__file__).resolve().parent.parent / "services"
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from node_fable import accounts, config
from node_fable.errors import FableError


class FakeOauth:
    """Mimics cswap oauth.relevant_windows: 5h + 7d + scoped-by-model."""

    @staticmethod
    def relevant_windows(usage, models=()):
        if not isinstance(usage, dict):
            return []
        wins = []
        for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
            w = usage.get(key)
            if isinstance(w, dict) and isinstance(w.get("pct"), (int, float)):
                wins.append((label, float(w["pct"]), w.get("resets_at")))
        wanted = {m.lower() for m in models}
        for s in usage.get("scoped") or []:
            if isinstance(s, dict) and isinstance(s.get("pct"), (int, float)):
                name = str(s.get("display", ""))
                if name.lower() in wanted or "all" in wanted:
                    wins.append((name, float(s["pct"]), s.get("resets_at")))
        return wins


def _acct(email, *, active=False, usage=None, age_s=12.0, number="1", disabled=False):
    return SimpleNamespace(
        number=number,
        email=email,
        is_active=active,
        kind="oauth",
        switchable=True,
        usage=SimpleNamespace(last_good=usage, age_s=age_s if usage else None),
        disabled=disabled,
    )


def _snapshot(*accts):
    return SimpleNamespace(active_number=None, accounts=tuple(accts))


def _usage(five=10.0, seven=20.0, fable=None):
    u = {
        "five_hour": {"pct": five, "resets_at": "2026-09-07T10:00:00Z"},
        "seven_day": {"pct": seven, "resets_at": "2026-09-12T10:00:00Z"},
    }
    if fable is not None:
        u["scoped"] = [{"display": "Fable", "pct": fable, "resets_at": None}]
    return u


# ── fable_gate_and_rank: the pure per-account gate + rank expression ────────
#
# Replaces the retired fable_headroom_excl_7d(), which computed
# 100 - max(pct) over the non-7d windows (min(5h, Fable) headroom) and let
# 7d neither gate nor rank anything. The maintainer's rule makes
# BOTH 5h and 7d hard gates and ranks on Fable headroom alone — see
# accounts.py's module docstring for the verbatim rule and the rationale
# for the change of direction on 7d.

def test_gate_and_rank_all_windows_present_gate_passes():
    r = accounts.fable_gate_and_rank(_usage(five=10.0, seven=20.0, fable=30.0), FakeOauth)
    assert r["five_h_avail"] == 90.0
    assert r["seven_d_avail"] == 80.0
    assert r["fable_headroom"] == 70.0
    assert r["gate_ok"] is True


def test_gate_and_rank_7d_exhausted_fails_gate():
    # This is the maintainer's exact correction: previously 7d was excluded
    # from eligibility entirely and could never sink a candidate. Now a
    # fully-consumed 7d window fails the gate outright, regardless of how
    # much 5h or Fable headroom remains.
    r = accounts.fable_gate_and_rank(_usage(five=10.0, seven=100.0, fable=30.0), FakeOauth)
    assert r["seven_d_avail"] == 0.0
    assert r["gate_ok"] is False


def test_gate_and_rank_5h_exhausted_fails_gate():
    r = accounts.fable_gate_and_rank(_usage(five=100.0, seven=10.0, fable=30.0), FakeOauth)
    assert r["five_h_avail"] == 0.0
    assert r["gate_ok"] is False


def test_gate_and_rank_missing_fable_window_still_gates_but_no_rank():
    # 5h/7d both measured and both clear the gate; Fable itself was never
    # reported (no "scoped" entry at all). Fable is rank-only, so its
    # absence must NOT fail the gate — only leave fable_headroom disclosed
    # as null rather than defaulted to a number.
    r = accounts.fable_gate_and_rank(_usage(five=10.0, seven=20.0), FakeOauth)
    assert r["gate_ok"] is True
    assert r["fable_headroom"] is None


def test_gate_and_rank_missing_usage_fails_gate():
    # No usage at all → no 5h/7d reading → "we could not measure it" must
    # never be voted as "it passed."
    r = accounts.fable_gate_and_rank(None, FakeOauth)
    assert r["five_h_avail"] is None
    assert r["seven_d_avail"] is None
    assert r["fable_headroom"] is None
    assert r["gate_ok"] is False


# ── allowlist / row construction ─────────────────────────────────────────────

def test_empty_allowlist_fails_closed(monkeypatch):
    monkeypatch.delenv(config.ENV_ACCOUNTS, raising=False)
    with pytest.raises(FableError) as ei:
        config.accounts_allowlist()
    assert ei.value.error_class == "missing_env"
    assert config.ENV_ACCOUNTS in ei.value.message


def test_allowlist_intersects_live_accounts(monkeypatch):
    monkeypatch.setenv(config.ENV_ACCOUNTS, "a@x.com,ghost@x.com")
    snap = _snapshot(_acct("a@x.com", usage=_usage()), _acct("b@x.com", usage=_usage()))
    rows = accounts.account_rows(snapshot=snap, cooldowns={}, oauth_mod=FakeOauth)
    emails = [r["email"] for r in rows]
    assert emails == ["a@x.com", "ghost@x.com"]  # allowlist order, b excluded
    ghost = rows[1]
    assert ghost.get("not_in_cswap") is True
    assert ghost["gate_ok"] is False
    # ghost never becomes a candidate
    assert [c["email"] for c in accounts.select(rows)] == ["a@x.com"]


def test_is_active_is_not_an_exclusion_and_ranks_like_any_other(monkeypatch):
    """The rule has four terms — not disabled, 5h avail > 0, 7d avail > 0,
    greatest remaining Fable avail. `is_active` is not one of them.

    It WAS an exclusion here, on the reasoning that `cswap run
    --require-session` refuses the current default login. That is a fact
    about one launch mechanism, not about eligibility, and encoding it as an
    exclusion made an eligible account invisible to both the selector and
    `fable_headroom`'s `selector_would_pick`. The active account now
    competes on Fable headroom like any other and the caller launches it
    through the direct, no-wrapper argv.

    Both accounts here clear the gate, and the ACTIVE one has the better
    Fable headroom, so it must come FIRST. Asserting only membership would
    pass against an implementation that kept the active account but sorted
    it last, which is the exclusion in a weaker form.
    """
    monkeypatch.setenv(config.ENV_ACCOUNTS, "a@x.com,b@x.com")
    snap = _snapshot(
        _acct("a@x.com", active=True, usage=_usage(five=0.0, seven=0.0, fable=10.0)),
        _acct("b@x.com", usage=_usage(five=50.0, fable=60.0)),
    )
    rows = accounts.account_rows(snapshot=snap, cooldowns={}, oauth_mod=FakeOauth)
    assert [c["email"] for c in accounts.select(rows)] == ["a@x.com", "b@x.com"]
    assert rows[0]["is_active"] is True  # still disclosed, just not excluding


def test_active_account_still_loses_to_greater_fable_headroom(monkeypatch):
    """The mirror of the test above: dropping the exclusion must not promote
    the active account either. Same two rows with the Fable headrooms
    swapped — the non-active account now wins on the fourth term, and rank
    is decided by that term alone."""
    monkeypatch.setenv(config.ENV_ACCOUNTS, "a@x.com,b@x.com")
    snap = _snapshot(
        _acct("a@x.com", active=True, usage=_usage(five=0.0, seven=0.0, fable=90.0)),
        _acct("b@x.com", usage=_usage(five=50.0, fable=5.0)),
    )
    rows = accounts.account_rows(snapshot=snap, cooldowns={}, oauth_mod=FakeOauth)
    assert [c["email"] for c in accounts.select(rows)] == ["b@x.com", "a@x.com"]


def test_active_account_is_still_subject_to_every_real_exclusion(monkeypatch):
    """Dropping `is_active` drops ONLY `is_active`. A disabled or
    cooled-down active account stays out, or the change would become a way
    around the cooldown a 429 just set."""
    import time
    monkeypatch.setenv(config.ENV_ACCOUNTS, "a@x.com")
    disabled = _snapshot(_acct("a@x.com", active=True, usage=_usage(), disabled=True))
    rows = accounts.account_rows(snapshot=disabled, cooldowns={}, oauth_mod=FakeOauth)
    assert accounts.select(rows) == []

    live = _snapshot(_acct("a@x.com", active=True, usage=_usage()))
    rows = accounts.account_rows(
        snapshot=live, cooldowns={"a@x.com": time.time() + 600}, oauth_mod=FakeOauth
    )
    assert accounts.select(rows) == []

    gated = _snapshot(_acct("a@x.com", active=True, usage=_usage(seven=100.0)))
    rows = accounts.account_rows(snapshot=gated, cooldowns={}, oauth_mod=FakeOauth)
    assert rows[0]["gate_ok"] is False
    assert accounts.select(rows) == []


def test_cooldown_excluded(monkeypatch):
    monkeypatch.setenv(config.ENV_ACCOUNTS, "a@x.com,b@x.com")
    snap = _snapshot(
        _acct("a@x.com", usage=_usage(five=0.0)),
        _acct("b@x.com", usage=_usage(five=50.0)),
    )
    import time
    rows = accounts.account_rows(
        snapshot=snap, cooldowns={"a@x.com": time.time() + 600}, oauth_mod=FakeOauth
    )
    assert [c["email"] for c in accounts.select(rows)] == ["b@x.com"]
    a_row = next(r for r in rows if r["email"] == "a@x.com")
    assert a_row["in_cooldown"] is True


def test_disabled_excluded_from_select(monkeypatch):
    # Pin the wall clock a review lens observed this test fail intermittently
    # (2 of 14 full-suite runs) with no reproduction on either a 300-run loop
    # of this file alone or a 300-run loop of the full suite (0/600). Reading
    # _row()/account_rows() shows `time.time()` reaches this test only through
    # `cooldown_until is not None and cooldown_until > now` — and cooldowns={}
    # here means `cooldown_until` is always None, short-circuiting before
    # `now` is ever compared. So the pin changes nothing today; it converts
    # "wall clock cannot affect this test" from an implicit fact about the
    # current source into an explicit, checked one that a future edit
    # coupling disabled-exclusion to elapsed time would immediately break.
    monkeypatch.setattr(accounts.time, "time", lambda: 1_800_000_000.0)
    monkeypatch.setenv(config.ENV_ACCOUNTS, "a@x.com,b@x.com")
    snap = _snapshot(
        _acct("a@x.com", usage=_usage(five=0.0), disabled=True),
        _acct("b@x.com", usage=_usage(five=50.0)),
    )
    rows = accounts.account_rows(snapshot=snap, cooldowns={}, oauth_mod=FakeOauth)
    assert [c["email"] for c in accounts.select(rows)] == ["b@x.com"]
    disabled_row = next(r for r in rows if r["email"] == "a@x.com")
    assert disabled_row["in_cooldown"] is False
    assert disabled_row["cooldown_until"] is None


def test_disabled_disclosed_in_account_rows(monkeypatch):
    # a disabled account clears the gate with the BEST possible headroom on
    # every window — it would rank first (only candidate, in fact) if
    # disclosure alone governed selection. It must still surface in
    # account_rows() carrying disabled: true (disclosed, not hidden), while
    # remaining absent from select()'s candidate list.
    #
    # Clock pinned for the same reason as test_disabled_excluded_from_select
    # above (unreproduced intermittent-failure report; see that test's
    # comment) — a no-op against current logic, made explicit rather than
    # left to chance.
    monkeypatch.setattr(accounts.time, "time", lambda: 1_800_000_000.0)
    monkeypatch.setenv(config.ENV_ACCOUNTS, "a@x.com")
    snap = _snapshot(
        _acct("a@x.com", usage=_usage(five=0.0, seven=0.0, fable=0.0), disabled=True)
    )
    rows = accounts.account_rows(snapshot=snap, cooldowns={}, oauth_mod=FakeOauth)
    assert len(rows) == 1
    assert rows[0]["disabled"] is True
    assert rows[0]["gate_ok"] is True  # gate itself passes; disabled still excludes
    assert rows[0]["fable_headroom"] == 100.0
    assert rows[0]["in_cooldown"] is False
    assert rows[0]["cooldown_until"] is None
    assert accounts.select(rows) == []


def test_not_in_cswap_row_carries_disabled_field(monkeypatch):
    # Row shape stays uniform: a not-in-cswap synthetic row carries the same
    # disabled key as a real account's row (always False — cswap never
    # reported an opinion on an account it doesn't know about), and its
    # gate_ok is False (nothing was measured).
    monkeypatch.setenv(config.ENV_ACCOUNTS, "ghost@x.com")
    snap = _snapshot()
    rows = accounts.account_rows(snapshot=snap, cooldowns={}, oauth_mod=FakeOauth)
    assert rows[0]["not_in_cswap"] is True
    assert rows[0]["disabled"] is False
    assert rows[0]["gate_ok"] is False


def test_5h_gate_excludes_from_select(monkeypatch):
    monkeypatch.setenv(config.ENV_ACCOUNTS, "a@x.com,b@x.com")
    snap = _snapshot(
        # a: 5h pct=100 → 0 avail, fails the gate even though 7d/Fable are
        # wide open.
        _acct("a@x.com", usage=_usage(five=100.0, seven=5.0, fable=5.0)),
        _acct("b@x.com", usage=_usage(five=50.0, seven=5.0, fable=5.0)),
    )
    rows = accounts.account_rows(snapshot=snap, cooldowns={}, oauth_mod=FakeOauth)
    assert [c["email"] for c in accounts.select(rows)] == ["b@x.com"]
    a_row = next(r for r in rows if r["email"] == "a@x.com")
    assert a_row["gate_ok"] is False
    assert a_row["five_h_avail"] == 0.0


def test_7d_gate_now_excludes_a_candidate_previously_only_deprioritized(monkeypatch):
    # The behavioral core of the maintainer's correction: under the retired
    # policy, 7d was excluded from eligibility entirely and could not sink
    # a candidate — an account with 7d fully consumed but 5h/Fable open
    # would still have been selected. Now it is excluded outright.
    monkeypatch.setenv(config.ENV_ACCOUNTS, "a@x.com,b@x.com")
    snap = _snapshot(
        _acct("a@x.com", usage=_usage(five=5.0, seven=100.0, fable=5.0)),
        _acct("b@x.com", usage=_usage(five=50.0, seven=50.0, fable=50.0)),
    )
    rows = accounts.account_rows(snapshot=snap, cooldowns={}, oauth_mod=FakeOauth)
    assert [c["email"] for c in accounts.select(rows)] == ["b@x.com"]
    a_row = next(r for r in rows if r["email"] == "a@x.com")
    assert a_row["gate_ok"] is False
    assert a_row["seven_d_avail"] == 0.0


def test_ranking_by_fable_headroom_not_min_with_5h(monkeypatch):
    # Discriminates the old rank (min(5h, Fable) headroom, descending) from
    # the new rank (Fable headroom alone, descending) — live data on this
    # node happened not to, so this fixture is constructed to disagree.
    #   x: 5h avail=20 (low), Fable avail=90 (high) → old rank min=20, new
    #      rank=90.
    #   y: 5h avail=95 (high), Fable avail=50 (mid)  → old rank min=50, new
    #      rank=50.
    # Old policy ranks y(50) above x(20). New policy ranks x(90) above
    # y(50) — the order flips. Both clear the 5h/7d gate.
    monkeypatch.setenv(config.ENV_ACCOUNTS, "x@x.com,y@x.com")
    snap = _snapshot(
        _acct("x@x.com", usage=_usage(five=80.0, seven=10.0, fable=10.0)),
        _acct("y@x.com", usage=_usage(five=5.0, seven=10.0, fable=50.0)),
    )
    rows = accounts.account_rows(snapshot=snap, cooldowns={}, oauth_mod=FakeOauth)
    ordered = [c["email"] for c in accounts.select(rows)]
    assert ordered == ["x@x.com", "y@x.com"]
    x_row = next(r for r in rows if r["email"] == "x@x.com")
    y_row = next(r for r in rows if r["email"] == "y@x.com")
    assert x_row["fable_headroom"] == 90.0
    assert y_row["fable_headroom"] == 50.0


def test_missing_usage_fails_gate_excluded_not_ranked_last(monkeypatch):
    # Superseded policy: an account with no usable usage data ranked LAST
    # but stayed a candidate. New policy: the gate needs BOTH 5h and 7d
    # availability measured and positive — "we could not measure it" is
    # never "it passed" — so an account with no usage data at all fails the
    # gate and is excluded outright, not merely deprioritized.
    monkeypatch.setenv(config.ENV_ACCOUNTS, "known@x.com,nousage@x.com")
    snap = _snapshot(
        _acct("known@x.com", usage=_usage(five=50.0, seven=50.0, fable=50.0)),
        _acct("nousage@x.com", usage=None),
    )
    rows = accounts.account_rows(snapshot=snap, cooldowns={}, oauth_mod=FakeOauth)
    ordered = [c["email"] for c in accounts.select(rows)]
    assert ordered == ["known@x.com"]
    nousage = next(r for r in rows if r["email"] == "nousage@x.com")
    assert nousage["gate_ok"] is False
    assert nousage["fable_headroom"] is None
    assert nousage["five_h_avail"] is None
    assert nousage["seven_d_avail"] is None
    assert nousage["usage_age_s"] is None


def test_missing_fable_window_gate_passes_but_ranks_last(monkeypatch):
    # 5h and 7d are both measured and clear the gate; Fable itself has no
    # reading yet (never consulted through this account, so cswap reports
    # no scoped "Fable" window). Fable is rank-only, so the account stays a
    # candidate — ranked LAST among gate-passers, disclosed via
    # fable_headroom: null rather than defaulted to a number.
    monkeypatch.setenv(config.ENV_ACCOUNTS, "ranked@x.com,unranked@x.com")
    snap = _snapshot(
        _acct("ranked@x.com", usage=_usage(five=90.0, seven=90.0, fable=90.0)),
        # No "fable" kwarg → no scoped Fable window in the usage dict at
        # all → relevant_windows() never reports a "Fable" label.
        _acct("unranked@x.com", usage=_usage(five=10.0, seven=10.0)),
    )
    rows = accounts.account_rows(snapshot=snap, cooldowns={}, oauth_mod=FakeOauth)
    ordered = [c["email"] for c in accounts.select(rows)]
    assert ordered == ["ranked@x.com", "unranked@x.com"]
    unranked = next(r for r in rows if r["email"] == "unranked@x.com")
    assert unranked["gate_ok"] is True
    assert unranked["fable_headroom"] is None


def test_no_candidates_raises_no_headroom(monkeypatch):
    # The sole account is BOTH active and cswap-disabled. `disabled` is what
    # makes it ineligible; `active` alone no longer removes anything, so
    # pinning the empty case on it would pass for the wrong reason and
    # silently re-assert the exclusion that was removed.
    monkeypatch.setenv(config.ENV_ACCOUNTS, "a@x.com")
    snap = _snapshot(_acct("a@x.com", active=True, usage=_usage(), disabled=True))
    rows = accounts.account_rows(snapshot=snap, cooldowns={}, oauth_mod=FakeOauth)
    with pytest.raises(FableError) as ei:
        accounts.require_candidates(rows)
    assert ei.value.error_class == "no_headroom"
    assert ei.value.extra.get("failure_class") == "no_headroom"


def test_usage_age_disclosed(monkeypatch):
    monkeypatch.setenv(config.ENV_ACCOUNTS, "a@x.com")
    snap = _snapshot(_acct("a@x.com", usage=_usage(), age_s=777.5))
    rows = accounts.account_rows(snapshot=snap, cooldowns={}, oauth_mod=FakeOauth)
    assert rows[0]["usage_age_s"] == 777.5


def test_select_treats_missing_gate_ok_key_as_failed_gate():
    # Fail-closed on a row shape that predates the gate (e.g. a stale
    # caller-supplied fixture with no "gate_ok" key at all): the missing
    # key must NOT default to a silent pass.
    row = {
        "email": "a@x.com",
        "is_active": False,
        "in_cooldown": False,
        "fable_headroom": 90.0,
        # no "gate_ok", no "disabled", no "not_in_cswap"
    }
    assert accounts.select([row]) == []
