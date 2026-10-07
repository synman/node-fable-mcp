#!/usr/bin/env python3
"""node-fable-census — CLI entry point for the fleet MCP tool census producer.

Task G wired mcp_grant.py to CONSUME a census at
``~/.node-fable-mcp/mcp-tool-census.json`` but shipped no code that ever
PRODUCES one. This script is that producer's real, re-runnable command --
the receipt an earlier measurementounds row G requires names this command, not a memory.

Run:  ~/.virtualenvs/main/bin/python3 services/node-fable-census.py

Measures every server registered in this node's ``.mcp.json`` and
``~/.claude.json`` via a safe, execution-free source read of each server's
own Python source (never a live spawn of a fleet server -- see
``node_fable/census.py``'s module docstring for why), writes the result to
the exact path ``mcp_grant.build_grant()`` reads, and prints a JSON receipt
naming the real measured per-server numbers.
"""

from __future__ import annotations

import sys
from pathlib import Path

SERVICES_DIR = Path(__file__).resolve().parent
if str(SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICES_DIR))

from node_fable import census  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(census.main())
