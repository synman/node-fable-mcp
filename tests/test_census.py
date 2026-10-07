"""node_fable.census — the fleet MCP tool census producer (Task G follow-up).

No subprocess against any REAL fleet server anywhere in this file. The
``measure_server_live`` tests spawn only a scratch fixture script this file
writes itself.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

_SERVICES = Path(__file__).resolve().parent.parent / "services"
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from node_fable import census, mcp_grant


# --------------------------------------------------------------------------
# classify_file — static source-level classification
# --------------------------------------------------------------------------

def _write(path: Path, source: str) -> Path:
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def test_classify_inline_toolannotations_read_only(tmp_path):
    f = _write(tmp_path / "srv.py", """
        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, title="X"))
        def get_thing():
            pass
    """)
    assert census.classify_file(f) == [("get_thing", True)]


def test_classify_inline_toolannotations_write(tmp_path):
    f = _write(tmp_path / "srv.py", """
        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
        def delete_thing():
            pass
    """)
    assert census.classify_file(f) == [("delete_thing", False)]


def test_classify_absent_annotations_is_unclassified_fail_closed(tmp_path):
    """The fail-closed absent-annotation case the brief names explicitly:
    a bare @mcp.tool() with no annotations kwarg at all (node-git-tools-mcp's
    own real convention) must classify as unclassified, never read-only."""
    f = _write(tmp_path / "srv.py", """
        @mcp.tool()
        def git_status():
            pass
    """)
    assert census.classify_file(f) == [("git_status", None)]


def test_classify_non_boolean_readonlyhint_is_unclassified(tmp_path):
    f = _write(tmp_path / "srv.py", """
        @mcp.tool(annotations={"readOnlyHint": "sort-of"})
        def weird_tool():
            pass
    """)
    assert census.classify_file(f) == [("weird_tool", None)]


def test_classify_named_constant_reference(tmp_path):
    """node-extract-mcp's real convention: a module-level ToolAnnotations
    constant referenced by name in the decorator."""
    f = _write(tmp_path / "srv.py", """
        _LOCAL_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False)

        @mcp.tool(annotations=_LOCAL_READ)
        def extract_doc(path: str):
            pass
    """)
    assert census.classify_file(f) == [("extract_doc", True)]


def test_classify_dict_spread_read_only_with_title_first(tmp_path):
    """node-kb-mcp's real convention, title BEFORE the spread:
    annotations={"title": "...", **_READ_ONLY}."""
    f = _write(tmp_path / "srv.py", """
        _READ_ONLY = {"readOnlyHint": True, "destructiveHint": False}

        @mcp.tool(annotations={"title": "Search Knowledge", **_READ_ONLY})
        def kb_search(query: str):
            pass
    """)
    assert census.classify_file(f) == [("kb_search", True)]


def test_classify_dict_spread_write_different_object_name(tmp_path):
    """WRITE-shaped spread AND a non-'mcp' FastMCP variable name ('app')
    to prove the matcher is indifferent to the instance's identifier."""
    f = _write(tmp_path / "srv.py", """
        WRITE = {"readOnlyHint": False, "destructiveHint": True}

        @app.tool(annotations={**WRITE})
        def create_thing():
            pass
    """)
    assert census.classify_file(f) == [("create_thing", False)]


def test_classify_nested_function_scope_registration(tmp_path):
    """node-git-tools-mcp's real registration shape: the decorated function
    is defined INSIDE another function's body, not at module scope."""
    f = _write(tmp_path / "srv.py", """
        def register_tools(mcp):
            @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
            def git_status():
                pass
            @mcp.tool()
            def git_commit(message: str):
                pass
    """)
    result = dict(census.classify_file(f))
    assert result == {"git_status": True, "git_commit": None}


def test_classify_name_kwarg_overrides_function_name(tmp_path):
    f = _write(tmp_path / "srv.py", """
        @mcp.tool(name="custom_public_name",
                   annotations=ToolAnnotations(readOnlyHint=True))
        def _internal_fn():
            pass
    """)
    assert census.classify_file(f) == [("custom_public_name", True)]


