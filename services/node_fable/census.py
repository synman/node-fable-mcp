"""node_fable.census — measures the census mcp_grant.py's load_census() reads.

Task G shipped a CONSUMER (mcp_grant.generate_grant()) with no PRODUCER: no
census file existed on disk and no code anywhere in this repo or the fleet
measured one (adversarial finding, SIGNIFICANT — see the review notes). This
module closes that gap. an earlier measurementounds row G binds the census to "the live
per-server tools/list annotations measured by this packet itself" — never a
figure carried over from another effort's measurement at another time.

Two measurement methods, both real and both tested, used for different
reasons:

  source_read  -- statically parse a server's OWN Python source for
                  ``@<obj>.tool(...)`` decorated functions and read each
                  one's ``annotations`` keyword (a literal dict, a
                  ``ToolAnnotations(...)`` call, a reference to a
                  module-level constant, or a ``{**CONST, ...}`` spread --
                  the conventions actually in use across this fleet, see
                  module docstring below for the classification contract).
                  Zero execution, zero subprocess, zero network -- reading
                  a file cannot trigger a credential prompt.

  tools_list   -- a REAL MCP initialize + tools/list handshake over stdio,
                  the same JSON-RPC shape evals/verify_eval.py already uses
                  safely against this app's own server. Genuine and unit-
                  tested against a scratch fixture server in this module's
                  own test file -- not vaporware -- but NOT the method this
                  module chooses by default for a REAL fleet server.

``method`` alone ("source_read" vs "tools_list") is NOT a uniform reliability
label -- it collapses two structurally different guarantees for the
``source_read`` rows, which is why every row also carries a second field,
``source_kind`` ("local_stdio" vs "remote_proxy_snapshot"), naming which one
applies (residual, from an earlier review). For a
LOCAL STDIO registration, ``source_read`` reads the exact code that will run
when this node spawns the process for a granted call -- airtight by
construction, same guarantee either method gives. For an HTTP/remote
registration, this module has no local process to read at all: the caller
never chose a --add-dir root the way it does for stdio, so
``resolve_server_root()`` falls back to a workspace-registry path lookup and
scans whatever git checkout happens to live there -- a snapshot of the
SOURCE that presumably informed the deployed service, not a measurement of
the process actually answering that URL at call/grant time. That checkout
can be stale, on a different branch, or (in principle) a different
deployment than what runs behind the registered endpoint, and this module
has no way to detect any of those. ``source_kind`` names this basis
explicitly per row so a reader (or a future gating decision) never mistakes
a remote row's ``source_read`` for the same airtight guarantee a local
stdio row's carries.

Why source_read is the default for every real fleet server (deliberate,
disclosed, not an oversight): the vault-first credential convention
records a real incident -- a bulk MCP-server health-check spawn resolved a
vault key from a subprocess that could not see ``master.key`` and raised a
macOS keychain dialog whose default button resets the login keychain. That
is exactly the shape of "spawn a fresh subprocess for a server this module
does not control and cannot fully audit in advance." This module cannot
prove ahead of time that a GIVEN server's own import-time code never
touches a credential, so the safe default is never to guess: build_census()
never spawns a real fleet server. ``tools_list`` stays available as a
tested capability for a caller that has independently vetted one specific
server as safe (``live_servers=frozenset({"some-server"})``), never as this
module's own default.

FAIL-CLOSED classification contract: ``readOnlyHint: True`` -> read-only;
``readOnlyHint: False`` -> write; anything else -- absent, unresolved, or a
non-boolean value -- is UNCLASSIFIED and named in ``unclassified_tool_names``
exactly like a write tool. Undeclared is never read-only. A classification
MISS in this module (a scope this static reader cannot resolve -- a locally
-scoped annotation constant, a dynamically-computed annotations expression)
always fails toward unclassified, never toward a guessed read-only grant --
the one direction that matters for safety.

Output shape matches exactly what ``mcp_grant.load_census()`` consumes:
``{"servers_reached": [{"name", "tools_total", "read_only", "write",
"unclassified", "write_tool_names", "unclassified_tool_names", "method",
"files_scanned"}, ...], "servers_failed": [...]}`` -- ``servers_failed`` is
kept a plain list[str] (mcp_grant.py sorts it directly) with a PARALLEL
``failure_reasons`` dict for per-server disclosure, so richer disclosure
never breaks the schema mcp_grant.py already reads. ``measured_at``,
``method_by_server``, and each row's ``source_kind`` extend the schema;
mcp_grant.py ignores unknown keys, so this is additive, not a contract
change -- it does not gate any grant decision, per this lane's own bound
(a disclosure fix, not a policy change).
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from node_fable import mcp_grant

ENV_WORKSPACE_PATHS = "NODE_FABLE_WORKSPACE_PATHS_PATH"
DEFAULT_WORKSPACE_PATHS = "~/.node-fable-mcp/workspace-paths"

# The two values a row's ``source_kind`` field can carry -- see the module
# docstring's "method alone is NOT a uniform reliability label" paragraph
# for what each one means and why the distinction exists.
SOURCE_KIND_LOCAL_STDIO = "local_stdio"
SOURCE_KIND_REMOTE_PROXY = "remote_proxy_snapshot"

# Directories never worth descending into: build/cache noise (never source),
# and test suites -- a test fixture's own throwaway @mcp.tool()-shaped stub
# must never be counted as a real registered tool.
_NOISE_DIR_NAMES = {
    "__pycache__", ".git", ".venv", "venv", "node_modules", ".pytest_cache",
    "tests", "test", "evals", ".mypy_cache", ".ruff_cache", "dist", "build",
    ".idea", ".vscode",
}


def _resolved_path(env_var: str, default: str) -> Path:
    raw = os.environ.get(env_var, "").strip() or default
    return Path(os.path.expanduser(raw))


def workspace_paths_file() -> Path:
    return _resolved_path(ENV_WORKSPACE_PATHS, DEFAULT_WORKSPACE_PATHS)


def load_workspace_registry() -> dict[str, Path]:
    """repo-key -> absolute local Path, from the plain ``key=/abs/path``
    lines only. Metadata lines (``key_tier=project``, ``key_authors=...``)
    never carry a value starting with ``/`` so they are excluded by
    construction, not by matching on the ``_suffix`` in the key name --
    a key can legitimately contain no underscore at all (e.g.
    ``node-kb-mcp``) so filtering on the VALUE shape is the only reliable
    discriminator. Missing/malformed file fails SAFE: empty registry,
    never a crash (mirrors mcp_grant.py's own disk-read posture)."""
    registry: dict[str, Path] = {}
    try:
        text = workspace_paths_file().read_text(encoding="utf-8")
    except OSError:
        return registry
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and value.startswith("/"):
            registry[key] = Path(value)
    return registry


def resolve_server_root(
    name: str, cfg: dict, registry: dict[str, Path]
) -> Optional[Path]:
    """Best-effort local source directory to scan for a registered server.

    A stdio registration already carries its own entrypoint path in
    ``args`` -- the most reliable signal available, used first. Walk up
    from it to the nearest ancestor literally named ``services`` (the
    fleet's own convention: an entrypoint script at ``services/<name>.py``
    importing sibling logic from ``services/<pkg>/*.py``) so the scan
    reaches tool registrations that live in a DIFFERENT file than the
    entrypoint (e.g. node-git-tools-mcp registers its tools from
    ``services/git_tools_server/tools.py``, not from the ``server.py``
    named in ``args``). No such ancestor -- fall back to the entrypoint's
    own parent directory.

    An http/remote registration (or a stdio entry whose file is not
    actually on this disk) carries no local path at all; resolve one from
    the workspace registry instead, by exact name match or by a UNIQUE
    ``-<name>`` key suffix (the fleet's own project-key convention, e.g.
    ``tools-ticketing-mcp`` for server ``ticketing-mcp``). An ambiguous
    suffix match (more than one candidate) is treated the same as no
    match -- never guessed at; a wrong guess would silently attribute one
    server's tool surface to another server's grant decision.
    """
    args = cfg.get("args") or []
    if cfg.get("command") and args:
        entry = Path(os.path.expanduser(str(args[-1])))
        if entry.is_file():
            for ancestor in (entry.parent, *entry.parents):
                if ancestor.name == "services":
                    return ancestor
            return entry.parent
    if name in registry:
        return registry[name]
    suffix = f"-{name}"
    candidates = sorted({p for k, p in registry.items() if k.endswith(suffix)})
    if len(candidates) == 1:
        return candidates[0]
    return None


# --------------------------------------------------------------------------
# Static (source_read) classification.
# --------------------------------------------------------------------------

def _call_func_name(node: Optional[ast.AST]) -> Optional[str]:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _decorator_is_tool(dec: ast.AST) -> bool:
    """Matches ``@<anything>.tool(...)`` or bare ``@<anything>.tool`` --
    deliberately indifferent to the FastMCP instance's variable name
    (``mcp``, ``app``, ``server``, ...), which varies across the fleet."""
    if isinstance(dec, ast.Call):
        return _call_func_name(dec.func) == "tool"
    return _call_func_name(dec) == "tool"


def _decorator_annotations_kwarg(dec: ast.AST) -> Optional[ast.AST]:
    if not isinstance(dec, ast.Call):
        return None
    for kw in dec.keywords:
        if kw.arg == "annotations":
            return kw.value
    return None


def _decorator_name_kwarg(dec: ast.AST) -> Optional[str]:
    if not isinstance(dec, ast.Call):
        return None
    for kw in dec.keywords:
        if kw.arg == "name":
            try:
                value = ast.literal_eval(kw.value)
            except (ValueError, TypeError):
                return None
            return value if isinstance(value, str) else None
    return None


def _build_module_level_assigns(tree: ast.Module) -> dict[str, ast.AST]:
    """name -> assigned-value AST node, MODULE-LEVEL ONLY (``tree.body``
    direct children). Deliberately not a whole-file walk: a name assigned
    inside a function body could collide with an unrelated same-named
    module constant, and resolving the wrong one is a wrong-direction
    miss (could manufacture a false read-only grant). Restricting to
    module scope means a locally-scoped annotations constant is simply
    unresolved -- which fails CLOSED (unclassified), the safe direction."""
    assigns: dict[str, ast.AST] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            assigns[node.targets[0].id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            assigns[node.target.id] = node.value
    return assigns


def _resolve_dict_like(
    node: Optional[ast.AST], assigns: dict[str, ast.AST], seen: frozenset[str]
) -> Optional[dict]:
    """Best-effort static evaluation of an ``annotations=`` expression to a
    plain dict, handling the conventions actually observed in this fleet:
    a ``ToolAnnotations(...)`` call, a literal dict, a spread
    (``{**CONST, "title": ...}`` -- order-preserving, later keys win,
    matching Python's own dict-literal construction), and a Name reference
    to a module-level constant of any of those shapes (resolved
    recursively; ``seen`` guards against a self-referential cycle).
    Anything genuinely unresolvable returns ``None`` -- the caller then
    finds no boolean ``readOnlyHint`` and classifies unclassified, never
    guesses read-only."""
    if node is None:
        return None
    if isinstance(node, ast.Name):
        if node.id in seen or node.id not in assigns:
            return None
        return _resolve_dict_like(assigns[node.id], assigns, seen | {node.id})
    if isinstance(node, ast.Call):
        func_name = _call_func_name(node.func)
        if func_name and func_name.endswith("ToolAnnotations"):
            result: dict = {}
            for kw in node.keywords:
                if kw.arg is None:
                    continue
                try:
                    result[kw.arg] = ast.literal_eval(kw.value)
                except (ValueError, TypeError):
                    pass
            return result
        return None
    if isinstance(node, ast.Dict):
        result = {}
        for key_node, value_node in zip(node.keys, node.values):
            if key_node is None:  # **spread -- merge in encounter order,
                spread = _resolve_dict_like(value_node, assigns, seen)
                if spread:
                    result.update(spread)
                continue
            try:
                key = ast.literal_eval(key_node)
            except (ValueError, TypeError):
                continue
            try:
                result[key] = ast.literal_eval(value_node)
            except (ValueError, TypeError):
                nested = _resolve_dict_like(value_node, assigns, seen)
                if nested is not None:
                    result[key] = nested
        return result
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def classify_file(path: Path) -> list[tuple[str, Optional[bool]]]:
    """[(tool_name, readOnlyHint), ...] for every ``@<obj>.tool(...)``
    decorated function found ANYWHERE in the file (``ast.walk``, not just
    module top-level function defs) -- the fleet's own registration
    pattern often defines tools inside a ``register_tools(mcp)`` function
    body (node-git-tools-mcp), not at module scope. ``readOnlyHint`` is
    ``True``/``False`` when resolved to an actual boolean, else ``None``
    (fail-closed: absent annotations kwarg, no resolvable dict, or a
    non-boolean value all land here). A syntax error or unreadable file
    yields an empty list -- disclosed by the caller as a failed server,
    never raised past this function."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
        return []
    assigns = _build_module_level_assigns(tree)
    results: list[tuple[str, Optional[bool]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        tool_decs = [d for d in node.decorator_list if _decorator_is_tool(d)]
        if not tool_decs:
            continue
        dec = tool_decs[0]
        name = _decorator_name_kwarg(dec) or node.name
        ann_node = _decorator_annotations_kwarg(dec)
        resolved = _resolve_dict_like(ann_node, assigns, frozenset())
        hint = resolved.get("readOnlyHint") if isinstance(resolved, dict) else None
        results.append((name, hint if isinstance(hint, bool) else None))
    return results


def _iter_python_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if d not in _NOISE_DIR_NAMES and not d.startswith(".")
        ]
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                yield Path(dirpath) / fn


def source_kind_for(cfg: dict) -> str:
    """``SOURCE_KIND_LOCAL_STDIO`` or ``SOURCE_KIND_REMOTE_PROXY``, from the
    server's OWN registration -- the same ``type`` check
    ``measure_server_live()`` already uses to decide whether a live
    handshake is even attempted (``cfg.get("type") not in (None, "stdio")``
    means remote/http). Ties the disclosure to what the operator actually
    registered, which is the direct, cold-reader-legible fact the reliability
    gap in the module docstring is about -- not to which branch of
    ``resolve_server_root()`` happened to resolve a scan root, which is an
    implementation detail of HOW the root was found rather than WHAT is
    being measured."""
    return (
        SOURCE_KIND_REMOTE_PROXY
        if cfg.get("type") not in (None, "stdio")
        else SOURCE_KIND_LOCAL_STDIO
    )


def measure_server(name: str, cfg: dict, registry: dict[str, Path]) -> dict:
    """One census row via ``source_read`` -- the default, safe method.
    Never executes anything; every input is a file read.

    Counts by UNIQUE TOOL NAME, never by raw decorator-occurrence event --
    a scan root can genuinely contain more than one file that registers a
    tool of the same name (an app with a legacy/duplicate implementation
    left in its tree, as opposed to one file importing sibling logic from
    another; the fleet has both shapes). Deduping per-classification-
    bucket-only (dropping the classification once a name repeats within
    the SAME bucket) is not enough: a name seen as write in one file and
    unclassified in another would then appear in BOTH the write and
    unclassified name lists while ``tools_total`` stayed an event count
    -- an internal inconsistency in THIS module's own numbers (event
    count vs. deduped name-set sizes disagreeing) that mcp_grant.py's
    tools_total reconciliation would then flag as a shortfall it did not
    cause and cannot explain. Building one name->hint map up front makes
    ``tools_total`` exactly ``len(by_name)`` by construction, so that
    reconciliation can never fire from THIS module's own counting. A
    genuine cross-file conflict (same name, different resolved hint) is
    real ambiguity in the scanned source, not a producer bug -- resolved
    fail-closed (unclassified, denied) and named in
    ``conflicting_tool_names`` rather than silently picking either
    reading."""
    root = resolve_server_root(name, cfg, registry)
    if root is None or not root.is_dir():
        return {"name": name, "failed": True, "reason": "no_local_source_resolved"}
    files = list(_iter_python_files(root))
    by_name: dict[str, Optional[bool]] = {}
    conflicting: set[str] = set()
    for f in files:
        for tool_name, hint in classify_file(f):
            if tool_name not in by_name:
                by_name[tool_name] = hint
            elif by_name[tool_name] != hint:
                conflicting.add(tool_name)
                by_name[tool_name] = None  # ambiguous across sources -> fail closed
    if not by_name:
        return {"name": name, "failed": True, "reason": "no_tools_found_in_source"}
    write_names = sorted(n for n, h in by_name.items() if h is False)
    unclassified_names = sorted(n for n, h in by_name.items() if h is None)
    row = {
        "name": name,
        "tools_total": len(by_name),
        "read_only": sum(1 for h in by_name.values() if h is True),
        "write": len(write_names),
        "unclassified": len(unclassified_names),
        "write_tool_names": write_names,
        "unclassified_tool_names": unclassified_names,
        "method": "source_read",
        "source_kind": source_kind_for(cfg),
        "files_scanned": len(files),
        "source_root": str(root),
    }
    if conflicting:
        row["conflicting_tool_names"] = sorted(conflicting)
    return row


# --------------------------------------------------------------------------
# Live (tools_list) classification -- real, tested, not the default.
# --------------------------------------------------------------------------

def measure_server_live(name: str, cfg: dict, timeout: float = 20.0) -> dict:
    """A REAL MCP initialize + tools/list handshake over stdio -- the same
    JSON-RPC shape evals/verify_eval.py already uses safely against this
    app's own server. Only usable for a stdio (command-based) local
    registration; an http/remote server has no local process to spawn and
    is reported failed rather than attempted over the network (out of
    this method's scope -- source_read is the only path for those).

    Spawns with a MINIMAL explicit env (PATH only -- never the caller's
    inherited environment), communicates over stdin/stdout only, and is
    killed unconditionally in a ``finally``. NOT invoked by
    ``build_census()`` for any real fleet server by default -- see the
    module docstring for why. This function exists so the capability is
    real and tested (this module's own test file spawns a scratch fixture
    server with it), available to a caller that has independently vetted
    ONE specific server as safe via ``build_census(live_servers=...)``.
    """
    if cfg.get("type") not in (None, "stdio") or not cfg.get("command"):
        return {"name": name, "failed": True, "reason": "not_a_local_stdio_server"}
    command = str(cfg["command"])
    args = [str(a) for a in (cfg.get("args") or [])]
    try:
        proc = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        )
    except OSError as exc:
        return {"name": name, "failed": True, "reason": f"spawn_failed:{exc}"}
    try:
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "node-fable-census", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        try:
            out, _ = proc.communicate(
                "\n".join(json.dumps(m) for m in msgs) + "\n", timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return {"name": name, "failed": True, "reason": "timeout"}
    finally:
        if proc.poll() is None:
            proc.kill()

    tools: list[dict] = []
    for line in out.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("id") == 2 and isinstance(obj.get("result"), dict):
            tools = obj["result"].get("tools") or []
    if not tools:
        return {"name": name, "failed": True, "reason": "no_tools_in_response"}

    read_only = write = unclassified = 0
    write_names: list[str] = []
    unclassified_names: list[str] = []
    for t in tools:
        hint = (t.get("annotations") or {}).get("readOnlyHint")
        if hint is True:
            read_only += 1
        elif hint is False:
            write += 1
            write_names.append(t["name"])
        else:
            unclassified += 1
            unclassified_names.append(t["name"])
    return {
        "name": name,
        "tools_total": read_only + write + unclassified,
        "read_only": read_only,
        "write": write,
        "unclassified": unclassified,
        "write_tool_names": sorted(set(write_names)),
        "unclassified_tool_names": sorted(set(unclassified_names)),
        "method": "tools_list",
        # Always local_stdio: the guard at the top of this function already
        # refused any cfg whose type is not None/"stdio" before spawning --
        # a tools_list row is never reached for a remote registration.
        "source_kind": SOURCE_KIND_LOCAL_STDIO,
    }


# --------------------------------------------------------------------------
# Whole-fleet orchestration + persistence.
# --------------------------------------------------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def build_census(*, live_servers: frozenset[str] = frozenset()) -> dict:
    """Measure every server this node has registered (project scope +
    user scope, via ``mcp_grant.load_server_configs()`` -- reused rather
    than re-implemented, so the merge precedence and node-fable-mcp
    self-strip stay defined in exactly one place). ``live_servers`` names
    servers to measure via the real ``tools_list`` handshake instead of
    ``source_read``; empty by default -- see the module docstring for why
    that default is deliberate. Discloses what it covered: every
    registered name gets an entry in ``method_by_server``, and every one
    that could not be measured is named (never just counted) in both
    ``servers_failed`` and ``failure_reasons``. Every reached row also carries
    ``source_kind`` (``source_kind_for()``) -- for an http/remote
    registration, this function's ``source_read`` path measures the local
    git checkout as a PROXY for the tool surface actually served at the
    registered URL, never a guarantee about it (see the module docstring's
    "method alone is NOT a uniform reliability label" paragraph)."""
    registry = load_workspace_registry()
    servers = mcp_grant.load_server_configs()
    reached: list[dict] = []
    failed: list[str] = []
    failure_reasons: dict[str, str] = {}
    method_by_server: dict[str, str] = {}
    for name in sorted(servers):
        cfg = servers[name]
        row = (
            measure_server_live(name, cfg)
            if name in live_servers
            else measure_server(name, cfg, registry)
        )
        method_by_server[name] = row.get("method") or "unavailable"
        if row.get("failed"):
            failed.append(name)
            failure_reasons[name] = row["reason"]
            continue
        reached.append(row)
    return {
        "measured_at": _now_iso(),
        "servers_registered_total": len(servers),
        "servers_reached": reached,
        "servers_failed": sorted(failed),
        "failure_reasons": dict(sorted(failure_reasons.items())),
        "method_by_server": dict(sorted(method_by_server.items())),
    }


def write_census(census: Optional[dict] = None) -> Path:
    """Compute (or accept an already-built) census and persist it
    atomically to ``mcp_grant.census_path()`` -- the exact location
    ``mcp_grant.load_census()`` reads, resolved through mcp_grant's own
    env-var contract rather than a second, parallel one. Write-then-
    rename so a reader never observes a partially-written file."""
    data = census if census is not None else build_census()
    target = mcp_grant.census_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, target)
    return target


def main(argv: Optional[list[str]] = None) -> int:
    """CLI: measure the fleet (source_read only -- see module docstring),
    persist the census, print a receipt naming per-server real numbers so
    the receipt is a command's output, never a memory."""
    census = build_census()
    path = write_census(census)
    reached = census["servers_reached"]
    receipt = {
        "written_to": str(path),
        "measured_at": census["measured_at"],
        "servers_registered_total": census["servers_registered_total"],
        "servers_reached": len(reached),
        "servers_failed": len(census["servers_failed"]),
        "failure_reasons": census["failure_reasons"],
        "tools_total": sum(r["tools_total"] for r in reached),
        "tools_read_only": sum(r["read_only"] for r in reached),
        "tools_write": sum(r["write"] for r in reached),
        "tools_unclassified": sum(r["unclassified"] for r in reached),
        "servers_by_source_kind": {
            kind: sum(1 for r in reached if r["source_kind"] == kind)
            for kind in (SOURCE_KIND_LOCAL_STDIO, SOURCE_KIND_REMOTE_PROXY)
        },
        "per_server": [
            {
                "name": r["name"], "tools_total": r["tools_total"],
                "read_only": r["read_only"], "write": r["write"],
                "unclassified": r["unclassified"], "method": r["method"],
                "source_kind": r["source_kind"],
                "files_scanned": r.get("files_scanned"),
            }
            for r in reached
        ],
    }
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover -- exercised via the thin CLI
    sys.exit(main())
