"""Every test starts with no config file and no setting in the environment, so the
machine's real configuration is never read and no test can launch a real command.

``fake_cli`` is the opt-in fixture for tests that exercise argv construction or the
consult flow: it configures neutral fake commands, a fake model list and neutral
tool lists through the environment, nothing else.
"""
import json
import os
import sys
from pathlib import Path

import pytest

_SERVICES = Path(__file__).resolve().parent.parent / "services"
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

# Captured at import, before any fixture clears the environment: the live tests (which spend
# real money) read the operator's own config only from the path named here, and only when
# NODE_FABLE_LIVE=1 is set. Nothing else in the suite ever sees either variable.
LIVE = os.environ.get("NODE_FABLE_LIVE") == "1"
LIVE_CONFIG = os.environ.get("NODE_FABLE_LIVE_CONFIG")

FAKE_MODELS = ["fable-test-1", "fallback-test-1", "fallback-test-2"]
FAKE_EXCLUDED_SERVERS = ["excluded-a", "excluded-b", "excluded-c", "excluded-d"]
FAKE_DENIED_TOOLS = [
    "mcp__proc-a__process_list",
    "mcp__proc-a__detect_orphans",
    "mcp__proc-a__system_health",
    "mcp__proc-b__session_output",
]
FAKE_GIT_DENY = [
    "Bash(git commit:*)",
    "Bash(git push:*)",
    "Bash(git -C * commit:*)",
    "Bash(git -C * push:*)",
    "Bash(sh -c *git commit*)",
    "Bash(sh -c *git push*)",
]
FAKE_SETTINGS = {
    "models": json.dumps(FAKE_MODELS),
    "command": json.dumps(["fake-cmd"]),
    "switcher_command": json.dumps(["fake-switch", "run", "{account}", "--require-session", "--no-share", "--"]),
    "command_args": json.dumps([
        "-p", "--model", "{model}", "--output-format", "json", "--effort", "{effort}",
        "--strict-mcp-config", "--mcp-config", "{mcp_config}", "--settings", "{settings}",
        "--max-budget-usd", "{budget_usd}",
    ]),
    "allowed_tools_args": json.dumps(["--allowedTools", "{tools}"]),
    "disallowed_tools_args": json.dumps(["--disallowedTools", "{tools}"]),
    "add_dir_args": json.dumps(["--add-dir", "{dir}"]),
    "system_prompt_args": json.dumps(["--system-prompt-file", "{file}"]),
    "resume_args": json.dumps(["-r", "{session_id}"]),
    "new_session_args": json.dumps(["--session-id", "{session_id}"]),
    "allowed_tools": json.dumps(["Read", "Grep", "Glob"]),
    "armed_tools": json.dumps(["Edit", "Write", "NotebookEdit", "Bash"]),
    "git_write_deny_tools": json.dumps(FAKE_GIT_DENY),
    "mcp_config_template": json.dumps({"mcpServers": "{servers}"}),
    "settings_template": json.dumps({"disableAllHooks": True, "hooks": {}, "permissions": {"deny": "{deny}"}}),
    "excluded_servers": json.dumps(FAKE_EXCLUDED_SERVERS),
    "denied_tools": json.dumps(FAKE_DENIED_TOOLS),
    "jargon_map": json.dumps({"zorbx": "neutral one", "quuxl": "neutral two", "plugh frob": "neutral three"}),
    "forbidden_roots": json.dumps(["~/.forbidden-a", "~/.forbidden-b"]),
    "child_env_passthrough": json.dumps(["HOME", "PATH", "TMPDIR", "LANG", "LC_ALL"]),
    "child_env_user_var": "FAKE_ACCOUNT_USER",
    "child_env_set": json.dumps({"TERM": "dumb"}),
}


@pytest.fixture(autouse=True)
def _no_ambient_config(tmp_path_factory, monkeypatch):
    for name in list(os.environ):
        if name.startswith("NODE_FABLE_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("NODE_FABLE_CONFIG", str(tmp_path_factory.mktemp("noconfig") / "absent.json"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("noxdg")))


@pytest.fixture
def fake_cli(monkeypatch):
    """Configure the fake commands and neutral lists. Returns the settings dict used."""
    for key, value in FAKE_SETTINGS.items():
        monkeypatch.setenv("NODE_FABLE_" + key.upper(), value)
    return dict(FAKE_SETTINGS)


@pytest.fixture
def live_config(monkeypatch):
    """For the live tests only: the operator's own config, named explicitly, never a default."""
    if not LIVE or not LIVE_CONFIG:
        pytest.skip("live tests need NODE_FABLE_LIVE=1 and NODE_FABLE_LIVE_CONFIG=<config file>")
    monkeypatch.setenv("NODE_FABLE_CONFIG", LIVE_CONFIG)
    return LIVE_CONFIG