def test_classify_ignores_non_tool_decorators(tmp_path):
    f = _write(tmp_path / "srv.py", """
        @app.route("/health")
        def health():
            pass

        @staticmethod
        def helper():
            pass
    """)
    assert census.classify_file(f) == []


def test_classify_malformed_file_returns_empty_not_raise(tmp_path):
    f = _write(tmp_path / "bad.py", "def broken(:\n    pass")
    assert census.classify_file(f) == []


def test_classify_missing_file_returns_empty(tmp_path):
    assert census.classify_file(tmp_path / "absent.py") == []


# --------------------------------------------------------------------------
# resolve_server_root
# --------------------------------------------------------------------------

def test_resolve_stdio_walks_up_to_services_ancestor(tmp_path):
    entry = tmp_path / "app" / "services" / "pkg" / "server.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("", encoding="utf-8")
    cfg = {"command": "python3", "args": [str(entry)]}
    root = census.resolve_server_root("x", cfg, {})
    assert root == entry.parent.parent  # .../app/services


def test_resolve_stdio_no_services_ancestor_falls_back_to_parent(tmp_path):
    entry = tmp_path / "app" / "main.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("", encoding="utf-8")
    cfg = {"command": "python3", "args": [str(entry)]}
    root = census.resolve_server_root("x", cfg, {})
    assert root == entry.parent


def test_resolve_missing_entrypoint_falls_through_to_registry(tmp_path):
    cfg = {"command": "python3", "args": [str(tmp_path / "gone.py")]}
    registry = {"x": tmp_path / "real-root"}
    (tmp_path / "real-root").mkdir()
    root = census.resolve_server_root("x", cfg, registry)
    assert root == tmp_path / "real-root"


def test_resolve_http_exact_registry_match(tmp_path):
    cfg = {"type": "http", "url": "https://x/y"}
    registry = {"node-kb-mcp": tmp_path / "kb"}
    assert census.resolve_server_root("node-kb-mcp", cfg, registry) == tmp_path / "kb"


def test_resolve_http_unique_suffix_registry_match(tmp_path):
    cfg = {"type": "http", "url": "https://x/y"}
    registry = {"tools-ticketing-mcp": tmp_path / "tickets"}
    assert census.resolve_server_root("ticketing-mcp", cfg, registry) == tmp_path / "tickets"


def test_resolve_ambiguous_suffix_match_returns_none(tmp_path):
    cfg = {"type": "http", "url": "https://x/y"}
    registry = {
        "team-a-widget-mcp": tmp_path / "a",
        "team-b-widget-mcp": tmp_path / "b",
    }
    assert census.resolve_server_root("widget-mcp", cfg, registry) is None


def test_resolve_no_match_returns_none():
    assert census.resolve_server_root("nowhere-mcp", {"type": "http"}, {}) is None


# --------------------------------------------------------------------------
# source_kind_for -- the method-alone-is-not-uniform-reliability disclosure
# (residual, from an earlier review)
# --------------------------------------------------------------------------

def test_source_kind_for_stdio_registration_is_local():
    cfg = {"type": "stdio", "command": "python3", "args": ["x.py"]}
    assert census.source_kind_for(cfg) == census.SOURCE_KIND_LOCAL_STDIO


def test_source_kind_for_untyped_registration_defaults_local():
    """No ``type`` key at all -- the real shape most stdio entries carry in
    ~/.claude.json (node-git-tools-mcp, node-kb-mcp's stdio siblings, ...).
    Absent must default the same way ``measure_server_live()``'s own guard
    already does (``cfg.get("type") not in (None, "stdio")``), not to
    remote."""
    cfg = {"command": "python3", "args": ["x.py"]}
    assert census.source_kind_for(cfg) == census.SOURCE_KIND_LOCAL_STDIO


