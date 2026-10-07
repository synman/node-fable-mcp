"""mcp_grant — read-only fleet MCP tool grant generation (Task G).

Pure generate_grant() tests plus load_census()/load_server_configs() disk
fail-safety. No subprocess, no cswap, no live tools/list call anywhere in
this file.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

_SERVICES = Path(__file__).resolve().parent.parent / "services"
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from node_fable import mcp_grant

# A minimal but representative fixture census: one fully read-only server,
# one server carrying both a write tool and an unclassified (no
# readOnlyHint) tool, one server that is on the fixed exclusion list
# despite being "reached", and one that never answered at all.
_CENSUS = {
    "servers_reached": [
        {
            "name": "node-kb-mcp",
            "tools_total": 3,
            "read_only": 3,
            "write": 0,
            "unclassified": 0,
            "write_tool_names": [],
            "unclassified_tool_names": [],
        },
        {
            "name": "ticketing-mcp",
            "tools_total": 3,
            "read_only": 1,
            "write": 1,
            "unclassified": 1,
            "write_tool_names": ["incidents_create"],
            "unclassified_tool_names": ["batch_request"],
        },
        {
            "name": "isaac-mcp",
            "tools_total": 41,
            "read_only": 0,
            "write": 0,
            "unclassified": 41,
            "write_tool_names": [],
            "unclassified_tool_names": ["health_check"],
        },
    ],
    "servers_failed": ["registry-mcp"],
}

_CONFIGS = {
    "node-kb-mcp": {"type": "http", "url": "https://example/kb-mcp"},
    "ticketing-mcp": {"command": "/usr/bin/python3", "args": ["/srv/ticketing-mcp.py"]},
    "isaac-mcp": {"command": "/usr/bin/python3", "args": ["/srv/isaac-mcp.py"]},
    # node-fable-mcp deliberately present here (as it would be in a real
    # ~/ai/.mcp.json, self-registered) to prove self-exclusion.
    "node-fable-mcp": {"command": "/usr/bin/python3", "args": ["/srv/node-fable-mcp.py"]},
}

_BARE_TOOL_RE = re.compile(r"^mcp__[A-Za-z0-9_.-]+(__[A-Za-z0-9_.-]+)?$")


def test_excluded_servers_never_in_generated_config():
    grant = mcp_grant.generate_grant(_CENSUS, _CONFIGS)
    for name in mcp_grant.EXCLUDED_SERVERS:
        assert name not in grant["mcpServers"]
    # isaac-mcp is BOTH excluded-by-spec AND present in this fixture's
    # census/configs — proving the exclusion wins even when a server would
    # otherwise be grantable.
    assert "isaac-mcp" not in grant["mcpServers"]
    assert "node-kb-mcp" in grant["mcpServers"]
    assert "ticketing-mcp" in grant["mcpServers"]


def test_write_tool_lands_in_deny_list():
    grant = mcp_grant.generate_grant(_CENSUS, _CONFIGS)
    assert "mcp__ticketing-mcp__incidents_create" in grant["deny"]


def test_unclassified_tool_denied_fail_closed():
    """A tool with NO readOnlyHint (unclassified) is denied exactly like a
    write tool — never treated as read-only by default."""
    grant = mcp_grant.generate_grant(_CENSUS, _CONFIGS)
    assert "mcp__ticketing-mcp__batch_request" in grant["deny"]


def test_read_only_tool_never_denied():
    """Positive control for the two tests above: node-kb-mcp is 100%
    read-only in this fixture and must contribute NOTHING to the deny
    list — proves the deny logic discriminates rather than denying every
    granted server's tools indiscriminately."""
    grant = mcp_grant.generate_grant(_CENSUS, _CONFIGS)
    assert not any(d.startswith("mcp__node-kb-mcp__") for d in grant["deny"])


def test_deny_entries_are_bare_exact_names():
    """No parentheses (call-time-only scoping) and no wildcard — the exact
    form measured (2026-09-07) to remove a tool from context entirely."""
    grant = mcp_grant.generate_grant(_CENSUS, _CONFIGS)
    assert grant["deny"], "fixture must produce at least one deny entry"
    for entry in grant["deny"]:
        assert "(" not in entry and ")" not in entry
        assert "*" not in entry
        assert _BARE_TOOL_RE.match(entry), entry


