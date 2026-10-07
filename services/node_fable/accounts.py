"""Account selection for Fable consultations.

Candidate = allowlist (NODE_FABLE_ACCOUNTS) ∩ live cswap accounts, minus
accounts inside a rate-limit cooldown (call-state store), minus accounts
cswap itself has marked ``disabled`` (held out of auto-rotation — still a
valid explicit target on cswap's own side, but never an auto-selected one
here), minus any account failing the eligibility GATE below. Ranked by
Fable-window headroom alone, highest first, among the accounts the gate
lets through.

Eligibility gate (if 5h avail > 0 and 7d avail > 0, use the enabled account
with the greatest available Fable amount): BOTH the 5-hour and the 7-day window
are GATES — an account with either window exhausted is not a candidate at
all — and the RANK is the per-model Fable window's own headroom, not the
minimum of it and 5h. This supersedes the module's former 7d-excluding
policy: 7d used to be left out of eligibility entirely, on the theory that
the slow axis wastes proactive capacity by holding an account out early.
The current rule overrides that reasoning — 7d is now a hard
gate like 5h, not an ignored axis. cswap percentages are UTILIZATION
(% used); headroom ("available") is 100 - window pct.

**``is_active`` is NOT an exclusion, and this module previously had it
backwards.** The rule's four terms are: enabled, 5h avail > 0, 7d avail > 0,
greatest remaining Fable avail. Whether an account happens to be the current
default login is not among them. It was excluded here because ``cswap run
--require-session`` REFUSES the current default login (cswap raises
SessionError before anything launches) — but that is a fact about ONE
execution mechanism, not about eligibility, and encoding it as an exclusion
made an eligible account invisible to the selector and to
``fable_headroom``'s ``selector_would_pick``. The active account ranks in
the ordering like any other; the caller runs it through the direct
(no-wrapper) argv instead of the cswap wrapper. ``is_active`` stays on every
row purely as disclosure, so a reader can see WHICH execution path a pick
implies. (Maintainer: "you are supposed to use the account whose
5h avail >0 and 7d avail>0 and not disabled and most remaining fable avail
and headroom is supposed to reflect this as well".)

A window ABSENT from cswap's report (no reading at all for that label — a
completely missing/stale usage snapshot, or cswap simply never returned
that particular label) is DISCLOSED, never silently voted as passing or
failing by default substitution. The two missing-input cases resolve in
OPPOSITE directions, because they sit on opposite sides of the gate/rank
split:
  - A missing 5h or 7d reading FAILS the gate. "We could not measure it" is
    never "it passed" — an unmeasured window is exactly the case the gate
    exists to keep out, since it is exactly the case nothing here can rule
    out an immediate 429 on.
  - A missing Fable reading does NOT fail the gate (Fable is rank-only, not
    a gate input) — an account that clears 5h/7d but has no Fable reading
    yet stays a candidate, ranked LAST among gate-passers, disclosed via
    ``fable_headroom: null`` rather than silently sorted as though it were
    zero, infinite, or some other stand-in value.

A disabled account follows the SAME disclosure principle but the opposite
selection outcome: its row always carries ``disabled: true`` rather than
being silently indistinguishable from an enabled one, but it is excluded
from candidacy outright (like ``in_cooldown``/a failed gate) rather than
merely ranked last.
"""

from __future__ import annotations

import time
from typing import Any

from node_fable import config, cswap_api
from node_fable.errors import FableError


def _window_pct(windows: list[tuple], label: str) -> float | None:
    """The pct_used of ONE labeled window out of a relevant_windows() triple
    list, or None when that label was not reported at all (missing input,
    never a silent 0/100 stand-in)."""
    for lbl, pct, _resets in windows:
        if lbl == label:
            return float(pct)
    return None


def fable_gate_and_rank(usage: dict | None, oauth_mod=None) -> dict:
    """The 5h/7d/Fable window headrooms plus the gate verdict, computed from
    an already-fetched usage dict.

    Gate: ``five_h_avail is not None and five_h_avail > 0`` AND the same for
    ``seven_d_avail`` — both windows must be MEASURED and both must show
    headroom, per the maintainer's rule (see module docstring). A window
    absent from the usage snapshot fails the gate; it never passes it by
    default.

    Rank: ``fable_headroom`` — the Fable-scoped window's own headroom,
    independent of 5h/7d. None when the Fable window was not reported (does
    NOT affect ``gate_ok``; see module docstring's missing-Fable case).

    Returns: {"five_h_avail", "seven_d_avail", "fable_headroom", "gate_ok"}.
    """
    if oauth_mod is None:
        oauth_mod = cswap_api.oauth_module()
    windows = oauth_mod.relevant_windows(usage, ["Fable"])
    five_h_pct = _window_pct(windows, "5h")
    seven_d_pct = _window_pct(windows, "7d")
    fable_pct = _window_pct(windows, "Fable")
    five_h_avail = None if five_h_pct is None else 100.0 - five_h_pct
    seven_d_avail = None if seven_d_pct is None else 100.0 - seven_d_pct
    fable_headroom = None if fable_pct is None else 100.0 - fable_pct
    gate_ok = (
        five_h_avail is not None and five_h_avail > 0
        and seven_d_avail is not None and seven_d_avail > 0
    )
    return {
        "five_h_avail": five_h_avail,
        "seven_d_avail": seven_d_avail,
        "fable_headroom": fable_headroom,
        "gate_ok": gate_ok,
    }