def test_source_kind_for_http_registration_is_remote_proxy():
    cfg = {"type": "http", "url": "https://x/y"}
    assert census.source_kind_for(cfg) == census.SOURCE_KIND_REMOTE_PROXY


def test_load_workspace_registry_ignores_metadata_lines(tmp_path):
    wp = tmp_path / ".workspace-paths"
    wp.write_text(
        "# comment\n"
        "\n"
        "cdn-mcp=/Users/x/ws/cdn/mcp/cdn-mcp\n"
        "cdn-mcp_tier=application\n"
        "cdn-mcp_authors=team-a\n"
        "cdn-mcp_github=example-org/cdn-mcp\n",
        encoding="utf-8",
    )
    import os
    old = os.environ.get(census.ENV_WORKSPACE_PATHS)
    os.environ[census.ENV_WORKSPACE_PATHS] = str(wp)
    try:
        reg = census.load_workspace_registry()
    finally:
        if old is None:
            os.environ.pop(census.ENV_WORKSPACE_PATHS, None)
        else:
            os.environ[census.ENV_WORKSPACE_PATHS] = old
    assert reg == {"cdn-mcp": Path("/Users/x/ws/cdn/mcp/cdn-mcp")}


def test_load_workspace_registry_missing_file_fails_safe(tmp_path, monkeypatch):
    monkeypatch.setenv(census.ENV_WORKSPACE_PATHS, str(tmp_path / "absent"))
    assert census.load_workspace_registry() == {}


# --------------------------------------------------------------------------
# measure_server
# --------------------------------------------------------------------------

def _scratch_app(tmp_path: Path) -> Path:
    root = tmp_path / "app" / "services"
    root.mkdir(parents=True)
    _write(root / "reads.py", """
        _READ = {"readOnlyHint": True}

        @mcp.tool(annotations={**_READ})
        def get_a():
            pass

        @mcp.tool(annotations={**_READ})
        def get_b():
            pass
    """)
    _write(root / "writes.py", """
        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
        def delete_a():
            pass

        @mcp.tool()
        def mystery_tool():
            pass
    """)
    # noise: must never be counted
    (root / "tests").mkdir()
    _write(root / "tests" / "test_fixture.py", """
        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
        def decoy_write():
            pass
    """)
    pycache = root / "__pycache__"
    pycache.mkdir()
    _write(pycache / "reads.cpython-312.py", """
        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
        def decoy_cached():
            pass
    """)
    return root


def test_measure_server_full_row_and_excludes_noise_dirs(tmp_path):
    root = _scratch_app(tmp_path)
    cfg = {"command": "python3", "args": [str(root / "reads.py")]}
    row = census.measure_server("scratch-mcp", cfg, {})
    assert row["tools_total"] == 4
    assert row["read_only"] == 2
    assert row["write"] == 1
    assert row["unclassified"] == 1
    assert row["write_tool_names"] == ["delete_a"]
    assert row["unclassified_tool_names"] == ["mystery_tool"]
    assert row["method"] == "source_read"
    assert row["source_kind"] == census.SOURCE_KIND_LOCAL_STDIO
    assert row["files_scanned"] == 2  # reads.py + writes.py only
    assert "decoy_write" not in row["write_tool_names"]
    assert "decoy_cached" not in row["write_tool_names"]


def test_measure_server_root_none_reports_no_local_source():
    row = census.measure_server("ghost-mcp", {"type": "http"}, {})
    assert row == {"name": "ghost-mcp", "failed": True,
                    "reason": "no_local_source_resolved"}


def test_measure_server_http_registration_tags_source_kind_remote_proxy(tmp_path):
    """The finding this closes: an http/remote registration's ``method``
    reads identically to a local stdio row ("source_read"), but the root
    scanned came from a workspace-registry fallback, not the process's own
    args[] entrypoint -- a snapshot standing in for a deployed service this
    module never talks to, not a measurement of it. ``source_kind`` must
    say so even though every other field is shaped exactly like the local
    case."""
    root = _scratch_app(tmp_path)
    cfg = {"type": "http", "url": "https://mcp.example.test/scratch-mcp"}
    registry = {"scratch-mcp": root}
    row = census.measure_server("scratch-mcp", cfg, registry)
    assert row["method"] == "source_read"
    assert row["source_kind"] == census.SOURCE_KIND_REMOTE_PROXY
    assert row["tools_total"] == 4  # the classification itself is unaffected