def test_excluded_servers_denied_by_bare_server_name():
    grant = mcp_grant.generate_grant(_CENSUS, _CONFIGS)
    for name in mcp_grant.EXCLUDED_SERVERS:
        assert f"mcp__{name}" in grant["deny"]


def test_excluded_deny_present_even_with_empty_census():
    """The 4-server exclusion deny is unconditional — present even when
    the census found nothing at all (the pre-regen / first-run state)."""
    grant = mcp_grant.generate_grant({"servers_reached": [], "servers_failed": []}, {})
    assert grant["mcpServers"] == {}
    # The name deny is unconditional too (amended: this assertion predated it), so
    # the deny set is the excluded servers plus exactly the name-denied tools.
    assert set(grant["deny"]) == (
        {f"mcp__{n}" for n in mcp_grant.EXCLUDED_SERVERS}
        | {
            "mcp__node-process-mcp__process_list",
            "mcp__node-process-mcp__detect_orphans",
            "mcp__node-process-mcp__system_health",
            "mcp__node-procpool-mcp__session_output",
        }
    )


def test_generator_discloses_measured_corpus():
    """The generator reports what it covered — servers granted, servers
    excluded (both by spec and found-but-excluded), servers that failed to
    answer, and per-class tool counts — never a silent partial."""
    grant = mcp_grant.generate_grant(_CENSUS, _CONFIGS)
    disclosure = grant["disclosure"]
    assert disclosure["servers_included"] == ["node-kb-mcp", "ticketing-mcp"]
    assert disclosure["servers_excluded_by_spec"] == sorted(mcp_grant.EXCLUDED_SERVERS)
    assert disclosure["servers_excluded_present_in_census"] == ["isaac-mcp"]
    assert disclosure["servers_failed"] == ["registry-mcp"]
    assert disclosure["tools_read_only_granted"] == 4  # 3 (kb) + 1 (tickets)
    # incidents_create + batch_request from the census, plus the 4 tools denied by
    # name whatever the census says (amended: this was 2 before the name deny).
    assert disclosure["tools_denied"] == 6
    assert disclosure["deny_entries"] == len(grant["deny"])


def test_server_missing_config_disclosed_not_silently_dropped():
    census = {
        "servers_reached": [
            {"name": "node-kb-mcp", "read_only": 3, "write_tool_names": [],
             "unclassified_tool_names": []},
        ],
        "servers_failed": [],
    }
    grant = mcp_grant.generate_grant(census, {})  # no configs available at all
    assert "node-kb-mcp" not in grant["mcpServers"]
    assert grant["disclosure"]["servers_missing_config"] == ["node-kb-mcp"]
    # A server with no registration is never connected, so its read-only
    # tools are never reachable — the "granted" count must not count them.
    assert grant["disclosure"]["tools_read_only_granted"] == 0


def test_disclosure_distinguishes_no_census_from_empty_census():
    """An empty-but-genuinely-measured census and a never-measured one both
    produce zero servers/zero counts — the same shape unless census_present
    tells them apart."""
    empty = {"servers_reached": [], "servers_failed": []}
    measured = mcp_grant.generate_grant(empty, {}, census_present=True)
    never_measured = mcp_grant.generate_grant(empty, {}, census_present=False)
    assert measured["disclosure"]["census_present"] is True
    assert never_measured["disclosure"]["census_present"] is False
    # Every other field is identical — census_present is the ONLY signal.
    m, n = dict(measured["disclosure"]), dict(never_measured["disclosure"])
    del m["census_present"], n["census_present"]
    assert m == n


def test_build_grant_discloses_census_presence_from_disk(tmp_path, monkeypatch):
    """build_grant() measures census_present against the real disk path —
    not inferred from the loaded census's content."""
    monkeypatch.setenv(mcp_grant.ENV_CENSUS_PATH, str(tmp_path / "absent.json"))
    monkeypatch.setenv(mcp_grant.ENV_MCP_JSON_PATH, str(tmp_path / "no-mcp.json"))
    monkeypatch.setenv(mcp_grant.ENV_CLAUDE_JSON_PATH, str(tmp_path / "no-claude.json"))
    absent_grant = mcp_grant.build_grant()
    assert absent_grant["disclosure"]["census_present"] is False

    census_path = tmp_path / "census.json"
    census_path.write_text(json.dumps({"servers_reached": [], "servers_failed": []}),
                            encoding="utf-8")
    monkeypatch.setenv(mcp_grant.ENV_CENSUS_PATH, str(census_path))
    present_grant = mcp_grant.build_grant()
    assert present_grant["disclosure"]["census_present"] is True


