"""Stdio smoke — spawn the real server, raw JSON-RPC initialize + tools/list.

Uses newline-delimited JSON-RPC over pipes (the MCP python SDK's stdio
framing) so the smoke has no client-library dependency. Asserts both tools
are listed with their annotations. The server process is torn down in a
finally block. No cswap/claude is launched — listing tools invokes nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ENTRY = _ROOT / "services" / "node-fable-mcp.py"

_SMOKE_TIMEOUT_S = 30


def _readline_with_timeout(proc, deadline):
    while time.time() < deadline:
        line = proc.stdout.readline()
        if line:
            return line
        if proc.poll() is not None:
            raise AssertionError(
                f"server exited rc={proc.returncode}: {proc.stderr.read()[:2000]}"
            )
    raise AssertionError("timed out waiting for server stdout")


def test_stdio_handshake_lists_both_tools_with_annotations(tmp_path):
    env = dict(os.environ)
    env["NODE_FABLE_STATE_DIR"] = str(tmp_path / "state")
    proc = subprocess.Popen(
        [sys.executable, str(_ENTRY)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    deadline = time.time() + _SMOKE_TIMEOUT_S
    try:
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "0"},
            },
        }
        proc.stdin.write(json.dumps(init) + "\n")
        proc.stdin.flush()
        resp = json.loads(_readline_with_timeout(proc, deadline))
        assert resp["id"] == 1
        assert resp["result"]["serverInfo"]["name"] == "node-fable-mcp"

        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
        proc.stdin.flush()
        while True:
            msg = json.loads(_readline_with_timeout(proc, deadline))
            if msg.get("id") == 2:
                break
        tools = {t["name"]: t for t in msg["result"]["tools"]}
        assert set(tools) == {"fable_consult", "fable_headroom"}

        consult_ann = tools["fable_consult"]["annotations"]
        assert consult_ann["title"] == "Fable Consult"
        assert consult_ann["readOnlyHint"] is False
        assert consult_ann["destructiveHint"] is True

        headroom_ann = tools["fable_headroom"]["annotations"]
        assert headroom_ann["title"] == "Fable Headroom"
        assert headroom_ann["readOnlyHint"] is True
        assert headroom_ann["destructiveHint"] is False

        # What the wire description must carry. The 13 numbered pitfalls used to be
        # asserted here as `(1)`..`(13)`, which required the whole 9.6k-character
        # text to ride in the description; Claude Code cuts a description at 2,048
        # characters, so that guard mandated the very truncation it should prevent.
        # The pitfalls are pinned verbatim in the README reference section
        # (test_published_surface.py::test_fable_consult_reference_names_all_13_pitfalls);
        # what must survive to the wire is the cost, the write guard, the credential
        # rule, and the pointer to that reference.
        desc = tools["fable_consult"]["description"]
        # "WRITE GUARD" alone would still match the Args lines' cross-reference
        # after the guard paragraph itself was deleted, so pin the paragraph's own
        # opening and its arming sentence.
        for marker in ("COST:", "WRITE GUARD: confirmed=False", "confirmed=True ARMS", "PLAINTEXT", "README.md"):
            assert marker in desc, f"wire description lost {marker!r}"
        assert len(desc) <= 2048
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
