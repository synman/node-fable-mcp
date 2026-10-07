"""Shared call-state store — sessions, cooldowns, resume rules.

Single JSON file (state_dir/call-state.json), fcntl-flock'd read-modify-write.
A stdio MCP server is NOT a singleton — every CC session spawns its own
server process — so this file is the only shared state between them.

Row shape, keyed by session_id:
  {role, account, status, terminal_reason, failure_class, created_ts, last_ts}
``status`` is "pending" from ``record_pending`` (written BEFORE spawn, for
every FRESH mint — never for a resume target) through to "ok"/"error" once
``record_result`` overwrites it with the real outcome. A row stuck at
"pending" past its own attempt's lifetime means the attempt never reached a
terminal classification (e.g. the calling process itself died before
``_record`` ran) — distinct from a genuinely unknown session_id, which has
no row at all.
Plus per-account cooldown timestamps under "cooldowns" for rate_limited.

Enforced resume rules (all fail closed):
  (a) self-review, per role (scope literally adversarial_review-only, docstring-only criterion, no code-level gate
      on the criterion itself):
        - review_diff passing ANY session_id is rejected unconditionally —
          every session in this store is Fable-minted, so resuming one under
          review_diff is Fable reviewing its own prior output (self-review).
          review_diff never resumes.
        - adversarial_review passing a session_id is rejected UNLESS the
          resumed session's own recorded role is also adversarial_review —
          i.e. this is adversarial_review resuming its OWN prior session,
          never a different role's output (still self-review otherwise).
          This module enforces only that structural, same-role provenance
          check. Whether the CALLER *should* pass session_id at all is a
          separate, caller-owned judgment carried in the published tool
          docstring, not gated here: resume adversarial_review's own prior
          session only when BOTH hold — material changes were made since it
          last ran, AND Fable will benefit from the prior context in its
          analysis and decision-making — otherwise start fresh.
  (b) a session whose last result was ``refused`` is refused for resume —
      reframe and go fresh. This applies to BOTH review roles unchanged: an
      adversarial_review session matching its own-role provenance (a) is
      still refused for resume if it ended in a refusal; same-role match
      never bypasses this gate;
  (c) an UNKNOWN session_id is refused outright — this store is the only
      account-pinning record, so an unknown id cannot be routed safely;
  (d) resume targets the RECORDED account only (sessions are account-
      pinned). On the cswap-wrapped path this is enforced STRUCTURALLY, by
      TWO facts about cswap rather than one: every launch is prefixed
      `cswap run <account> --require-session`, and --require-session
      REFUSES outright (raises, no launch) when `account` is already the
      active default login instead of falling back to a plain launch — so
      a cswap-wrapped mint's transcript is ALWAYS written under that
      account's own isolated session profile
      (<backup>/sessions/<num>-<slug>/projects/...), never under the
      shared ~/.claude/projects/ store. A session_id that does not exist
      under the account's isolated profile fails upstream with "No
      conversation found" (exit 1, no spawn cost — verified empirically,
      no JSON, plain stderr text, `--output-format json` is not honored on
      this error path). The direct-invoke path (Task F's Opus tiers)
      shares no such isolation — build_direct_argv() never sets
      CLAUDE_CONFIG_DIR, so both a direct-invoke mint and a direct-invoke
      resume always hit the ONE shared ~/.claude/projects/ store, keyed on
      whatever account is the active default login at the time. A session
      minted this way is pinned to whichever account was active AT MINT,
      and that pin can drift from the NOW-active account by resume time
      (e.g. an intervening `cswap use`). Verified this does NOT fail
      loudly on that drift: the transcript file sits at the same physical
      path regardless of which account's credentials are currently active,
      so an unmatched-active resume would silently continue the pinned
      session's transcript under the CURRENT account's credentials/quota
      instead of refusing. (A cswap-wrapped-minted session later resumed
      direct cannot hit this — its transcript lives under the OTHER
      account's isolated profile dir, so the shared store has nothing at
      that session_id and it fails loudly instead, per the paragraph
      above.) runner.consult() enforces the pin explicitly on the
      direct-invoke path instead — comparing the recorded account against
      _active_account_email() before any spawn and refusing
      (account_mismatch) on a mismatch — so rule (d) holds universally, by
      two different mechanisms depending on launch path.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from node_fable.errors import FableError

_EMPTY_STATE: dict[str, Any] = {"sessions": {}, "cooldowns": {}}


class CallStateStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _read_locked(self, fh) -> dict:
        fh.seek(0)
        raw = fh.read()
        if not raw.strip():
            return {"sessions": {}, "cooldowns": {}}
        try:
            data = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise FableError(
                "state_corrupt",
                f"call-state file {self.path} holds invalid JSON ({exc}) — "
                "refusing to guess; repair or move the file aside.",
            ) from exc
        if not isinstance(data, dict):
            raise FableError(
                "state_corrupt",
                f"call-state file {self.path} top level is not an object — "
                "refusing to guess; repair or move the file aside.",
            )
        data.setdefault("sessions", {})
        data.setdefault("cooldowns", {})
        return data

    def _with_locked_state(self, fn: Callable[[dict], Any], write: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 0600: rows carry the consumer-account emails and session→account
        # pins — owner-only, like every credential-adjacent local file
        # (review finding). O_CREAT mode applies on creation; chmod covers a
        # pre-existing loose file.
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        # a+ semantics preserved via the fd: created if missing, never
        # truncated on open.
        with os.fdopen(fd, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                data = self._read_locked(fh)
                result = fn(data)
                if write:
                    fh.seek(0)
                    fh.truncate()
                    fh.write(json.dumps(data, indent=1, sort_keys=True))
                    fh.flush()
                return result
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    # ── sessions ─────────────────────────────────────────────────────────

    def record_result(
        self,
        session_id: str,
        *,
        role: str,
        account: str,
        terminal_reason: str | None,
        failure_class: str | None,
        status: str = "error",
    ) -> None:
        """Upsert the row for session_id after any terminal result.

        ``status`` is the caller's own ``payload["status"]`` ("ok"/"error"),
        passed explicitly rather than re-derived from terminal_reason/
        failure_class — those two are only reliably distinguishing for some
        failure classes, not all, and re-deriving status from them risks
        silently misclassifying one. This overwrites a ``pending`` row
        (see ``record_pending``) with the real terminal state."""
        if not session_id:
            return

        def _mut(data: dict) -> None:
            now = time.time()
            row = data["sessions"].get(session_id) or {"created_ts": now}
            row.update({
                "role": role,
                "account": account,
                "status": status,
                "terminal_reason": terminal_reason,
                "failure_class": failure_class,
                "last_ts": now,
            })
            data["sessions"][session_id] = row

        self._with_locked_state(_mut, write=True)

    def record_pending(self, session_id: str, *, role: str, account: str) -> None:
        """Insert a row for a freshly-minted session_id BEFORE the
        subprocess spawns, so a kill (timeout, crash, cancellation) still
        leaves an addressable row instead of an orphaned transcript with no
        known id anywhere in this store (an earlier finding
        23:19 EDT — the prior design only ever wrote a row from the CLI's
        own completed JSON, which a killed process never prints at all).

        ``status: "pending"`` distinguishes this from a genuinely-terminal
        row; ``record_result`` overwrites it with the real outcome once the
        attempt finishes, one way or the other. Callers only invoke this for
        a FRESH mint — a resume target already has its own completed row and
        this must never be called against it (that would transiently
        un-complete a finished session's row for the duration of the new
        attempt)."""
        if not session_id:
            return

        def _mut(data: dict) -> None:
            now = time.time()
            row = data["sessions"].get(session_id) or {"created_ts": now}
            row.update({
                "role": role,
                "account": account,
                "status": "pending",
                "terminal_reason": None,
                "failure_class": None,
                "last_ts": now,
            })
            data["sessions"][session_id] = row

        self._with_locked_state(_mut, write=True)

    def get(self, session_id: str) -> dict | None:
        return self._with_locked_state(
            lambda data: data["sessions"].get(session_id), write=False
        )

    # ── cooldowns ────────────────────────────────────────────────────────

    def set_cooldown(self, email: str, seconds: float) -> None:
        def _mut(data: dict) -> None:
            data["cooldowns"][email] = time.time() + seconds

        self._with_locked_state(_mut, write=True)

    def cooldowns(self) -> dict[str, float]:
        """Live cooldowns only (expired entries pruned on read)."""

        def _mut(data: dict) -> dict[str, float]:
            now = time.time()
            live = {e: t for e, t in data["cooldowns"].items() if t > now}
            data["cooldowns"] = live
            return dict(live)

        return self._with_locked_state(_mut, write=True)

    def in_cooldown(self, email: str) -> bool:
        return email in self.cooldowns()


def validate_resume(store: CallStateStore, session_id: str, role: str) -> dict:
    """Enforce resume rules (a)-(d); return the recorded row on success.

    The returned row carries the pinned ``account`` — the ONLY account a
    resume may target (rule d). A resume call never runs the account
    selector.

    review_diff is checked unconditionally, before the row is even read — it
    never resumes, so there is nothing to look up. adversarial_review is
    checked AFTER the row is read, because whether it counts as self-review
    depends on the resumed session's own recorded role (rule a).
    """
    if role == "review_diff":
        raise FableError(
            "self_review",
            f"role '{role}' may not resume a Fable-minted session — that is "
            "Fable reviewing its own prior output. Run the review fresh "
            "against the artifact instead.",
            failure_class="refused",
        )
    row = store.get(session_id)
    if row is None:
        raise FableError(
            "unknown_session",
            f"session_id '{session_id}' is not in the call-state store — "
            "refusing the resume outright (fail closed): without a recorded "
            "row there is no account pinning and no refusal history.",
        )
    if role == "adversarial_review" and row.get("role") != "adversarial_review":
        raise FableError(
            "self_review",
            f"role 'adversarial_review' may not resume session "
            f"'{session_id}' — it was run under role '{row.get('role')}', "
            "not adversarial_review, so this is not the sanctioned "
            "own-prior-session resume. That is still Fable reviewing a "
            "different prior output (self-review). Run the review fresh "
            "against the artifact instead.",
            failure_class="refused",
        )
    if row.get("failure_class") == "refused":
        raise FableError(
            "refused_session",
            f"session '{session_id}' ended in a refusal — resuming a refused "
            "conversation is refused (backstop for pitfall 5): reframe the "
            "brief and start a fresh session.",
        )
    account = row.get("account")
    if not account:
        raise FableError(
            "state_corrupt",
            f"session '{session_id}' row records no account — cannot pin the "
            "resume; refusing (fail closed).",
        )
    return row