def test_self_server_never_granted_even_if_present():
    census = {
        "servers_reached": [
            {"name": "node-fable-mcp", "read_only": 2, "write_tool_names": [],
             "unclassified_tool_names": []},
        ],
        "servers_failed": [],
    }
    grant = mcp_grant.generate_grant(census, _CONFIGS)
    assert "node-fable-mcp" not in grant["mcpServers"]


def test_load_server_configs_strips_self(tmp_path, monkeypatch):
    """load_server_configs() is what actually reads .mcp.json/.claude.json
    on a real deployment; confirm the self-strip happens there too, not
    only when a census-derived name happens to hit generate_grant's own
    guard above."""
    mcp_path = tmp_path / "mcp.json"
    claude_path = tmp_path / "claude.json"
    mcp_path.write_text(json.dumps({"mcpServers": {
        "node-fable-mcp": {"command": "self"},
        "node-kb-mcp": {"type": "http", "url": "https://x"},
    }}), encoding="utf-8")
    claude_path.write_text(json.dumps({"mcpServers": {
        "node-vault-mcp": {"command": "vault"},
    }}), encoding="utf-8")
    monkeypatch.setenv(mcp_grant.ENV_MCP_JSON_PATH, str(mcp_path))
    monkeypatch.setenv(mcp_grant.ENV_CLAUDE_JSON_PATH, str(claude_path))
    cfgs = mcp_grant.load_server_configs()
    assert "node-fable-mcp" not in cfgs
    assert cfgs["node-kb-mcp"] == {"type": "http", "url": "https://x"}
    assert cfgs["node-vault-mcp"] == {"command": "vault"}


def test_load_census_missing_file_fails_safe(tmp_path, monkeypatch):
    monkeypatch.setenv(mcp_grant.ENV_CENSUS_PATH, str(tmp_path / "absent.json"))
    census = mcp_grant.load_census()
    assert census == {"servers_reached": [], "servers_failed": []}