def test_measure_server_dedupes_same_name_same_classification_across_files(tmp_path):
    """Two files legitimately registering the SAME tool name with the SAME
    classification (a sibling module re-exporting/re-registering) must
    count as ONE tool, not two -- tools_total stays a name count, never a
    decorator-occurrence event count."""
    root = tmp_path / "app" / "services"
    root.mkdir(parents=True)
    _write(root / "a.py", """
        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
        def shared_tool():
            pass
    """)
    _write(root / "b.py", """
        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
        def shared_tool():
            pass
    """)
    cfg = {"command": "python3", "args": [str(root / "a.py")]}
    row = census.measure_server("dup-mcp", cfg, {})
    assert row["tools_total"] == 1
    assert row["read_only"] == 1
    assert "conflicting_tool_names" not in row


def test_measure_server_cross_file_conflict_fails_closed_not_a_shortfall(tmp_path):
    """The real defect this fix closes (found against the live fleet,
    smoke-mcp): a tool name classified DIFFERENTLY across two files in
    the same scan root must be (a) counted exactly ONCE in tools_total --
    never inflating it into a phantom mcp_grant.py shortfall -- and (b)
    resolved fail-closed (unclassified, denied), never silently picking
    either file's reading, and (c) named so the ambiguity is visible."""
    root = tmp_path / "app" / "services"
    root.mkdir(parents=True)
    _write(root / "old_impl.py", """
        @mcp.tool()
        def start_run():
            pass
    """)
    _write(root / "new_impl.py", """
        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
        def start_run():
            pass

        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
        def get_status():
            pass
    """)
    cfg = {"command": "python3", "args": [str(root / "old_impl.py")]}
    row = census.measure_server("conflict-mcp", cfg, {})
    assert row["tools_total"] == 2  # start_run (once) + get_status
    assert row["read_only"] == 1
    assert row["write"] == 0
    assert row["unclassified"] == 1
    assert row["unclassified_tool_names"] == ["start_run"]
    assert row["write_tool_names"] == []
    assert row["conflicting_tool_names"] == ["start_run"]
    # tools_total must reconcile cleanly through generate_grant() -- the
    # exact end-to-end assertion that would have caught this defect.
    configs = {"conflict-mcp": {"command": "true"}}
    census_doc = {"servers_reached": [row], "servers_failed": []}
    grant = mcp_grant.generate_grant(census_doc, configs)
    assert grant["disclosure"]["servers_incomplete_classification"] == {}
    assert "conflict-mcp" in grant["mcpServers"]
    assert "mcp__conflict-mcp__start_run" in grant["deny"]
    assert "mcp__conflict-mcp__get_status" not in grant["deny"]


def test_measure_server_root_with_zero_tools_reports_reason(tmp_path):
    empty_root = tmp_path / "empty" / "services"
    empty_root.mkdir(parents=True)
    _write(empty_root / "nothing.py", "x = 1\n")
    cfg = {"command": "python3", "args": [str(empty_root / "nothing.py")]}
    row = census.measure_server("empty-mcp", cfg, {})
    assert row == {"name": "empty-mcp", "failed": True,
                    "reason": "no_tools_found_in_source"}


# --------------------------------------------------------------------------
# build_census / write_census — end to end, including the mcp_grant handoff
# --------------------------------------------------------------------------

