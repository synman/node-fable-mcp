#!/usr/bin/env python3
"""Hand-verification harness for evals/eval.xml (mcp-builder Phase 4).

Asserts every answer in eval.xml against the running code/server — no live
cswap/claude launch (every question resolves before a subprocess would
spawn). Exits nonzero on any mismatch, printing per-question PASS/FAIL.

Run:  ~/.virtualenvs/main/bin/python3 evals/verify_eval.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import xml.etree.ElementTree as ET

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "services"))

os.environ.setdefault("NODE_FABLE_ACCOUNTS", "a@example.com")
os.environ["NODE_FABLE_STATE_DIR"] = tempfile.mkdtemp(prefix="fable-eval-")

from node_fable import call_state, config, prompts, runner  # noqa: E402
from node_fable.errors import FableError  # noqa: E402


def _err_class(fn, *a, **k) -> str:
    try:
        fn(*a, **k)
    except FableError as exc:
        return exc.error_class
    return "(no error)"


def _tool_surface() -> dict:
    """Spawn the real server over stdio, initialize, tools/list."""
    import json
    import subprocess

    proc = subprocess.Popen(
        [sys.executable, os.path.join(_REPO, "services", "node-fable-mcp.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "eval-verify", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        out, _ = proc.communicate(
            "\n".join(json.dumps(m) for m in msgs) + "\n", timeout=30)
        tools = {}
        for line in out.splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("id") == 2:
                for t in obj["result"]["tools"]:
                    tools[t["name"]] = t
        return tools
    finally:
        if proc.poll() is None:
            proc.kill()


def main() -> int:
    tree = ET.parse(os.path.join(_HERE, "eval.xml"))
    expected = [
        (q.findtext("question"), q.findtext("answer"))
        for q in tree.getroot().findall("qa_pair")
    ]
    tools = _tool_surface()
    home = os.environ["HOME"]
    claude_probe = os.path.join(home, ".claude")
    # ~/.claude must exist for Q10's probe to be meaningful on this host.
    q10_target = claude_probe if os.path.isdir(claude_probe) else None

    actual = [
        str(len(tools)),
        str((tools.get("fable_consult") or {}).get("annotations", {}).get("title")),
        str(bool((tools.get("fable_headroom") or {}).get("annotations", {}).get("readOnlyHint"))).lower(),
        _err_class(runner.validate_model, "claude-fable-5-0"),
        ", ".join(runner.MODELS),
        _err_class(
            call_state.validate_resume,
            call_state.CallStateStore(config.call_state_path()),
            "no-such-session", "plan",
        ),
        _err_class(runner.consult, role="plan", brief=""),
        "plan" if "plan" in prompts.ROLES and "elaborate" not in prompts.ROLES else "(roles wrong)",
        prompts.ROLE_EFFORT_DEFAULT["implement"],
        _err_class(runner.validate_grant_path, q10_target, "material")
        if q10_target else "(no ~/.claude on this host)",
    ]

    failures = 0
    for i, ((question, answer), got) in enumerate(zip(expected, actual), 1):
        ok = (answer or "").strip() == (got or "").strip()
        print(f"Q{i}: {'PASS' if ok else 'FAIL'}  expected={answer!r} got={got!r}")
        if not ok:
            failures += 1
    print(f"{len(expected) - failures}/{len(expected)} verified")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