def _row(snap: Any, oauth_mod, cooldown_until: float | None, now: float) -> dict:
    usage = snap.usage.last_good if snap.usage is not None else None
    age_s = snap.usage.age_s if snap.usage is not None else None
    windows = oauth_mod.relevant_windows(usage, ["Fable"])
    gr = fable_gate_and_rank(usage, oauth_mod)
    return {
        "email": snap.email,
        "number": snap.number,
        "is_active": bool(snap.is_active),
        "windows": [
            {"label": label, "pct_used": pct, "resets_at": resets_at}
            for label, pct, resets_at in windows
        ],
        "five_h_avail": gr["five_h_avail"],
        "seven_d_avail": gr["seven_d_avail"],
        "fable_headroom": gr["fable_headroom"],
        "gate_ok": gr["gate_ok"],
        "usage_age_s": age_s,
        "in_cooldown": bool(cooldown_until is not None and cooldown_until > now),
        "cooldown_until": cooldown_until,
        "disabled": bool(snap.disabled),
    }


def account_rows(snapshot=None, cooldowns: dict[str, float] | None = None, oauth_mod=None) -> list[dict]:
    """Per-account disclosure rows for every allowlisted live account.

    The allowlist is required (fail closed when empty). Rows include active
    and cooled-down accounts — disclosure, not filtering; ``select`` applies
    the exclusions.
    """
    allowlist = config.accounts_allowlist()
    if snapshot is None:
        snapshot = cswap_api.accounts_snapshot()
    if oauth_mod is None:
        oauth_mod = cswap_api.oauth_module()
    cooldowns = cooldowns or {}
    now = time.time()
    by_email = {a.email: a for a in snapshot.accounts}
    rows = []
    for email in allowlist:
        snap = by_email.get(email)
        if snap is None:
            rows.append({
                "email": email,
                "number": None,
                "is_active": False,
                "windows": [],
                "five_h_avail": None,
                "seven_d_avail": None,
                "fable_headroom": None,
                "gate_ok": False,
                "usage_age_s": None,
                "in_cooldown": False,
                "cooldown_until": None,
                "disabled": False,
                "not_in_cswap": True,
            })
            continue
        rows.append(_row(snap, oauth_mod, cooldowns.get(email), now))
    return rows


def select(rows: list[dict]) -> list[dict]:
    """Ordered candidate list from disclosure rows.

    Excludes: not-in-cswap, in_cooldown, disabled (cswap's own
    held-out-of-auto-rotation flag), and any row failing the eligibility
    gate (``gate_ok`` is not True — a gate that could not be verified is not
    passed; see module docstring's missing-window policy). A row with no
    ``gate_ok`` key at all (e.g. a caller-supplied row shape that predates
    the gate) is treated the same as a failed gate — fail closed on the
    missing key, never a silent pass.

    ``is_active`` is deliberately NOT an exclusion (see module docstring).
    The active account competes on the same four terms as every other and
    is executed through the direct, no-wrapper argv.

    Ranks the remainder by ``fable_headroom`` descending; rows with unknown
    fable_headroom (Fable window not yet reported, though 5h/7d cleared the
    gate) rank LAST — disclosed, not skipped. Rows with known
    fable_headroom <= 0 also stay in (the runner's rate_limited handling is
    the authority on an actual 429), ranked at the bottom of the
    known-headroom set by the same sort.
    """
    eligible = [
        r for r in rows
        if not r.get("not_in_cswap")
        and not r["in_cooldown"]
        and not r.get("disabled")
        and r.get("gate_ok")
    ]
    known = [r for r in eligible if r["fable_headroom"] is not None]
    unknown = [r for r in eligible if r["fable_headroom"] is None]
    known.sort(key=lambda r: r["fable_headroom"], reverse=True)
    return known + unknown


def require_candidates(rows: list[dict]) -> list[dict]:
    """select(), raising a structured no_headroom error when empty."""
    candidates = select(rows)
    if not candidates:
        raise FableError(
            "no_headroom",
            "no eligible account: after allowlist ∩ live accounts, "
            "excluding accounts in rate-limit cooldown, accounts cswap has "
            "marked disabled, and accounts failing the 5h/7d availability "
            "gate, zero candidates remain.",
            failure_class="no_headroom",
            accounts=rows,
        )
    return candidates
