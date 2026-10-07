"""Constructed argv — flag presence, guard-state tool sets, canary regression.

Pure build_argv tests: no subprocess, no cswap.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SERVICES = Path(__file__).resolve().parent.parent / "services"
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from node_fable import mcp_grant, runner
from node_fable.errors import FableError

# The always-on exclusion deny (Task G) — present in every generated
# --settings payload regardless of census content. Hardcoded here
# (independently of mcp_grant.EXCLUDED_SERVERS) so this file cannot pass
# vacuously if that set is ever weakened; it must independently agree with
# the canonical set, same discipline as the git-write deny tests below.
_ALWAYS_DENIED_SERVERS = [
    "mcp__claude-channel-watcher",
    "mcp__isaac-mcp",
    "mcp__node-federation-mcp",
    "mcp__node-whisper-mcp",
]
# Amended with the name deny (mcp_grant.NAME_DENIED_TOOLS): four read-only tools
# whose return carries process command lines and tmux pane text are denied by
# qualified name in EVERY payload whatever the census says. These payload-shape
# tests pinned a deny list of the four servers only, which was true before the
# name deny existed. Hardcoded independently of the constant, in the order
# generate_grant() emits (servers, then sorted qualified names), so this file
# cannot pass vacuously if that set is weakened.
_ALWAYS_DENIED_TOOLS = [
    "mcp__node-process-mcp__detect_orphans",
    "mcp__node-process-mcp__process_list",
    "mcp__node-process-mcp__system_health",
    "mcp__node-procpool-mcp__session_output",
]
_EMPTY_SETTINGS_JSON = json.dumps(
    {
        "disableAllHooks": True,
        "hooks": {},
        "permissions": {"deny": _ALWAYS_DENIED_SERVERS + _ALWAYS_DENIED_TOOLS},
    },
    separators=(",", ":"),
)


@pytest.fixture(autouse=True)
def _isolate_mcp_grant_paths(monkeypatch, tmp_path):
    """These argv tests assert exact literal shapes — they must not see
    THIS machine's real census/registration files (Task G made
    --mcp-config/--settings environment-dependent). Point every mcp_grant
    path at a guaranteed-empty scratch dir so the generated payloads stay
    deterministic (exclusion-only deny) regardless of what the live host
    has seeded at ~/.node-fable-mcp/mcp-tool-census.json or ~/ai/.mcp.json.
    """
    monkeypatch.setenv(mcp_grant.ENV_CENSUS_PATH, str(tmp_path / "no-census.json"))
    monkeypatch.setenv(mcp_grant.ENV_MCP_JSON_PATH, str(tmp_path / "no-mcp.json"))
    monkeypatch.setenv(mcp_grant.ENV_CLAUDE_JSON_PATH, str(tmp_path / "no-claude.json"))


def _argv(**kw):
    defaults = dict(
        account="canary@example.com",
        model="claude-fable-5-1",
        effort="high",
        budget_usd="5.00",
        confirmed=False,
        add_dirs=[],
        system_prompt_file="/scratch/system-prompt.md",
    )
    defaults.update(kw)
    return runner.build_argv(**defaults)


def _direct_argv(**kw):
    defaults = dict(
        model="claude-opus-5",
        effort="high",
        budget_usd="5.00",
        confirmed=False,
        add_dirs=[],
        system_prompt_file="/scratch/system-prompt.md",
    )
    defaults.update(kw)
    return runner.build_direct_argv(**defaults)


def test_required_flags_present():
    argv = _argv()
    for flag in ("--require-session", "--no-share", "--strict-mcp-config", "-p"):
        assert flag in argv
    # literal config payloads — no census on disk means no server is
    # granted, but the 4-server exclusion deny (Task G) is unconditional.
    i = argv.index("--mcp-config")
    assert argv[i + 1] == '{"mcpServers":{}}'
    i = argv.index("--settings")
    assert argv[i + 1] == _EMPTY_SETTINGS_JSON
    i = argv.index("--max-budget-usd")
    assert argv[i + 1] == "5.00"


def test_effort_passed_explicitly_even_at_default():
    # "high" IS the default for review roles — still explicit on argv.
    argv = _argv(effort="high")
    i = argv.index("--effort")
    assert argv[i + 1] == "high"


def test_brief_never_on_argv():
    brief = "SENTINEL-BRIEF-TEXT do not put me on argv"
    argv = _argv()
    assert all(brief not in a for a in argv)
    # and the prompt goes via file path, not inline
    i = argv.index("--system-prompt-file")
    assert argv[i + 1] == "/scratch/system-prompt.md"


def test_unarmed_toolset():
    argv = _argv(confirmed=False)
    i = argv.index("--allowedTools")
    j = argv.index("--disallowedTools")
    allowed = argv[i + 1:j]
    assert allowed == ["Read", "Grep", "Glob"]
    for tool in ("Edit", "Write", "NotebookEdit"):
        assert tool not in argv
    # 2026-09-08: no Bash allow rule of any shape unarmed — the four
    # `Bash(git <verb>:*)` prefix rules were dropped (could not reach a repo
    # from the scratch cwd; git reads go through the node-git-tools-mcp
    # grant). Pin the absence so they cannot creep back as a "fix".
    assert not any(a.startswith("Bash(") for a in allowed)


def test_armed_toolset_adds_write_tools():
    argv = _argv(confirmed=True)
    for tool in ("Edit", "Write", "NotebookEdit", "Bash"):
        assert tool in argv


# ── Reachability gap fix: --allowedTools carries the grant too ──────────
#
# Task G wired --mcp-config/--settings from mcp_grant.build_grant() but left
# --allowedTools with zero mcp__* entries — a server present in --mcp-config
# and absent from permissions.deny was still unreachable under -p, which is
# a positive allowlist. allowed_tools() now appends one bare mcp__<server>
# entry per granted server, sourced from build_grant()'s own output (never
# hand-typed). Live-verified 2026-09-08 (matched positive/negative control,
# node-extract-mcp): a server-level allow entry made its read-only tools
# callable while its one bare-exact-name-denied tool (extract_snow_
# attachments) stayed NOT_VISIBLE — deny overrides allow at the CC engine
# level, which is what makes this design safe.


def test_allowed_tools_appends_one_bare_name_per_granted_server():
    result = runner.allowed_tools(False, granted_servers=["node-kb-mcp", "ticketing-mcp"])
    assert result == runner.allowed_tools(False) + ["mcp__node-kb-mcp", "mcp__ticketing-mcp"]


def test_allowed_tools_no_grant_is_byte_identical_to_pre_grant_shape():
    """A caller passing no grant (or an empty one) gets exactly the old
    base list — no regression for the many callers/tests that predate
    server-level allow entries."""
    assert runner.allowed_tools(False, granted_servers=None) == runner.allowed_tools(False)
    assert runner.allowed_tools(False, granted_servers=[]) == runner.allowed_tools(False)
    assert runner.allowed_tools(True, granted_servers=[]) == runner.allowed_tools(True)


def test_allowed_tools_never_re_admits_a_wholesale_denied_server():
    """The scenario the brief calls out by name: the allow side must not
    accidentally re-admit something the deny side removed. mcp_grant.py's
    own producer-side invariant already guarantees a wholesale-denied
    server (bare mcp__<name> in `deny`) never reaches `mcpServers` — this
    is the CONSUMER-side defense-in-depth layer on top of that, exercised
    directly against an adversarial grant shape that should not arise from
    mcp_grant.py today but must still be refused here if it ever did."""
    result = runner.allowed_tools(
        False, granted_servers=["evil-mcp", "good-mcp"], denied=["mcp__evil-mcp"],
    )
    assert "mcp__evil-mcp" not in result
    assert "mcp__good-mcp" in result


@pytest.mark.parametrize("confirmed", [False, True])
def test_git_write_deny_present_in_both_guard_states(confirmed):
    argv = _argv(confirmed=confirmed)
    i = argv.index("--disallowedTools")
    tail = argv[i + 1:]
    for deny in (
        "Bash(git commit:*)",
        "Bash(git push:*)",
        "Bash(git -C * commit:*)",
        "Bash(git -C * push:*)",
        "Bash(sh -c *git commit*)",
        "Bash(sh -c *git push*)",
    ):
        assert deny in tail
    # commit and push must be denied SYMMETRICALLY across every pattern
    # variant — the review found push covered only in bare form, leaving
    # `git -C /repo push` and `sh -c 'git push'` open while the equivalent
    # commit shapes were closed.
    commit_variants = {d.replace("commit", "VERB") for d in tail if "commit" in d}
    push_variants = {d.replace("push", "VERB") for d in tail if "push" in d}
    assert commit_variants == push_variants


def test_git_write_deny_has_no_python_or_library_coverage_disclosed_gap():
    """Pins the honest, disclosed gap (an earlier review finding,
    the review notes, against the design's forbidden-behavior list):
    GIT_WRITE_DENY_TOOLS is an argv-pattern deny on the literal shell
    command only — it has ZERO coverage for a python-level or
    library-level commit (`python3 -c "...subprocess..."`, dulwich/
    GitPython calling Repo.index.commit()/porcelain.commit() directly).

    This is deliberately NOT closable by enumerating more patterns: once a
    call is armed, Bash is otherwise fully granted, so a finite pattern
    list only ever catches shapes its author thought of, never the
    unbounded remainder — see the module comment above GIT_WRITE_DENY_TOOLS
    and node-fable-mcp.py pitfall 3 for what actually bounds the risk
    (arming's own confirmed=True bar; implement's design intent of routing
    commits back to the caller; git_write_override as the one sanctioned
    path).

    This test exists so a future silent "closure" claim — a pattern added
    with the surrounding docs left saying "reconciled"/"proven" without
    the docs actually being read and updated — is caught immediately: it
    fails the moment anyone adds python/library coverage to the constant,
    which forces a deliberate look at the two docstrings and the tracker
    text this gap is cross-referenced against, rather than an incidental
    change.
    """
    for entry in runner.GIT_WRITE_DENY_TOOLS:
        lowered = entry.lower()
        for shape in ("python", "dulwich", "gitpython", "subprocess"):
            assert shape not in lowered, (
                f"{entry!r} appears to add python/library coverage — "
                "update the module comment above GIT_WRITE_DENY_TOOLS, "
                "node-fable-mcp.py pitfall 3, and the design's Explicitly "
                "Forbidden item 16 in the SAME change, then relax this "
                "assertion deliberately (never widen it silently)."
            )


def test_git_write_override_drops_deny():
    argv = _argv(git_write_override=True)
    assert "--disallowedTools" not in argv
    assert "Bash(git commit:*)" not in argv


def test_resume_re_passes_full_flag_set():
    """Flags/grants re-bind from argv on EVERY resume — a -r call carries
    the identical full flag set plus the session id."""
    fresh = _argv(confirmed=True, add_dirs=["/repo"])
    resumed = runner.build_argv(
        account="canary@example.com",
        model="claude-fable-5-1",
        effort="high",
        budget_usd="5.00",
        confirmed=True,
        add_dirs=["/repo"],
        system_prompt_file="/scratch/system-prompt.md",
        session_id="sess-123",
    )
    assert resumed[: len(fresh)] == fresh
    assert resumed[len(fresh):] == ["-r", "sess-123"]
    for flag in ("--system-prompt-file", "--disallowedTools", "--allowedTools",
                 "--max-budget-usd", "--effort"):
        assert flag in resumed


def test_fresh_mint_passes_session_id_flag():
    """A fresh call (no resume target) passes --session-id <uuid> — a
    genuine, distinct claude CLI flag from -r, giving the caller a known id
    BEFORE the subprocess exists. an earlier finding: a
    killed/timed-out subprocess never prints its own JSON, so without this
    the caller never learns an id to record or resume from."""
    argv = _argv(mint_session_id="minted-uuid-1")
    assert argv[-2:] == ["--session-id", "minted-uuid-1"]
    assert "-r" not in argv


def test_resume_and_mint_are_mutually_exclusive():
    """session_id (resume) and mint_session_id must never both land on
    argv — -r wins when both are somehow supplied, and --session-id is
    never appended alongside it. (Callers are expected to pass at most one;
    this pins the argv-builder's own priority so a future caller mistake
    fails toward the SAFER of the two readings — resuming the named
    session — rather than silently minting a second, unresumable one.)"""
    argv = _argv(session_id="sess-999", mint_session_id="minted-uuid-2")
    assert argv[-2:] == ["-r", "sess-999"]
    assert "--session-id" not in argv
    assert "minted-uuid-2" not in argv


def test_add_dirs_on_argv():
    argv = _argv(add_dirs=["/a", "/b"])
    idxs = [i for i, a in enumerate(argv) if a == "--add-dir"]
    assert [argv[i + 1] for i in idxs] == ["/a", "/b"]


def test_canary_argv_regression():
    """The exact canary argv shape ($0.557 two-word canary) must stay
    reproducible byte-for-byte. Any change here is a wire-contract change —
    bump deliberately, never incidentally."""
    expected = [
        "cswap", "run", "canary@example.com",
        "--require-session", "--no-share", "--",
        "-p",
        "--model", "claude-fable-5-1",
        "--output-format", "json",
        "--effort", "high",
        "--strict-mcp-config",
        "--mcp-config", '{"mcpServers":{}}',
        "--settings", _EMPTY_SETTINGS_JSON,
        "--max-budget-usd", "5.00",
        "--allowedTools",
        "Read", "Grep", "Glob",
        # ^ deliberate bump 2026-09-08: the four Bash(git <verb>:*) allow
        # rules dropped — prefix rules that never reached a repo from the
        # empty scratch cwd; git reads are the node-git-tools-mcp grant.
        "--disallowedTools",
        "Bash(git commit:*)", "Bash(git push:*)",
        "Bash(git -C * commit:*)", "Bash(git -C * push:*)",
        "Bash(sh -c *git commit*)", "Bash(sh -c *git push*)",
        # ^ deliberate bump 2026-09-07: push deny extended to the -C and
        # sh -c variants (review finding — commit/push asymmetry).
        "--system-prompt-file", "/scratch/system-prompt.md",
    ]
    assert _argv() == expected


def test_model_validation_three_ids():
    for m in ("claude-fable-5-1", "claude-opus-5", "claude-opus-4-8"):
        assert runner.validate_model(m) == m
    assert runner.validate_model(None) == "claude-fable-5-1"


@pytest.mark.parametrize("bad", ["claude-fable-5-0", "gpt-5", "opus", ""])
def test_model_validation_rejects_everything_else(bad):
    with pytest.raises(FableError) as ei:
        runner.validate_model(bad)
    assert ei.value.error_class == "invalid_model"


def test_effort_validation():
    assert runner.validate_effort("implement", None) == "medium"
    assert runner.validate_effort("plan", None) == "high"
    assert runner.validate_effort("plan", "xhigh") == "xhigh"
    with pytest.raises(FableError):
        runner.validate_effort("plan", "extreme")


# ── Task F: Opus-fallback direct-invoke argv ────────────────────────────
#
# The two fallback tiers (claude-opus-5, claude-opus-4-8) skip the cswap
# wrapper entirely; claude-fable-5-1 keeps using it unchanged. Both argv
# shapes share _claude_argv() — these tests prove neither path lost or
# gained an invariant in the split.


def test_direct_invoke_models_excludes_only_the_default():
    assert runner.DIRECT_INVOKE_MODELS == ("claude-opus-5", "claude-opus-4-8")
    assert runner.DEFAULT_MODEL not in runner.DIRECT_INVOKE_MODELS


def test_direct_argv_no_cswap_prefix():
    for model in runner.DIRECT_INVOKE_MODELS:
        argv = _direct_argv(model=model)
        assert argv[0] == "claude"
        assert argv[1] == "-p"
        assert "cswap" not in argv
        assert "run" not in argv
        assert "--require-session" not in argv
        assert "--no-share" not in argv


def test_fable_tier_still_cswap_wrapped_byte_unchanged():
    """claude-fable-5-1 must still emit the full cswap prefix, unchanged by
    the build_argv/build_direct_argv split (Task F Bounds row)."""
    argv = _argv(model="claude-fable-5-1", account="canary@example.com")
    assert argv[:6] == [
        "cswap", "run", "canary@example.com",
        "--require-session", "--no-share", "--",
    ]
    assert argv[6] == "-p"


def test_direct_path_preserves_launch_flags():
    argv = _direct_argv(effort="xhigh", budget_usd="7.25")
    assert "--strict-mcp-config" in argv
    i = argv.index("--mcp-config")
    assert argv[i + 1] == '{"mcpServers":{}}'
    i = argv.index("--settings")
    assert argv[i + 1] == _EMPTY_SETTINGS_JSON
    i = argv.index("--max-budget-usd")
    assert argv[i + 1] == "7.25"
    i = argv.index("--effort")
    assert argv[i + 1] == "xhigh"
    i = argv.index("--system-prompt-file")
    assert argv[i + 1] == "/scratch/system-prompt.md"


def test_direct_path_add_dir_roots():
    argv = _direct_argv(add_dirs=["/a", "/b"])
    idxs = [i for i, a in enumerate(argv) if a == "--add-dir"]
    assert [argv[i + 1] for i in idxs] == ["/a", "/b"]


@pytest.mark.parametrize("confirmed", [False, True])
def test_direct_path_git_write_deny_present(confirmed):
    argv = _direct_argv(confirmed=confirmed)
    i = argv.index("--disallowedTools")
    tail = argv[i + 1:]
    # Hardcoded literals (not runner.GIT_WRITE_DENY_TOOLS) — deliberately,
    # so this test cannot pass vacuously if that constant is ever weakened;
    # it must independently agree with the canonical set.
    for deny in (
        "Bash(git commit:*)",
        "Bash(git push:*)",
        "Bash(git -C * commit:*)",
        "Bash(git -C * push:*)",
        "Bash(sh -c *git commit*)",
        "Bash(sh -c *git push*)",
    ):
        assert deny in tail
    if confirmed:
        for tool in ("Edit", "Write", "NotebookEdit", "Bash"):
            assert tool in argv


def test_direct_path_git_write_override_drops_deny():
    argv = _direct_argv(git_write_override=True)
    assert "--disallowedTools" not in argv
    assert "Bash(git commit:*)" not in argv


def test_direct_path_resume_flag():
    argv = _direct_argv(session_id="sess-456")
    assert argv[-2:] == ["-r", "sess-456"]


def test_direct_path_mint_flag():
    """The Opus direct-invoke path shares _claude_argv with the cswap path
    (module docstring's own stated invariant) — the mint flag must be
    identical on both, not re-derived."""
    argv = _direct_argv(mint_session_id="minted-uuid-3")
    assert argv[-2:] == ["--session-id", "minted-uuid-3"]
    assert "cswap" not in argv


def test_active_account_email_returns_the_active_row():
    from types import SimpleNamespace

    snapshot = SimpleNamespace(accounts=[
        SimpleNamespace(email="a@x.com", is_active=False),
        SimpleNamespace(email="active@x.com", is_active=True),
        SimpleNamespace(email="c@x.com", is_active=False),
    ])
    assert runner._active_account_email(snapshot=snapshot) == "active@x.com"


def test_active_account_email_raises_when_none_active():
    from types import SimpleNamespace

    snapshot = SimpleNamespace(accounts=[
        SimpleNamespace(email="a@x.com", is_active=False),
        SimpleNamespace(email="b@x.com", is_active=False),
    ])
    with pytest.raises(FableError) as ei:
        runner._active_account_email(snapshot=snapshot)
    assert ei.value.error_class == "no_active_account"


# ── Task G: --mcp-config/--settings wiring end-to-end through runner.py ──
#
# The mcp_grant unit tests (test_mcp_grant.py) prove generate_grant() in
# isolation; these prove build_argv/build_direct_argv actually THREAD a
# real seeded grant through _claude_argv, on both the cswap-wrapped and
# the direct-invoke launch path.


def _seed_grant_files(monkeypatch, tmp_path):
    census_path = tmp_path / "census.json"
    mcp_path = tmp_path / "mcp.json"
    claude_path = tmp_path / "claude.json"
    census_path.write_text(json.dumps({
        "servers_reached": [
            # tools_total is present and honest on every row here — this
            # fixture exercises the threading of an already-clean grant
            # through build_argv/build_direct_argv, not mcp_grant's own
            # absent/malformed-tools_total handling (test_mcp_grant.py
            # covers that in isolation). An absent tools_total now denies
            # wholesale (residual, SIGNIFICANT: mcp_grant.py 225-231), so
            # a row here without one would silently empty mcpServers and
            # these tests would stop proving what they say they prove.
            {"name": "node-kb-mcp", "tools_total": 3, "read_only": 3,
             "write_tool_names": [], "unclassified_tool_names": []},
            {"name": "ticketing-mcp", "tools_total": 3, "read_only": 1,
             "write_tool_names": ["incidents_create"],
             "unclassified_tool_names": ["batch_request"]},
            # present in census AND has a real config below, but on the
            # fixed exclusion list — must not leak into mcpServers.
            {"name": "isaac-mcp", "tools_total": 1, "read_only": 0,
             "write_tool_names": [],
             "unclassified_tool_names": ["health_check"]},
        ],
        "servers_failed": [],
    }), encoding="utf-8")
    mcp_path.write_text(json.dumps({"mcpServers": {
        "node-kb-mcp": {"type": "http", "url": "https://example/kb-mcp"},
        "isaac-mcp": {"command": "/usr/bin/python3", "args": ["/srv/isaac-mcp.py"]},
    }}), encoding="utf-8")
    claude_path.write_text(json.dumps({"mcpServers": {
        "ticketing-mcp": {"command": "/usr/bin/python3", "args": ["/srv/ticketing-mcp.py"]},
    }}), encoding="utf-8")
    monkeypatch.setenv(mcp_grant.ENV_CENSUS_PATH, str(census_path))
    monkeypatch.setenv(mcp_grant.ENV_MCP_JSON_PATH, str(mcp_path))
    monkeypatch.setenv(mcp_grant.ENV_CLAUDE_JSON_PATH, str(claude_path))


def test_build_argv_threads_real_grant_through_mcp_config(monkeypatch, tmp_path):
    _seed_grant_files(monkeypatch, tmp_path)
    argv = _argv()
    i = argv.index("--mcp-config")
    mcp_config = json.loads(argv[i + 1])
    assert set(mcp_config["mcpServers"].keys()) == {"node-kb-mcp", "ticketing-mcp"}
    assert "isaac-mcp" not in mcp_config["mcpServers"]


def test_build_argv_threads_real_grant_through_settings_deny(monkeypatch, tmp_path):
    _seed_grant_files(monkeypatch, tmp_path)
    argv = _argv()
    i = argv.index("--settings")
    deny = set(json.loads(argv[i + 1])["permissions"]["deny"])
    assert "mcp__ticketing-mcp__incidents_create" in deny
    assert "mcp__ticketing-mcp__batch_request" in deny
    assert "mcp__isaac-mcp" in deny  # always-on exclusion, even though
    # isaac-mcp had a real config AND appeared in the census above.
    assert not any(d.startswith("mcp__node-kb-mcp__") for d in deny)


def test_build_direct_argv_also_threads_real_grant(monkeypatch, tmp_path):
    """The Opus direct-invoke path shares _claude_argv — it must not fall
    back to the pre-Task-G empty literal just because it skips the cswap
    wrapper prefix."""
    _seed_grant_files(monkeypatch, tmp_path)
    argv = _direct_argv()
    i = argv.index("--mcp-config")
    mcp_config = json.loads(argv[i + 1])
    assert "node-kb-mcp" in mcp_config["mcpServers"]


def test_build_argv_threads_real_grant_through_allowed_tools(monkeypatch, tmp_path):
    """The reachability fix, proven against the SAME seeded grant the two
    tests above already prove --mcp-config/--settings against — all three
    payloads must describe the identical grant."""
    _seed_grant_files(monkeypatch, tmp_path)
    argv = _argv()
    i = argv.index("--allowedTools")
    j = argv.index("--disallowedTools")
    allowed = argv[i + 1:j]
    assert "mcp__node-kb-mcp" in allowed
    assert "mcp__ticketing-mcp" in allowed
    # isaac-mcp is fixed-excluded (never in mcpServers) even though it had
    # a real config and appeared in the census — must never earn an allow.
    assert "mcp__isaac-mcp" not in allowed


def test_build_direct_argv_also_threads_real_grant_through_allowed_tools(monkeypatch, tmp_path):
    _seed_grant_files(monkeypatch, tmp_path)
    argv = _direct_argv()
    i = argv.index("--allowedTools")
    j = argv.index("--disallowedTools")
    allowed = argv[i + 1:j]
    assert "mcp__node-kb-mcp" in allowed
    assert "mcp__ticketing-mcp" in allowed
    assert "mcp__isaac-mcp" not in allowed


# ── MINOR finding: --mcp-config/--settings carry a fleet server's own ────
# registration verbatim (generate_grant forwards it with no content
# validation) straight onto argv, visible via `ps` to every process on the
# host — unlike the assembled prompt (system_prompt + brief), which is
# scrubbed in _attempt(). These prove the redaction chokepoint actually
# runs on both generated payloads, without refusing the call over a third
# party's config file.


def _seed_leaky_server(monkeypatch, tmp_path, planted):
    """One server registration whose env block carries a secret-pattern-
    shaped literal — the shape a future violation of the fleet's
    vault-only-auth convention would take."""
    census_path = tmp_path / "census.json"
    mcp_path = tmp_path / "mcp.json"
    claude_path = tmp_path / "claude.json"
    census_path.write_text(json.dumps({
        "servers_reached": [
            {"name": "leaky-mcp", "read_only": 1, "tools_total": 1,
             "write_tool_names": [], "unclassified_tool_names": []},
        ],
        "servers_failed": [],
    }), encoding="utf-8")
    mcp_path.write_text(json.dumps({"mcpServers": {
        "leaky-mcp": {
            "command": "/usr/bin/python3",
            "args": ["/srv/leaky-mcp.py"],
            "env": {"SOME_KEY": planted},
        },
    }}), encoding="utf-8")
    claude_path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    monkeypatch.setenv(mcp_grant.ENV_CENSUS_PATH, str(census_path))
    monkeypatch.setenv(mcp_grant.ENV_MCP_JSON_PATH, str(mcp_path))
    monkeypatch.setenv(mcp_grant.ENV_CLAUDE_JSON_PATH, str(claude_path))


# Disclosure: this file previously shipped
# test_mcp_config_secret_in_server_env_is_redacted_not_refused, asserting
# that a secret-shaped literal forwarded from a server's own `env` block
# landed REDACTED on --mcp-config and the call was NOT refused. That
# asserted the wrong posture — it directly contradicted the design's
# Explicitly Forbidden list ("No secret pattern hit is ever redacted-and-
# sent — a high-confidence hit refuses the call outright") and this app's
# own committed CLAUDE.md Safety section ("Secret-pattern hits REFUSE the
# call outright, never redact-and-send"), both binding and both predating
# the commit that added the redact posture. The three tests below replace
# it with the refuse posture those two documents require; the sibling
# test_settings_deny_list_unaffected_by_env_secret is removed with it — its
# whole premise (the call succeeds and returns a settings payload to
# inspect) no longer holds once the call refuses before building one.


def test_mcp_config_secret_in_server_env_refuses_the_call(monkeypatch, tmp_path):
    """A secret-shaped literal forwarded verbatim from a server's own `env`
    block REFUSES the whole call outright — never redacted-and-sent. This
    is the same scan_or_refuse posture the assembled prompt gets in
    _attempt(): --mcp-config is INPUT heading to the subprocess, exactly
    like the prompt, not the echoed-subprocess-OUTPUT case redact_secrets
    exists for."""
    planted = "AKIA" + "IOSFODNN7EXAMPLE"
    _seed_leaky_server(monkeypatch, tmp_path, planted)
    with pytest.raises(FableError) as ei:
        _argv()
    assert ei.value.error_class == "secret_refused"


def test_secret_refusal_names_server_and_withholds_value(monkeypatch, tmp_path):
    """The refusal must be actionable, not mysterious: it names the
    offending server registration so a caller knows exactly what to fix,
    and it never echoes the matched secret value."""
    planted = "AKIA" + "IOSFODNN7EXAMPLE"
    _seed_leaky_server(monkeypatch, tmp_path, planted)
    with pytest.raises(FableError) as ei:
        _argv()
    assert ei.value.extra.get("server") == "leaky-mcp"
    assert ei.value.extra.get("pattern_class") == "AWS access key id"
    assert "leaky-mcp" in ei.value.message
    assert planted not in ei.value.message
    assert planted not in json.dumps(ei.value.payload())


def test_mcp_config_clean_registration_builds_argv_normally(monkeypatch, tmp_path):
    """A registration carrying no secret-pattern hit is unaffected by the
    refusal gate — build_argv still succeeds and forwards the value
    verbatim (generate_grant does no content validation of its own; the
    refusal gate is the only thing standing between a clean value and
    argv, and it must stay out of the way of one)."""
    clean = "not-a-secret-value"
    _seed_leaky_server(monkeypatch, tmp_path, clean)
    argv = _argv()  # must not raise
    i = argv.index("--mcp-config")
    parsed = json.loads(argv[i + 1])
    assert parsed["mcpServers"]["leaky-mcp"]["env"]["SOME_KEY"] == clean


def test_minimal_env_carries_USER_for_keychain_credential_resolution(monkeypatch):
    """The spawn env must carry ``USER``.

    The claude CLI resolves its stored OAuth credentials through the macOS
    keychain, and that lookup is keyed on ``USER``. Without the variable the
    CLI does not error on the keychain — it returns a well-formed JSON result
    whose body is ``Not logged in · Please run /login`` at zero cost, which
    reads as an account/login problem and sends the reader to cswap, the
    allowlist, or the user's own login state. Measured 2026-09-08 by
    bisecting the parent env against a live probe: proxy/CA vars, XPC and
    session vars, ``LOGNAME``, ``SHELL`` and ``__CF_USER_TEXT_ENCODING`` each
    left the call unauthenticated; ``USER`` alone restored it.

    ``LOGNAME`` carries the same value and is deliberately NOT accepted as a
    substitute here — it was tested on its own and failed, so a future
    "simplification" that swaps one for the other must go red.
    """
    monkeypatch.setenv("USER", "someone")
    env = runner._minimal_env()
    assert env.get("USER") == "someone"


def test_minimal_env_still_resolves_USER_when_the_parent_env_lacks_it(monkeypatch):
    """A parent process with no ``USER`` (a launchd/daemon context) must not
    silently hand the subprocess an unauthenticated env — fall back to the
    home directory's basename rather than omitting the key."""
    monkeypatch.delenv("USER", raising=False)
    env = runner._minimal_env()
    assert env.get("USER")


def test_minimal_env_withholds_the_parent_process_environment(monkeypatch):
    """The USER addition must not become a slippery slope: the env is still
    an explicit allowlist, never a copy of os.environ, because the parent MCP
    server's env can carry tokens this call must never see."""
    monkeypatch.setenv("SOME_PRIVATE_TOKEN", "leak-me")
    env = runner._minimal_env()
    assert "SOME_PRIVATE_TOKEN" not in env
    assert set(env) <= {"HOME", "PATH", "USER", "TERM", "TMPDIR", "LANG", "LC_ALL"}


def test_default_max_budget_is_fifty_dollars(monkeypatch):
    """Spec: DEFAULT_MAX_BUDGET_USD = 50 for fable
    mcp invocations. The deployment env block sets no
    NODE_FABLE_MAX_BUDGET_USD, so the module default IS the live cap — pin
    it, and pin that the env var still overrides it."""
    from node_fable import config

    monkeypatch.delenv(config.ENV_MAX_BUDGET_USD, raising=False)
    assert config.DEFAULT_MAX_BUDGET_USD == "50.00"
    assert float(config.max_budget_usd()) == 50.0
    monkeypatch.setenv(config.ENV_MAX_BUDGET_USD, "7.5")
    assert float(config.max_budget_usd()) == 7.5


def test_default_timeout_is_one_hour(monkeypatch):
    """Spec: 3600s for all runs. A plan
    consult at high effort had just died at the old 900s default with 75
    turns and no plan text (the killed CLI never prints its JSON, so nothing
    is recoverable). The deployment env block sets no NODE_FABLE_TIMEOUT_S,
    so the module default IS the live kill — pin it, and pin that the env var
    still overrides it."""
    from node_fable import config

    monkeypatch.delenv(config.ENV_TIMEOUT_S, raising=False)
    assert config.DEFAULT_TIMEOUT_S == 3600
    assert config.timeout_s() == 3600.0
    monkeypatch.setenv(config.ENV_TIMEOUT_S, "120")
    assert config.timeout_s() == 120.0