def test_load_census_malformed_file_fails_safe(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("not json{{{", encoding="utf-8")
    monkeypatch.setenv(mcp_grant.ENV_CENSUS_PATH, str(bad))
    census = mcp_grant.load_census()
    assert census == {"servers_reached": [], "servers_failed": []}


def test_load_server_configs_missing_files_fails_safe(tmp_path, monkeypatch):
    monkeypatch.setenv(mcp_grant.ENV_MCP_JSON_PATH, str(tmp_path / "no-mcp.json"))
    monkeypatch.setenv(mcp_grant.ENV_CLAUDE_JSON_PATH, str(tmp_path / "no-claude.json"))
    assert mcp_grant.load_server_configs() == {}


def test_tools_total_shortfall_denies_server_wholesale():
    """Adversarial finding, BLOCKING — the exact reproduction: a census row
    that declares tools_total=50 but only enumerates 2 (read_only, no
    write/unclassified names) must NOT silently grant the other 48 as
    read-only by default. The whole server is denied instead."""
    census = {
        "servers_reached": [
            {"name": "evil-mcp", "tools_total": 50, "read_only": 2,
             "write_tool_names": [], "unclassified_tool_names": []},
        ],
        "servers_failed": [],
    }
    configs = {"evil-mcp": {"command": "/usr/bin/python3", "args": ["/srv/evil-mcp.py"]}}
    grant = mcp_grant.generate_grant(census, configs)
    assert "evil-mcp" not in grant["mcpServers"]
    assert "mcp__evil-mcp" in grant["deny"]
    assert grant["disclosure"]["servers_incomplete_classification"] == {"evil-mcp": 48}
    # A wholesale-denied server contributes nothing to the granted count —
    # it was never connected.
    assert grant["disclosure"]["tools_read_only_granted"] == 0


def test_tools_total_shortfall_disclosure_names_every_incomplete_server():
    """Two servers, two different shortfalls — the disclosure must name
    both, not just report that *a* shortfall happened somewhere."""
    census = {
        "servers_reached": [
            {"name": "evil-one", "tools_total": 10, "read_only": 1,
             "write_tool_names": [], "unclassified_tool_names": []},
            {"name": "evil-two", "tools_total": 5, "read_only": 0,
             "write_tool_names": ["w"], "unclassified_tool_names": []},
        ],
        "servers_failed": [],
    }
    configs = {
        "evil-one": {"command": "true"},
        "evil-two": {"command": "true"},
    }
    grant = mcp_grant.generate_grant(census, configs)
    assert grant["mcpServers"] == {}
    assert grant["disclosure"]["servers_incomplete_classification"] == {
        "evil-one": 9,   # 10 - (1 read_only + 0 named)
        "evil-two": 4,   # 5 - (0 read_only + 1 named)
    }


def test_tools_total_matching_enumeration_still_grants():
    """Positive control for the two tests above: a row whose declared
    tools_total exactly accounts for read_only + both name lists is a
    clean reconciliation and must be granted normally — the check
    discriminates a real shortfall, it does not deny every row that
    carries a tools_total field."""
    census = {
        "servers_reached": [
            {"name": "clean-mcp", "tools_total": 3, "read_only": 2,
             "write_tool_names": ["w"], "unclassified_tool_names": []},
        ],
        "servers_failed": [],
    }
    configs = {"clean-mcp": {"command": "true"}}
    grant = mcp_grant.generate_grant(census, configs)
    assert "clean-mcp" in grant["mcpServers"]
    assert grant["disclosure"]["servers_incomplete_classification"] == {}
    assert grant["disclosure"]["tools_read_only_granted"] == 2


def test_tools_total_overcount_not_treated_as_shortfall():
    """A row whose tools_total is SMALLER than what it enumerated (a
    double-listed tool, or a stale/wrong total) is a census data-quality
    issue, not the security-relevant direction this check exists for —
    every tool the row names is already accounted for by read_only plus
    the two name lists, so nothing ungoverned can reach the subprocess.
    Only a POSITIVE shortfall (tools_total > enumerated) denies wholesale."""
    census = {
        "servers_reached": [
            {"name": "overcounted-mcp", "tools_total": 1, "read_only": 2,
             "write_tool_names": [], "unclassified_tool_names": []},
        ],
        "servers_failed": [],
    }
    configs = {"overcounted-mcp": {"command": "true"}}
    grant = mcp_grant.generate_grant(census, configs)
    assert "overcounted-mcp" in grant["mcpServers"]
    assert grant["disclosure"]["servers_incomplete_classification"] == {}


# ── Residual 1 (SIGNIFICANT, mcp_grant.py 225-231): an absent/null/non-int
# tools_total used to grant a server through the two name lists alone —
# functionally the same fail-open bypass the BLOCKING finding closed for a
# numerically-inconsistent tools_total, just reached by never populating
# the field at all. ASSERTION CHANGE, disclosed: the test this replaces —
# test_tools_total_absent_still_granted_but_disclosed_unverifiable —
# asserted `"legacy-mcp" in grant["mcpServers"]` for exactly this shape.
# That assertion is now inverted: option (a) from the residual's own two
# choices was taken (fail closed on absence, matching the positive-
# shortfall branch) rather than (b) (stay granted, add a test merely
# proving the exposure). The producer (census.py) always emits tools_total
# as a real int, so (b)'s backward-compatibility case was already a
# hypothetical hand-written census; keeping the disclosure-only posture
# would have meant this module's own docstring claim of failing closed
# stayed contradicted by its own second layer.


def test_tools_total_absent_denied_wholesale_not_silently_granted():
    """A row that omits tools_total entirely can no longer be reconciled
    the "fine, just disclose it" way — it is now denied wholesale, exactly
    like a positive shortfall, and the gap is still named in its own
    disclosure field (never merged into servers_incomplete_classification,
    which is reserved for a COMPUTED shortfall)."""
    census = {
        "servers_reached": [
            {"name": "legacy-mcp", "read_only": 3,
             "write_tool_names": [], "unclassified_tool_names": []},
        ],
        "servers_failed": [],
    }
    configs = {"legacy-mcp": {"command": "true"}}
    grant = mcp_grant.generate_grant(census, configs)
    assert "legacy-mcp" not in grant["mcpServers"]
    assert "mcp__legacy-mcp" in grant["deny"]
    assert grant["disclosure"]["servers_tools_total_unavailable"] == ["legacy-mcp"]
    assert grant["disclosure"]["servers_incomplete_classification"] == {}
    assert grant["disclosure"]["tools_read_only_granted"] == 0


def test_tools_total_json_null_denied_wholesale_same_as_absent():
    """JSON null (Python None) is a distinct wire shape from a missing key
    — both must collapse to the identical fail-closed outcome, not just
    the missing-key case."""
    census = {
        "servers_reached": [
            {"name": "legacy-mcp", "tools_total": None, "read_only": 3,
             "write_tool_names": [], "unclassified_tool_names": []},
        ],
        "servers_failed": [],
    }
    configs = {"legacy-mcp": {"command": "true"}}
    grant = mcp_grant.generate_grant(census, configs)
    assert "legacy-mcp" not in grant["mcpServers"]
    assert grant["disclosure"]["servers_tools_total_unavailable"] == ["legacy-mcp"]


def test_tools_total_non_integer_denied_wholesale_same_as_absent():
    """A stringified count (the malformed shape residual 2 measured live:
    tools_total='50' raised TypeError) must be treated the same as absent
    — never coerced, never fed to the shortfall arithmetic."""
    census = {
        "servers_reached": [
            {"name": "legacy-mcp", "tools_total": "50", "read_only": 3,
             "write_tool_names": [], "unclassified_tool_names": []},
        ],
        "servers_failed": [],
    }
    configs = {"legacy-mcp": {"command": "true"}}
    grant = mcp_grant.generate_grant(census, configs)
    assert "legacy-mcp" not in grant["mcpServers"]
    assert grant["disclosure"]["servers_tools_total_unavailable"] == ["legacy-mcp"]


def test_tools_total_absent_adversarial_underreport_exposes_nothing():
    """The exact adversarial shape the residual named: an under-reporting
    row (real tool count is much larger than what read_only + the two name
    lists disclose) that simply OMITS tools_total. Before this fix this
    shape reproduced the BLOCKING bypass under a different lever — a
    truthful absent-tools_total row was the only shape the old suite
    exercised, so it never caught this. Now the server must not be granted
    at all, so the hidden tools are never reachable regardless of how many
    there really are."""
    census = {
        "servers_reached": [
            # claims 2 read-only tools and nothing else; a real server
            # could carry 50 more the census never saw or never named.
            {"name": "evil-mcp", "read_only": 2,
             "write_tool_names": [], "unclassified_tool_names": []},
        ],
        "servers_failed": [],
    }
    configs = {"evil-mcp": {"command": "/usr/bin/python3", "args": ["/srv/evil-mcp.py"]}}
    grant = mcp_grant.generate_grant(census, configs)
    assert grant["mcpServers"] == {}
    assert "mcp__evil-mcp" in grant["deny"]
    assert grant["disclosure"]["servers_tools_total_unavailable"] == ["evil-mcp"]
    assert grant["disclosure"]["tools_read_only_granted"] == 0


# ── Residual 2 (SIGNIFICANT, mcp_grant.py 204, 233): unguarded arithmetic
# and attribute access on census row content raised uncaught out of
# generate_grant() for a structurally-valid-but-malformed row, taking down
# every fable_consult launch through the shared _claude_argv() call site
# (runner.py, no surrounding except) — not merely the affected server's
# own grant. These prove a malformed servers_reached entry is skipped and
# named, never left to raise.


def test_servers_reached_non_dict_entries_skipped_and_disclosed():
    """The three shapes measured live to raise AttributeError
    ('not-a-dict' -> str, None -> NoneType, 42 -> int), mixed with one
    genuinely valid row that must still be processed normally — a
    malformed entry must not derail the rows around it."""
    census = {
        "servers_reached": [
            "not-a-dict",
            None,
            42,
            {"name": "clean-mcp", "tools_total": 1, "read_only": 1,
             "write_tool_names": [], "unclassified_tool_names": []},
        ],
        "servers_failed": [],
    }
    configs = {"clean-mcp": {"command": "true"}}
    grant = mcp_grant.generate_grant(census, configs)
    assert "clean-mcp" in grant["mcpServers"]
    assert grant["disclosure"]["servers_malformed"] == [
        "index 0: str", "index 1: NoneType", "index 2: int",
    ]


def test_servers_malformed_entries_never_raise():
    """The load-bearing claim itself: calling generate_grant() on a
    malformed row must not raise at all — this is what makes the fix a
    fix, not just a disclosure improvement. Runs each of the three
    measured shapes as the ONLY row, alone, so no valid row masks a
    residual crash."""
    configs: dict = {}
    for bad_row in ("not-a-dict", None, 42):
        census = {"servers_reached": [bad_row], "servers_failed": []}
        grant = mcp_grant.generate_grant(census, configs)  # must not raise
        assert grant["mcpServers"] == {}
        assert len(grant["disclosure"]["servers_malformed"]) == 1


def test_mcp_config_json_only_carries_mcpservers_key():
    grant = mcp_grant.generate_grant(_CENSUS, _CONFIGS)
    payload = json.loads(mcp_grant.mcp_config_json(grant))
    assert set(payload.keys()) == {"mcpServers"}
    assert set(payload["mcpServers"].keys()) == {"node-kb-mcp", "ticketing-mcp"}


def test_settings_json_carries_hooks_and_deny():
    grant = mcp_grant.generate_grant(_CENSUS, _CONFIGS)
    payload = json.loads(mcp_grant.settings_json(grant))
    assert payload["hooks"] == {}
    assert set(payload["permissions"]["deny"]) == set(grant["deny"])


def test_settings_json_disables_all_hooks_per_run():
    """`hooks: {}` merges additively and suppresses nothing; the user-scope
    PreToolUse hooks ran inside the paid subprocess on the direct launch
    path and denied Grep + every shell git read (measured 2026-09-08).
    `disableAllHooks: true` is the documented per-run switch — pin it as a
    literal boolean True, never a truthy string, and pin the payload's exact
    key set so a future edit cannot drop it while keeping `hooks: {}`."""
    grant = mcp_grant.generate_grant(_CENSUS, _CONFIGS)
    payload = json.loads(mcp_grant.settings_json(grant))
    assert payload["disableAllHooks"] is True
    assert set(payload.keys()) == {"disableAllHooks", "hooks", "permissions"}


def test_build_grant_reads_from_env_resolved_paths(tmp_path, monkeypatch):
    """build_grant() ties load_census() + load_server_configs() together —
    end-to-end through the disk, not just generate_grant() called directly."""
    census_path = tmp_path / "census.json"
    mcp_path = tmp_path / "mcp.json"
    claude_path = tmp_path / "claude.json"
    census_path.write_text(json.dumps({
        "servers_reached": [
            # tools_total present and honest — this test proves build_grant()
            # wires load_census()/load_server_configs() together end-to-end,
            # not mcp_grant's own absent-tools_total handling (covered in
            # isolation below); an absent field here would now deny this
            # server wholesale (residual, SIGNIFICANT: mcp_grant.py 225-231)
            # and stop this test proving what it says it proves.
            {"name": "node-kb-mcp", "tools_total": 2, "read_only": 2,
             "write_tool_names": [], "unclassified_tool_names": []},
        ],
        "servers_failed": [],
    }), encoding="utf-8")
    mcp_path.write_text(json.dumps({"mcpServers": {
        "node-kb-mcp": {"type": "http", "url": "https://x"},
    }}), encoding="utf-8")
    claude_path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    monkeypatch.setenv(mcp_grant.ENV_CENSUS_PATH, str(census_path))
    monkeypatch.setenv(mcp_grant.ENV_MCP_JSON_PATH, str(mcp_path))
    monkeypatch.setenv(mcp_grant.ENV_CLAUDE_JSON_PATH, str(claude_path))
    grant = mcp_grant.build_grant()
    assert grant["mcpServers"] == {"node-kb-mcp": {"type": "http", "url": "https://x"}}


# ── Name deny: read-only tools whose RETURN carries secrets (NAME_DENIED_TOOLS) ──

# Rows as a re-measured census would report them once the servers' read-only
# annotations landed: the census row carries a read_only COUNT, never the
# read-only names, so these six and four tools are "listed as read-only" only by
# being absent from the write and unclassified lists.
_PROCESS_ROW = {
    "name": "node-process-mcp", "tools_total": 9, "read_only": 6, "write": 3, "unclassified": 0,
    "write_tool_names": ["process_kill", "kill_by_pattern", "cleanup_orphans"],
    "unclassified_tool_names": [],
}
_PROCPOOL_ROW = {
    "name": "node-procpool-mcp", "tools_total": 8, "read_only": 4, "write": 4, "unclassified": 0,
    "write_tool_names": ["start_session", "stop_session", "destroy_pipe", "create_pipe"],
    "unclassified_tool_names": [],
}
_OTHER_ROW = {
    "name": "other-mcp", "tools_total": 3, "read_only": 3, "write": 0, "unclassified": 0,
    "write_tool_names": [], "unclassified_tool_names": [],
}
_NAME_DENIED = {
    "mcp__node-process-mcp__process_list",
    "mcp__node-process-mcp__detect_orphans",
    "mcp__node-process-mcp__system_health",
    "mcp__node-procpool-mcp__session_output",
}
_NAME_DENY_CONFIGS = {
    "node-process-mcp": {"command": "/usr/bin/python3", "args": ["/srv/process.py"]},
    "node-procpool-mcp": {"command": "/usr/bin/python3", "args": ["/srv/procpool.py"]},
    "other-mcp": {"command": "/usr/bin/python3", "args": ["/srv/other.py"]},
}


def test_name_deny_module_is_the_one_under_test():
    """The module measured is this tree's own, so a scratch copy of the tree is
    the copy a mutation is applied to."""
    assert _SERVICES in Path(mcp_grant.__file__).resolve().parents


def test_name_denied_set_is_pinned():
    assert set(mcp_grant.NAME_DENIED_TOOLS) == _NAME_DENIED
    assert len(mcp_grant.NAME_DENIED_TOOLS) == len(_NAME_DENIED)


def test_read_only_census_cannot_grant_a_name_denied_tool():
    census = {"servers_reached": [_PROCESS_ROW, _PROCPOOL_ROW, _OTHER_ROW], "servers_failed": []}
    grant = mcp_grant.generate_grant(census, _NAME_DENY_CONFIGS)
    assert _NAME_DENIED <= set(grant["deny"])
    d = grant["disclosure"]
    # Both servers stay granted (their other read-only tools are fine) ...
    assert {"node-process-mcp", "node-procpool-mcp", "other-mcp"} <= set(d["servers_included"])
    # ... but the four come out of the granted count: (6-3) + (4-1) + 3.
    assert d["tools_read_only_granted"] == 3 + 3 + 3
    # And are counted as denied by name: 3 census writes + 4 census writes + 4 by name.
    assert d["tools_denied_by_name"] == sorted(_NAME_DENIED)
    assert d["tools_denied"] == 3 + 4 + 4
    assert d["deny_entries"] == len(grant["deny"])


def test_name_deny_is_qualified_so_a_same_named_tool_elsewhere_is_not_caught():
    census = {"servers_reached": [_OTHER_ROW], "servers_failed": []}
    grant = mcp_grant.generate_grant(census, _NAME_DENY_CONFIGS)
    assert not any(d.startswith("mcp__other-mcp__") for d in grant["deny"])
    assert grant["disclosure"]["tools_read_only_granted"] == 3  # unchanged by the name deny
    assert all(d.count("__") == 2 for d in _NAME_DENIED)  # qualified: server and tool


def test_name_deny_applies_with_no_census_at_all():
    grant = mcp_grant.generate_grant({"servers_reached": [], "servers_failed": []}, {}, census_present=False)
    assert _NAME_DENIED <= set(grant["deny"])


def test_name_deny_is_not_double_counted_when_the_census_already_denies_it():
    """The stale census on this node lists these tools as write or unclassified, so
    they are already denied and must be neither duplicated nor taken out of a
    read-only count they were never in."""
    row = dict(
        _PROCESS_ROW, read_only=4, write=3, unclassified=2,
        unclassified_tool_names=["process_list", "detect_orphans"],
    )
    grant = mcp_grant.generate_grant({"servers_reached": [row], "servers_failed": []}, _NAME_DENY_CONFIGS)
    assert grant["deny"].count("mcp__node-process-mcp__process_list") == 1
    assert grant["deny"].count("mcp__node-process-mcp__detect_orphans") == 1
    d = grant["disclosure"]
    # The row's 4 reads include system_health, which comes out by name: 4 - 1.
    assert d["tools_read_only_granted"] == 3
    # 3 writes + 2 unclassified from the census, system_health and session_output by name.
    assert d["tools_denied"] == 5 + 2
    assert d["deny_entries"] == len(grant["deny"])


def test_name_deny_never_lifts_for_a_server_denied_wholesale():
    """An incomplete row is denied by bare server name; the name entries still apply."""
    row = dict(_PROCESS_ROW, tools_total=20)
    grant = mcp_grant.generate_grant({"servers_reached": [row], "servers_failed": []}, _NAME_DENY_CONFIGS)
    assert "mcp__node-process-mcp" in grant["deny"]
    assert _NAME_DENIED <= set(grant["deny"])