def _isolate_registrations(tmp_path, monkeypatch, mcp_servers=None, claude_servers=None):
    mcp_path = tmp_path / "mcp.json"
    claude_path = tmp_path / "claude.json"
    mcp_path.write_text(json.dumps({"mcpServers": mcp_servers or {}}), encoding="utf-8")
    claude_path.write_text(json.dumps({"mcpServers": claude_servers or {}}), encoding="utf-8")
    monkeypatch.setenv(mcp_grant.ENV_MCP_JSON_PATH, str(mcp_path))
    monkeypatch.setenv(mcp_grant.ENV_CLAUDE_JSON_PATH, str(claude_path))


def test_build_census_discloses_registered_total_and_methods(tmp_path, monkeypatch):
    root = _scratch_app(tmp_path)
    _isolate_registrations(
        tmp_path, monkeypatch,
        mcp_servers={"scratch-mcp": {"command": "python3", "args": [str(root / "reads.py")]}},
        claude_servers={"ghost-mcp": {"type": "http", "url": "https://x"}},
    )
    monkeypatch.setenv(census.ENV_WORKSPACE_PATHS, str(tmp_path / "absent-workspace-paths"))
    result = census.build_census()
    assert result["servers_registered_total"] == 2
    assert [r["name"] for r in result["servers_reached"]] == ["scratch-mcp"]
    assert result["servers_failed"] == ["ghost-mcp"]
    assert result["failure_reasons"] == {"ghost-mcp": "no_local_source_resolved"}
    assert result["method_by_server"] == {
        "scratch-mcp": "source_read", "ghost-mcp": "unavailable",
    }
    # measured_at is a real UTC ISO-ish timestamp, not a placeholder
    assert result["measured_at"].endswith("Z")
    assert len(result["measured_at"]) == 20


def test_build_census_tags_source_kind_matching_registration_type(tmp_path, monkeypatch):
    """A reader must be able to compute a source_kind breakdown from
    servers_reached alone, and it must match each server's OWN registration
    -- not collapse into one undifferentiated "source_read" bucket (residual,
    SIGNIFICANT -- an earlier review)."""
    local_root = _scratch_app(tmp_path / "local")
    remote_root = _scratch_app(tmp_path / "remote")
    _isolate_registrations(
        tmp_path, monkeypatch,
        mcp_servers={
            "local-mcp": {"command": "python3", "args": [str(local_root / "reads.py")]},
        },
        claude_servers={
            "remote-mcp": {"type": "http", "url": "https://mcp.example.test/remote-mcp"},
        },
    )
    wp = tmp_path / ".workspace-paths"
    wp.write_text(f"remote-mcp={remote_root}\n", encoding="utf-8")
    monkeypatch.setenv(census.ENV_WORKSPACE_PATHS, str(wp))

    result = census.build_census()
    by_name = {r["name"]: r for r in result["servers_reached"]}
    assert set(by_name) == {"local-mcp", "remote-mcp"}
    assert by_name["local-mcp"]["method"] == "source_read"
    assert by_name["local-mcp"]["source_kind"] == census.SOURCE_KIND_LOCAL_STDIO
    assert by_name["remote-mcp"]["method"] == "source_read"
    assert by_name["remote-mcp"]["source_kind"] == census.SOURCE_KIND_REMOTE_PROXY
    # The reader-computed breakdown a cold reader would derive from the
    # persisted census -- exactly the count this residual's evidence quoted
    # (11 of 19 real reached rows) is the same shape as this fixture's 1-of-2.
    counts = {}
    for r in result["servers_reached"]:
        counts[r["source_kind"]] = counts.get(r["source_kind"], 0) + 1
    assert counts == {
        census.SOURCE_KIND_LOCAL_STDIO: 1,
        census.SOURCE_KIND_REMOTE_PROXY: 1,
    }


def test_write_census_persists_to_mcp_grant_census_path(tmp_path, monkeypatch):
    census_path = tmp_path / "out" / "census.json"
    monkeypatch.setenv(mcp_grant.ENV_CENSUS_PATH, str(census_path))
    data = {"servers_reached": [], "servers_failed": []}
    written = census.write_census(data)
    assert written == census_path
    assert json.loads(census_path.read_text(encoding="utf-8")) == data
    # no leftover temp file
    assert list(census_path.parent.iterdir()) == [census_path]


