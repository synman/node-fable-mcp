"""node_fable — package backing the node-fable-mcp FastMCP stdio server.

Wraps one-shot headless Fable consultations launched via the external
``cswap`` account switcher. Modules:

  config      env resolution (NODE_FABLE_*), state paths — fail closed
  errors      FableError structured-error carrier
  cswap_api   read-only in-process cswap imports (switcher snapshot + oauth)
  scrub       secret-pattern refusal chokepoint + jargon neutralization
  accounts    candidate account selection (allowlist ∩ live, headroom rank)
  call_state  shared fcntl-locked JSON call-state store (sessions, cooldowns)
  prompts     role system-prompt construction from prompts/*.md templates
  runner      argv construction, subprocess lifecycle, result classification
"""

from __future__ import annotations

__all__ = [
    "accounts",
    "call_state",
    "config",
    "cswap_api",
    "errors",
    "prompts",
    "runner",
    "scrub",
]
