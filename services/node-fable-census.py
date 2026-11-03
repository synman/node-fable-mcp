#!/usr/bin/env python3
"""node-fable-census — CLI entry point for the MCP tool census producer.

mcp_grant.py CONSUMES a census at the ``mcp_census_path`` setting (default
``~/.node-fable-mcp/mcp-tool-census.json``). This script is the producer's real,
re-runnable command -- the receipt names this command, not a memory.

Run:  python3 services/node-fable-census.py

Measures every server registered in the files named by the ``mcp_registry_paths``
setting via a safe, execution-free source read of each server's
own Python source (never a live spawn of a registered server -- see
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
