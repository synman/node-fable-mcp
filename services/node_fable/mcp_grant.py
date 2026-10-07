"""Read-only fleet MCP tool grant for Fable launches (Task G).

Wires ``--mcp-config``/``--settings`` from a MEASURED per-server tool census
plus the node's own MCP server registrations, instead of the always-empty
literal every prior packet shipped. The mechanism (confirmed by measurement): a bare exact tool name with no wildcard and no parentheses in
``permissions.deny`` removes that tool from the subprocess's own context
entirely (genuinely undiscoverable) — a parameter-scoped or glob deny only
blocks the call while leaving the tool listed. Classification is by each
tool's ``readOnlyHint`` annotation, never by name/verb heuristic.

FAIL-CLOSED, load-bearing: a tool carrying no ``readOnlyHint`` is
UNCLASSIFIED, and unclassified is denied exactly like a write tool — never
treated as read-only by default. ``EXCLUDED_SERVERS`` is fixed by design
directive (Open Tangents thread 1) and is denied wholesale by bare server
name even though it is never present in ``mcpServers`` either — the same
belt-and-braces posture as runner.py's always-on git-write deny.
``_SELF_SERVER`` is a FIFTH, engineering-only exclusion (no recursive
self-grant) — additional to, and distinct from, the maintainer's 4-server
``EXCLUDED_SERVERS`` spec, not evidence that spec is incomplete.
``NAME_DENIED_TOOLS`` is a tool-level deny by qualified name that no census
content and no ``readOnlyHint`` can lift: read-only tools whose RETURN carries
secrets into a plaintext transcript.

This module does not merely trust the census producer to have named every
tool it saw in ``write_tool_names``/``unclassified_tool_names``.
``generate_grant()`` also cross-checks each row's own declared
``tools_total`` against what it actually enumerated (the ``read_only``
count plus those two name lists) — a positive shortfall means the producer
reached tools it never classified (a plausible shape for a partial or
truncated write), and a shortfall cannot be denied tool-by-tool because the
unnamed tools cannot be named. The whole server is denied instead, wholesale,
rather than granted with an unaccounted remainder treated as read-only by
default. A row whose ``tools_total`` is absent, JSON ``null``, or not
literally an integer (e.g. a stringified count) cannot be reconciled at all
either — and is now ALSO denied wholesale, exactly like a positive
shortfall, rather than granted through the two name lists alone: treating
absence as "fine" would reopen the identical hole one level up, since a
producer (or a hand-edited/corrupted census file) could bypass this entire
cross-check simply by never populating the field. The gap is still
disclosed by name (``servers_tools_total_unavailable``), distinct from a
positive, COMPUTED shortfall (``servers_incomplete_classification``), so a
reader can tell "this row was never verified" from "this row was verified
and found short." A ``servers_reached`` entry that is not a dict at all is
never handed to any of the above — it is skipped and named by index in
``servers_malformed`` before any attribute access is attempted on it, so a
single corrupted row cannot raise out of this function and take the whole
launch down with it.

Regeneration is ON DEMAND (the deny-list regenerates silently and is used
immediately): this module reads whatever census + registration files are
present on disk at CALL time. There is no cache and no live tools/list
probe here — MEASURING the live per-server census (spawning each granted
server, reading its ``tools/list`` annotations) is a separate, explicitly-
invoked step and is out of THIS module's scope; it consumes the census as
an already-produced artifact, the same relationship ``scrub.py`` has to
the secret-pattern list in ``secret_patterns.py``. Absence of a census or of
either registration file fails SAFE, never loud: zero servers are granted
(the pre-Task-G default) plus the always-on exclusion deny — never a crash
that would take fable_consult down over a missing regen.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Servers NEVER granted, regardless of census content — the exclusion list
# IS the full specification (the exclusion set is fixed by design), not a starting point for a further allowlist decision.
# Deny-listed by bare server name even though they are never present in
# mcpServers either (belt-and-braces).
EXCLUDED_SERVERS = frozenset({
    "claude-channel-watcher",
    "isaac-mcp",
    "node-federation-mcp",
    "node-whisper-mcp",
})

# Tools NEVER granted, regardless of census content and regardless of
# readOnlyHint: qualified names (mcp__<server>__<tool>) so a same-named tool on
# another server is not caught. A read-only annotation says a tool changes
# nothing; it says nothing about what the tool RETURNS, and these four return
# material that must not reach a consultation's plaintext transcript under
# whichever account ran the call (README pitfall 13): process_list and
# detect_orphans return `cmdline` for every visible process (MCP config JSON,
# Authorization headers, passwords passed as arguments), system_health embeds
# the first five detect_orphans rows (cmdline cut at 200 characters) under
# `orphans.top`, and session_output returns captured tmux pane text. Each is a
# truthful read-only tool; the deny is about what flows out, so no re-measure of
# the census can lift it.
NAME_DENIED_TOOLS = (
    "mcp__node-process-mcp__process_list",
    "mcp__node-process-mcp__detect_orphans",
    "mcp__node-process-mcp__system_health",
    "mcp__node-procpool-mcp__session_output",
)

# node-fable-mcp never grants itself to itself — no recursive spawn. This
# is a fifth exclusion, added for that engineering reason alone — it is
# NOT part of the maintainer's 4-server EXCLUDED_SERVERS spec above and its
# presence does not mean that spec is incomplete.
_SELF_SERVER = "node-fable-mcp"

ENV_CENSUS_PATH = "NODE_FABLE_MCP_CENSUS_PATH"
ENV_MCP_JSON_PATH = "NODE_FABLE_MCP_JSON_PATH"
ENV_CLAUDE_JSON_PATH = "NODE_FABLE_CLAUDE_JSON_PATH"

DEFAULT_CENSUS_PATH = "~/.node-fable-mcp/mcp-tool-census.json"
DEFAULT_MCP_JSON_PATH = "~/.mcp.json"
DEFAULT_CLAUDE_JSON_PATH = "~/.claude.json"

_EMPTY_CENSUS: dict = {"servers_reached": [], "servers_failed": []}


def _resolved_path(env_var: str, default: str) -> Path:
    raw = os.environ.get(env_var, "").strip() or default
    return Path(os.path.expanduser(raw))


def census_path() -> Path:
    return _resolved_path(ENV_CENSUS_PATH, DEFAULT_CENSUS_PATH)


def mcp_json_path() -> Path:
    return _resolved_path(ENV_MCP_JSON_PATH, DEFAULT_MCP_JSON_PATH)


def claude_json_path() -> Path:
    return _resolved_path(ENV_CLAUDE_JSON_PATH, DEFAULT_CLAUDE_JSON_PATH)


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def load_census() -> dict:
    """The on-disk per-server tool census.

    Shape: ``{"servers_reached": [{"name", "read_only", "write_tool_names",
    "unclassified_tool_names", ...}, ...], "servers_failed": [...]}``. A
    tool with no measured ``readOnlyHint`` is carried in
    ``unclassified_tool_names`` — the census's producer, not this reader,
    is where fail-closed classification happens; this function only
    reports a missing/malformed file as the empty census (fails SAFE: no
    servers granted beyond the always-on exclusion deny), never raises.
    """
    data = _read_json(census_path())
    if data is None:
        return dict(_EMPTY_CENSUS)
    data.setdefault("servers_reached", [])
    data.setdefault("servers_failed", [])
    return data


def load_server_configs() -> dict:
    """Merge this node's own MCP server registrations.

    Project scope (``.mcp.json``) takes precedence over user scope
    (``.claude.json``) on a name collision, mirroring Claude Code's own
    project-over-user precedence. Missing/malformed files contribute
    nothing (fails SAFE — fewer grantable servers, never a crash). Always
    strips ``node-fable-mcp`` itself, present in project scope as the
    self-registration.
    """
    merged: dict = {}
    for path in (claude_json_path(), mcp_json_path()):
        data = _read_json(path)
        servers = data.get("mcpServers") if data else None
        if isinstance(servers, dict):
            merged.update(servers)
    merged.pop(_SELF_SERVER, None)
    return merged


def _as_int_or_none(value) -> int | None:
    """Coerce a census field expected to be an integer count.

    Returns the value unchanged only when it is literally an ``int`` —
    ``None`` for anything else: a missing key (``row.get`` already
    returns ``None``), JSON ``null``, a stringified count, a list, etc.
    Callers treat "malformed" and "genuinely absent" identically rather
    than performing arithmetic on an untrusted type (residual,
    SIGNIFICANT: mcp_grant.py 204, 233 — a malformed ``tools_total`` used
    to raise ``TypeError`` here instead of failing safe).
    """
    return value if isinstance(value, int) else None


def generate_grant(
    census: dict, server_configs: dict, *,
    excluded=EXCLUDED_SERVERS, census_present: bool = True,
    name_denied=NAME_DENIED_TOOLS,
) -> dict:
    """Pure: census + server registrations -> {mcpServers, deny, disclosure}.

    Never a hand-typed tool allowlist — every deny entry traces to either
    the fixed ``excluded`` server-name set or a name the census itself
    reported as write/unclassified. Deny entries are always bare exact
    names (``mcp__<server>`` or ``mcp__<server>__<tool>``) — no
    parentheses, no wildcard, the form measured (2026-09-07, matched
    positive/negative control) to remove a tool from the subprocess's
    context entirely rather than merely block the call.

    ``census_present`` distinguishes "no census file was ever measured"
    from "a census was measured and legitimately found nothing to grant"
    — both would otherwise disclose as an identical empty
    ``servers_included``/zero-counts shape, which is exactly the
    "0 reads as not-anywhere instead of not-in-this" failure.
    ``build_grant()`` passes the real on-disk answer; direct callers
    (tests, or a caller that already holds a census) default to True.

    A row is also cross-checked against its own ``tools_total`` (adversarial
    finding, BLOCKING): ``read_only`` plus the two name lists must account
    for the row's declared total, or the census reached tools it never
    classified. Such a row is denied WHOLESALE (bare server name, never
    added to ``mcpServers``) rather than granted with an unnamed remainder
    treated as read-only by default — the shortfall cannot be denied
    tool-by-tool because the extra tools have no name to deny. The
    shortfall count is disclosed per server in
    ``servers_incomplete_classification``. A row whose ``tools_total`` is
    absent, JSON ``null``, or not literally an integer cannot be
    reconciled at all EITHER, and is now denied wholesale too (residual,
    SIGNIFICANT: mcp_grant.py 225-231) — granting it through the two name
    lists alone would let a producer bypass this whole cross-check simply
    by never populating the field. It is disclosed by name in
    ``servers_tools_total_unavailable``, kept distinct from a positive,
    computed ``servers_incomplete_classification`` shortfall so "never
    verified" never looks identical to "verified and found a hole." A
    ``servers_reached`` entry that is not a dict at all is skipped before
    any attribute access and named by index in ``servers_malformed``
    (residual, SIGNIFICANT: mcp_grant.py 204, 233) — a malformed row used
    to raise out of this function uncaught, which took down every launch
    through it, not merely the affected server's own grant.
    """
    deny: list[str] = [f"mcp__{name}" for name in sorted(excluded)]
    name_denied_set = frozenset(name_denied)
    census_denied: set[str] = set()
    mcp_servers: dict = {}
    servers_included: list[str] = []
    servers_excluded_in_census: list[str] = []
    servers_missing_config: list[str] = []
    servers_malformed: list[str] = []
    servers_incomplete_classification: dict[str, int] = {}
    servers_tools_total_unavailable: list[str] = []
    tools_granted = 0
    tools_denied = 0

    for index, row in enumerate(census.get("servers_reached") or []):
        if not isinstance(row, dict):
            # A malformed entry has no "name" to key any other disclosure
            # field on — index + type is what identifies it, and it is
            # skipped BEFORE any .get()/attribute access is attempted
            # (residual, SIGNIFICANT: mcp_grant.py 204, 233 — this used to
            # raise AttributeError uncaught, taking down every launch
            # through this function, not merely the affected server's
            # grant). A dropped row that discloses nothing would be
            # indistinguishable from a server never in the census at all.
            servers_malformed.append(f"index {index}: {type(row).__name__}")
            continue
        name = row.get("name")
        if not name or name == _SELF_SERVER:
            continue
        if name in excluded:
            servers_excluded_in_census.append(name)
            continue
        write_tools = row.get("write_tool_names") or []
        # FAIL CLOSED: a tool with no readOnlyHint is unclassified, and
        # unclassified is denied exactly like write — never treated as
        # read-only by default (unclassified is never read-only).
        unclassified_tools = row.get("unclassified_tool_names") or []
        deny_names = sorted(set(write_tools) | set(unclassified_tools))
        for tool in deny_names:
            deny.append(f"mcp__{name}__{tool}")
            census_denied.add(f"mcp__{name}__{tool}")
        tools_denied += len(deny_names)
        cfg = server_configs.get(name)
        if cfg is None:
            servers_missing_config.append(name)
            continue

        # read_only_count is trusted to be numeric here — a non-numeric
        # read_only is a shape this pass does not guard (unmeasured;
        # tools_total and non-dict rows are the two shapes verified to
        # reach this function malformed).
        read_only_count = row.get("read_only", 0) or 0
        tools_total = _as_int_or_none(row.get("tools_total"))
        if tools_total is None:
            # Cannot be reconciled at all — and, per the residual this
            # cross-check was written to close, absence must not be a
            # WEAKER guarantee than a present-but-wrong total: a producer
            # (or a hand-edited/corrupted census file) could otherwise
            # bypass this whole check simply by never populating the
            # field, or by populating it with a non-numeric placeholder.
            # Denied wholesale, exactly like a positive shortfall below —
            # the unnamed remainder still cannot be denied tool-by-tool,
            # so there is nothing safer about granting it. Still
            # disclosed by its own name (distinct from a COMPUTED
            # shortfall) so a reader can tell "never verified" from
            # "verified and found a hole."
            deny.append(f"mcp__{name}")
            servers_tools_total_unavailable.append(name)
            continue

        shortfall = tools_total - (read_only_count + len(deny_names))
        if shortfall > 0:
            # FAIL CLOSED, second layer: the producer's own declared
            # total disagrees with what it enumerated. The unnamed
            # remainder cannot be denied tool-by-tool, so the whole
            # server is denied instead of granted by default.
            deny.append(f"mcp__{name}")
            servers_incomplete_classification[name] = shortfall
            continue
        # shortfall <= 0 is an over-count (double-listed tool, or a
        # stale/wrong total) — not the security-relevant direction,
        # since every tool this row names is already accounted for
        # by read_only_count + deny_names; only a POSITIVE shortfall
        # means an unnamed tool exists that this row never disclosed.

        mcp_servers[name] = cfg
        servers_included.append(name)
        # Counted only once the server is actually connected (cfg present)
        # — a server with no registration contributes nothing to
        # mcpServers, so its read-only tools are not reachable and must
        # not inflate a field named "granted".
        # A name-denied tool the row does not already list as write or
        # unclassified is, by the reconciliation above, one of the row's
        # read_only COUNT (the census row carries a count, never the
        # read-only names), so it comes out of that count. If the server
        # does not actually have the tool this under-counts by one, which is
        # the safe direction for a disclosure of what is granted.
        by_name_here = sum(
            1 for qualified in name_denied_set
            if qualified.startswith(f"mcp__{name}__") and qualified not in census_denied
        )
        tools_granted += max(read_only_count - by_name_here, 0)

    # The name deny applies whatever the census said, including no census at
    # all and a row that listed the tool as read-only.
    for qualified in sorted(name_denied_set - census_denied):
        deny.append(qualified)
    tools_denied += len(name_denied_set - census_denied)

    disclosure = {
        "census_present": census_present,
        "servers_included": sorted(servers_included),
        "servers_excluded_by_spec": sorted(excluded),
        "servers_excluded_present_in_census": sorted(servers_excluded_in_census),
        "servers_failed": sorted(census.get("servers_failed") or []),
        "servers_missing_config": sorted(servers_missing_config),
        "servers_malformed": servers_malformed,
        "servers_incomplete_classification": dict(
            sorted(servers_incomplete_classification.items())
        ),
        "servers_tools_total_unavailable": sorted(servers_tools_total_unavailable),
        "tools_read_only_granted": tools_granted,
        "tools_denied": tools_denied,
        "tools_denied_by_name": sorted(name_denied_set),
        "deny_entries": len(deny),
    }
    return {"mcpServers": mcp_servers, "deny": deny, "disclosure": disclosure}


def build_grant() -> dict:
    """The on-demand step: read whatever census + registrations are
    present on disk right now and generate the grant. No caching.

    ``census_present`` is measured directly against the disk path — never
    inferred from the loaded census's content — so a genuinely-measured
    census that found nothing to grant discloses differently from no
    census having been produced at all; both would otherwise present the
    identical empty-servers/zero-counts shape.
    """
    return generate_grant(
        load_census(), load_server_configs(),
        census_present=census_path().is_file(),
    )


def mcp_config_json(grant: dict | None = None) -> str:
    """The ``--mcp-config`` payload: only ``mcpServers``, compact form."""
    g = grant if grant is not None else build_grant()
    return json.dumps({"mcpServers": g["mcpServers"]}, separators=(",", ":"))


def settings_json(grant: dict | None = None) -> str:
    """The ``--settings`` payload: ``disableAllHooks: true``, ``hooks: {}``,
    plus the generated ``permissions.deny`` list, compact form.

    ``disableAllHooks`` is load-bearing and ``hooks: {}`` is not: settings
    merge additively across scopes, so an empty ``hooks`` object here never
    suppressed the user-scope hooks in the real ``~/.claude/settings.json``.
    On the direct launch path (active login, or the Opus tiers) the
    subprocess runs with the real HOME and no config-dir isolation, so the
    operator's own PreToolUse hooks ran INSIDE the paid consultation and
    denied Grep and every shell git read with an MCP-first redirect whose
    targets that subprocess could not reach (measured 2026-09-08, RCA in
    the KB). ``disableAllHooks`` is the documented per-run switch that
    turns hooks off at every scope. The empty ``hooks`` object stays as a
    belt-and-braces declaration and for payload-shape stability.
    """
    g = grant if grant is not None else build_grant()
    return json.dumps(
        {
            "disableAllHooks": True,
            "hooks": {},
            "permissions": {"deny": g["deny"]},
        },
        separators=(",", ":"),
    )
