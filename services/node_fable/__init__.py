"""node_fable — package backing the node-fable-mcp FastMCP stdio server.

Wraps one-shot headless Fable consultations launched through a configured
command and an optional configured account switcher. Modules:

  settings    where a setting comes from (NODE_FABLE_*, config file, default)
  config      typed settings, state paths — fail closed
  errors      FableError structured-error carrier
  account_snapshot  the accounts snapshot, read from a configured command
  scrub       secret-pattern refusal chokepoint + jargon neutralization
  accounts    candidate account selection (allowlist ∩ live, headroom rank)
  call_state  shared fcntl-locked JSON call-state store (sessions, cooldowns)
  prompts     role system-prompt construction from prompts/*.md templates
  runner      argv construction, subprocess lifecycle, result classification
"""

from __future__ import annotations

__all__ = [
    "account_snapshot",
    "accounts",
    "call_state",
    "config",
    "errors",
    "prompts",
    "runner",
    "scrub",
    "settings",
]
