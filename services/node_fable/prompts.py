"""Role system-prompt construction.

Five roles, each a DISTINCT system prompt built from its template
(prompts/<role>.md) + the shared operating fragments (guard state, tool-use,
model right-sizing) + material list (+ scope, plan only). There is NO
elaborate/orchestrate role — both live under ``plan`` with the ``scope``
param setting granularity.

The constructed prompt (templates + fragments — NOT the caller's brief) is
passed through scrub.neutralize_jargon so governance vocabulary never leaks
into a prompt consumed under an external account. Effort defaults live here
beside the roles they describe; --effort is ALWAYS passed explicitly on argv
even when it equals the default (runner.py).
"""

from __future__ import annotations

from pathlib import Path

from node_fable import scrub
from node_fable.errors import FableError

ROLES = ("adversarial_review", "review_diff", "plan", "diagnose", "implement")

# No role defaults to max.
ROLE_EFFORT_DEFAULT = {
    "adversarial_review": "high",
    "review_diff": "high",
    "plan": "high",
    "diagnose": "high",
    "implement": "medium",
}

EFFORTS = ("low", "medium", "high", "xhigh", "max")

# Roles whose target_repo param joins the --add-dir grants.
REPO_ROLES = ("diagnose", "implement")

_TEMPLATES_DIR = Path(__file__).resolve().parent / "prompts"


def _template(role: str) -> str:
    path = _TEMPLATES_DIR / f"{role}.md"
    if not path.is_file():
        raise FableError(
            "missing_dependency",
            f"prompt template missing for role '{role}' at {path} — the "
            "package is incomplete; reinstall.",
        )
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FableError(
            "missing_dependency",
            f"cannot read prompt template {path}: {exc}",
        ) from exc


def _guard_fragment(confirmed: bool) -> str:
    if confirmed:
        return (
            "Tool access state: ARMED. Edit/Write/NotebookEdit/Bash are "
            "enabled — your file edits and commands take effect immediately "
            "on the granted directories. Git commit and git push remain "
            "denied regardless."
        )
    return (
        "Tool access state: UNARMED (read-only). You have Read/Grep/Glob and "
        "the granted read-only external tools in your tool list. For git "
        "reads use the granted git tools (mcp__node-git-tools-mcp__git_status, "
        "git_log, git_diff, git_show, and their siblings) when present; there "
        "is no shell git path, and a shell command chained with && or ;, a "
        "heredoc, a cd, or a python one-liner is refused without a prompt. Do "
        "not attempt writes; describe the changes you would make instead."
    )


_RIGHT_SIZING_FRAGMENT = (
    "Model right-sizing: if you spin up subagents or fan out work yourself, "
    "use the cheapest adequate model tier for each subtask — bulk or "
    "mechanical work goes to the lowest tier, mid-tier for ordinary "
    "reasoning, and the top tier only for a step that genuinely requires it."
)

# Tool-use doctrine for whatever read-only external tools happen to be
# granted (mcp_grant.py's on-demand grant — see that module's docstring for
# the mechanism this fragment describes). Written role-agnostic and
# confirmed-agnostic because the doctrine holds regardless of role or arm
# state; measured at 480 characters / 80 words (Task I requirement 4 — the
# ceiling below is measured on this exact string, not assumed), comparable
# in size to _RIGHT_SIZING_FRAGMENT above.
_TOOL_USE_FRAGMENT = (
    "Tool-use note: any tool named mcp__<server>__<name> in your tool list "
    "is a granted external tool, and it is read-only. Every write-capable "
    "tool, and every tool with no declared read-only status, has been "
    "removed from your tool list entirely, not merely blocked. Prefer a "
    "granted read tool over reconstructing the same read with a shell "
    "command when it already covers what you need. A granted tool never "
    "returns a credential or secret value; never expect or ask for one "
    "through it."
)

# Measured ceiling on the fragment's added size (Task I requirement 4) — the
# fragment above is 480 chars / 80 words at commit time; this constant is
# the enforced ceiling with headroom, not the measured value itself, so a
# future edit can grow the fragment a little without a rules-text vote but
# cannot silently balloon it back toward template-sized prose.
TOOL_USE_FRAGMENT_MAX_CHARS = 700


def build_prompt(
    role: str,
    *,
    confirmed: bool,
    material: list[str] | None,
    scope: str | None,
    target_repo: str | None,
) -> str:
    """Assemble the role system prompt (jargon-neutralized builder text)."""
    if role not in ROLES:
        raise FableError(
            "invalid_role",
            f"unknown role '{role}' — valid roles: {', '.join(ROLES)}",
        )
    body = _template(role)
    if role == "plan":
        granularity = (scope or "standard").strip() or "standard"
        body = body.replace("{scope}", granularity)
    # Builder text (template body incl. substituted scope + fragments) is
    # jargon-neutralized; the material/target_repo PATH LISTINGS are appended
    # AFTER neutralization, verbatim — a neutralized path is a nonexistent
    # path (a path segment containing a jargon term would be rewritten into a nonexistent path),
    # telling Fable an address it can never open while --add-dir grants the
    # real one (review finding).
    builder_parts = [
        body.strip(),
        _guard_fragment(confirmed),
        _TOOL_USE_FRAGMENT,
        _RIGHT_SIZING_FRAGMENT,
    ]
    neutralized = scrub.neutralize_jargon("\n\n".join(builder_parts))
    path_parts: list[str] = []
    if target_repo and role in REPO_ROLES:
        path_parts.append(f"Target repository (granted read path): {target_repo}")
    if material:
        listing = "\n".join(f"- {p}" for p in material)
        path_parts.append("Material provided (granted read paths):\n" + listing)
    return "\n\n".join([neutralized, *path_parts]) + "\n"
