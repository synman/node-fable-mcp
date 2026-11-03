"""The server's version of record: one constant, ``__version__`` in services/node-fable-mcp.py,
written as a literal semver triple and reported to clients as serverInfo.version."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
SERVER = APP / "services" / "node-fable-mcp.py"
SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
LITERAL = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.M)


@pytest.fixture(scope="module")
def server():
    spec = importlib.util.spec_from_file_location("nfm_version_server", str(SERVER))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_constant_is_a_semver_triple_written_as_a_literal(server):
    assert SEMVER.fullmatch(server.__version__), server.__version__
    match = LITERAL.search(SERVER.read_text())
    assert match and match.group(1) == server.__version__


def test_the_low_level_server_carries_the_constant(server):
    assert server.mcp._mcp_server.version == server.__version__


def test_a_fresh_spawn_answers_initialize_with_the_constant(server, tmp_path):
    env = dict(os.environ, NODE_FABLE_STATE_DIR=str(tmp_path / "state"), PYTHONDONTWRITEBYTECODE="1")
    request = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "version-test", "version": "0"}},
    }
    done = subprocess.run(
        [sys.executable, str(SERVER)], input=json.dumps(request) + "\n", env=env, cwd=tmp_path,
        capture_output=True, text=True, timeout=60,
    )
    lines = [ln for ln in done.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, f"no JSON-RPC reply; stderr: {done.stderr[-400:]}"
    reply = json.loads(lines[0])
    assert reply["result"]["serverInfo"] == {"name": "node-fable-mcp", "version": server.__version__}