def test_real_measured_census_feeds_mcp_grant_fail_closed_end_to_end(tmp_path, monkeypatch):
    """The core cross-module contract this lane exists to prove: a
    genuinely-measured census (source_read, real classify_file logic) is
    truthful enough that mcp_grant's tools_total reconciliation NEVER
    fires against it -- because this producer always enumerates read_only
    + write + unclassified to exactly equal tools_total by construction."""
    root = _scratch_app(tmp_path)
    _isolate_registrations(
        tmp_path, monkeypatch,
        mcp_servers={"scratch-mcp": {"command": "python3", "args": [str(root / "reads.py")]}},
    )
    monkeypatch.setenv(census.ENV_WORKSPACE_PATHS, str(tmp_path / "absent-workspace-paths"))
    census_path = tmp_path / "mcp-tool-census.json"
    monkeypatch.setenv(mcp_grant.ENV_CENSUS_PATH, str(census_path))

    built = census.build_census()
    census.write_census(built)

    grant = mcp_grant.build_grant()
    assert grant["disclosure"]["census_present"] is True
    assert "scratch-mcp" in grant["mcpServers"]
    assert grant["disclosure"]["servers_incomplete_classification"] == {}
    assert grant["disclosure"]["servers_tools_total_unavailable"] == []
    assert "mcp__scratch-mcp__delete_a" in grant["deny"]
    assert "mcp__scratch-mcp__mystery_tool" in grant["deny"]
    assert not any(d.startswith("mcp__scratch-mcp__get_") for d in grant["deny"])
    assert grant["disclosure"]["tools_read_only_granted"] == 2


# --------------------------------------------------------------------------
# measure_server_live — a REAL handshake against a scratch fixture server
# (never a real fleet server).
# --------------------------------------------------------------------------

_FIXTURE_SERVER = """
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    mcp = FastMCP("census-fixture")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def read_thing():
        return "ok"

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    def write_thing():
        return "ok"

    @mcp.tool()
    def unclassified_thing():
        return "ok"

    if __name__ == "__main__":
        mcp.run()
"""


def test_measure_server_live_real_handshake_against_scratch_fixture(tmp_path):
    server = _write(tmp_path / "fixture_server.py", _FIXTURE_SERVER)
    cfg = {"type": "stdio", "command": sys.executable, "args": [str(server)]}
    row = census.measure_server_live("fixture-mcp", cfg, timeout=30.0)
    assert row["tools_total"] == 3
    assert row["read_only"] == 1
    assert row["write"] == 1
    assert row["unclassified"] == 1
    assert row["write_tool_names"] == ["write_thing"]
    assert row["unclassified_tool_names"] == ["unclassified_thing"]
    assert row["method"] == "tools_list"
    # A tools_list row is only ever reached for a local stdio server -- the
    # guard at the top of measure_server_live() refuses any other type
    # before spawning -- so source_kind is always local_stdio here too.
    assert row["source_kind"] == census.SOURCE_KIND_LOCAL_STDIO


def test_measure_server_live_rejects_http_config():
    row = census.measure_server_live("x", {"type": "http", "url": "https://x"})
    assert row == {"name": "x", "failed": True, "reason": "not_a_local_stdio_server"}


def test_measure_server_live_bad_command_reports_spawn_failed():
    cfg = {"command": "/no/such/binary-xyz", "args": []}
    row = census.measure_server_live("x", cfg)
    assert row["failed"] is True
    assert row["reason"].startswith("spawn_failed:")


def test_measure_server_live_timeout(tmp_path):
    hang = _write(tmp_path / "hang.py", "import time\ntime.sleep(30)\n")
    cfg = {"command": sys.executable, "args": [str(hang)]}
    row = census.measure_server_live("x", cfg, timeout=1.0)
    assert row == {"name": "x", "failed": True, "reason": "timeout"}
