#!/usr/bin/env python3
"""node-fable-mcp — one-shot headless Fable consultations via cswap.

FastMCP stdio server exposing exactly two tools: `fable_consult` (launch or
resume a consultation) and `fable_headroom` (read-only account eligibility).
Layout: entry file + `node_fable` package under
services/, per-test-file sys.path bootstrap, no conftest/pyproject.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

SERVICES_DIR = Path(__file__).resolve().parent
if str(SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICES_DIR))

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from node_fable import accounts, call_state, config, runner
from node_fable.errors import FableError

INSTRUCTIONS = """\
SPEND AND WRITE RISK: `fable_consult` costs real money on a real Anthropic account on every call, and `confirmed=True` arms it to edit and write files on the granted directories. Set `confirmed` only on explicit operator approval.
node-fable-mcp runs one-shot headless consultations with the Fable model through the cswap account switcher. Search for it when you need Fable-grade reasoning on a bounded artifact: an adversarial review, a diff review, a plan, a root-cause diagnosis or a scoped implementation. It is not for interactive back-and-forth.
Tools: `fable_consult` runs one role-framed consultation (adversarial_review, review_diff, plan, diagnose, implement). Unarmed, the default, it only reads: Read, Grep, Glob and the granted read-only fleet tools, with no edits. Git commit and push stay denied in both states. `fable_headroom` is the eligibility check: it spends nothing, starts no consultation, and may refresh stale usage from the account's usage API. Call it first when unsure any account has capacity.
Briefs travel on stdin, never argv. A secret found in a brief refuses the call outright.
"""

mcp = FastMCP("node-fable-mcp", instructions=INSTRUCTIONS)


def _server_build_sha() -> str | None:
    """Short git SHA of this checkout's HEAD, read once at import time.

    This is a long-running stdio server: a fixed source-tree bug lands the
    moment it's committed but the RUNNING process keeps serving the old code
    until something restarts it.
    Two callers independently reading the same real bug and the same real
    fix reached opposite conclusions on 2026-09-09/10 because the answer
    depended on which of four concurrently-running server generations
    happened to serve the call, and nothing in the payload disclosed which
    one that was. Stamping the build lets a caller tell "genuinely still
    broken" from "this process just hasn't restarted yet" without needing
    to cross-reference `ps` and `git log` by hand.

    Pure file read (loose-ref HEAD only, no packed-refs fallback — this
    app's own checkout has never packed its refs) — no subprocess, matching
    the fork-cost hierarchy even where the app's core purpose is spawning
    one. Never raises; an unreadable checkout yields None rather than
    breaking every payload over a diagnostics-only field.
    """
    try:
        git_dir = SERVICES_DIR.parent / ".git"
        head = (git_dir / "HEAD").read_text().strip()
        if head.startswith("ref:"):
            ref_path = git_dir / head.split(" ", 1)[1]
            sha = ref_path.read_text().strip()
        else:
            sha = head
        return sha[:12] or None
    except Exception:
        return None


_SERVER_BUILD_SHA = _server_build_sha()


def _json(payload: dict) -> str:
    payload.setdefault("server_build", _SERVER_BUILD_SHA)
    return json.dumps(payload, ensure_ascii=False, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Fable Consult",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def fable_consult(
    role: Annotated[str, Field(description=(
        "One of adversarial_review, review_diff, plan, diagnose, implement. Sets the "
        "system prompt and the effort default; no role is structurally barred from "
        "confirmed=True."
    ))],
    brief: Annotated[str, Field(description=(
        "The task text, delivered to the subprocess on stdin (never argv). Refused "
        "outright on secret-pattern hits; otherwise never rewritten (except optional "
        "aliasing, see alias_internal_names)."
    ))],
    material: Annotated[list[str] | None, Field(description=(
        "Local paths to grant read access to (--add-dir roots; a file grants its parent "
        "directory). They must exist, be readable and fall inside the granted roots; "
        "~/.claude and ~/.claude-swap-backup are never grantable, directly or via an "
        "ancestor directory (granting $HOME is refused)."
    ))] = None,
    confirmed: Annotated[bool, Field(description=(
        "False = unarmed read-only; True = armed writes (Edit/Write/NotebookEdit/Bash on "
        "the granted directories, a real immediate mutation). Set True only on explicit "
        "operator approval you can cite (see WRITE GUARD)."
    ))] = False,
    session_id: Annotated[str | None, Field(description=(
        "Resume a prior Fable-minted session (pitfalls 4/5/7). Unknown ids are refused "
        "fail-closed. Omit it to mint a FRESH session: the server assigns and records "
        "that id BEFORE spawning, so it comes back in the result even on a timeout or "
        "any other failure, not only on success."
    ))] = None,
    model: Annotated[str | None, Field(description=(
        "One of claude-fable-5-1 (default), claude-opus-5, claude-opus-4-8; anything "
        "else is rejected before launch."
    ))] = None,
    effort: Annotated[str | None, Field(description=(
        "low/medium/high/xhigh/max; default per role; raising it costs real time, "
        "lowering it risks an unflagged shallower answer (pitfall 11)."
    ))] = None,
    scope: Annotated[str | None, Field(description=(
        "plan role only: granularity of the plan (free text, e.g. \"phases\" or "
        "\"per-file edits\"); default \"standard\"."
    ))] = None,
    target_repo: Annotated[str | None, Field(description=(
        "diagnose/implement: the repository path to grant."
    ))] = None,
    git_write_override: Annotated[bool, Field(description=(
        "Drops the git-write deny for this one call. Operator-authorized use only, "
        "with a citable current-turn operator instruction (see WRITE GUARD)."
    ))] = False,
    alias_internal_names: Annotated[bool, Field(description=(
        "When True, applies simple hostname/IP aliasing to the brief before sending. "
        "Default False."
    ))] = False,
) -> str:
    """Run a one-shot headless Fable consultation under a selected account.

    COST: every call spends real money (canary: $0.557 for two words).

    WRITE GUARD: confirmed=False (default) is UNARMED: read-only (Read/Grep/Glob
    and the granted read-only MCP tools; no Edit/Write).
    confirmed=True ARMS Edit/Write/NotebookEdit/Bash on the granted directories:
    a real immediate mutation. Set it only on explicit operator approval you can
    cite. Git commit/push stay denied in both states, but a python- or
    library-level commit is NOT caught. git_write_override=True drops that deny
    for one call and needs a citable current-turn operator instruction.

    NOT FOR interactive back-and-forth, or any brief or material holding
    credentials: a secret hit REFUSES the call, and every granted path is read
    by Fable and kept in a PLAINTEXT transcript under the account that ran it.

    WHEN TO USE: Fable-grade reasoning on a bounded artifact (a review, plan,
    diagnosis or scoped implementation). Sibling: `fable_headroom`; call it
    first when unsure of capacity.

    PITFALLS: all 13 are in README.md ("fable_consult reference").

    Returns:
        JSON: status, session_id, cost_usd, result. A launched call that fails
        sets failure_class to one of no_headroom, subprocess_error,
        malformed_output, timeout, refused, rate_limited; branch on each. A
        pre-launch refusal (secret_refused, forbidden_path, self_review) has no
        failure_class and carries error_class instead; branch on that field.
    """
    try:
        return _json(
            runner.consult(
                role=role,
                brief=brief,
                material=material,
                confirmed=confirmed,
                session_id=session_id,
                model=model,
                effort=effort,
                scope=scope,
                target_repo=target_repo,
                git_write_override=git_write_override,
                alias_internal_names=alias_internal_names,
            )
        )
    except FableError as exc:
        return _json(exc.payload())
    except Exception as exc:  # fail closed, structured — never a raw traceback wall
        return _json({
            "status": "error",
            "error_class": "internal_error",
            "message": f"{type(exc).__name__}: {exc}",
        })


@mcp.tool(
    annotations=ToolAnnotations(
        title="Fable Headroom",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
def fable_headroom() -> str:
    """Report per-account Fable eligibility; starts no consultation, spends no model money.

    NOT a pure local read: it calls cswap's accounts_snapshot, which can fetch
    stale usage from the account usage API and refresh tokens (for the
    logged-in account, under Claude Code's own lock, which can rewrite its live
    credential file), and it creates and prunes the local call-state file. Usage rows are CACHED until stale;
    `usage_age_s` is each row's staleness.

    WHEN TO USE: before a `fable_consult` when unsure any account has capacity,
    or to diagnose a no_headroom / rate_limited result. Sibling:
    `fable_consult` is the spending tool.

    Percentages are UTILIZATION (% used); headroom ("available") = 100 - window
    pct. The rule has four terms: NOT `disabled`,
    `five_h_avail > 0`, `seven_d_avail > 0`, and among those the greatest
    remaining `fable_headroom`. The first three are GATES: either window
    exhausted removes the account. Only the fourth ranks, and it is the Fable
    window's own headroom, NOT the minimum of it and 5h.

    `is_active` is NOT a term: the logged-in account competes like any other and
    selector_would_pick names it when it wins. It only selects the launch path.
    A missing 5h or 7d reading FAILS the gate; a missing Fable reading ranks
    that account last, disclosed as `fable_headroom: null`.

    Returns:
        JSON {status:"ok", accounts:[{email, is_active, windows:[{label,
        pct_used, resets_at}], five_h_avail, seven_d_avail, fable_headroom,
        gate_ok, usage_age_s, in_cooldown, disabled}, ...],
        selector_would_pick: <email|null>}. selector_would_pick is the account
        a fresh fable_consult would try first right now.
    """
    try:
        store = call_state.CallStateStore(config.call_state_path())
        rows = accounts.account_rows(cooldowns=store.cooldowns())
        candidates = accounts.select(rows)
        public_rows = [
            {k: v for k, v in r.items() if k != "number"} for r in rows
        ]
        return _json({
            "status": "ok",
            "accounts": public_rows,
            "selector_would_pick": candidates[0]["email"] if candidates else None,
        })
    except FableError as exc:
        return _json(exc.payload())
    except Exception as exc:  # fail closed, structured
        return _json({
            "status": "error",
            "error_class": "internal_error",
            "message": f"{type(exc).__name__}: {exc}",
        })


if __name__ == "__main__":
    mcp.run()
