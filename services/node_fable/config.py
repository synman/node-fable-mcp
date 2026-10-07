"""Environment resolution for node-fable-mcp (NODE_FABLE_* prefix).

All lookups happen at CALL time, never import time, so a registration can set
env per-server and tests can isolate state with monkeypatch. Missing required
values raise FableError naming the env var (fail closed — never a silent
default for anything deployment-specific).
"""

from __future__ import annotations

import os
from pathlib import Path

from node_fable.errors import FableError

ENV_ACCOUNTS = "NODE_FABLE_ACCOUNTS"
ENV_CSWAP_SRC = "NODE_FABLE_CSWAP_SRC"
ENV_SECRET_PATTERNS_PATH = "NODE_FABLE_SECRET_PATTERNS_PATH"
ENV_EXTRA_INTERNAL_TLDS = "NODE_FABLE_EXTRA_INTERNAL_TLDS"
ENV_MAX_BUDGET_USD = "NODE_FABLE_MAX_BUDGET_USD"
ENV_TIMEOUT_S = "NODE_FABLE_TIMEOUT_S"
ENV_COOLDOWN_S = "NODE_FABLE_COOLDOWN_S"
ENV_STATE_DIR = "NODE_FABLE_STATE_DIR"

DEFAULT_CSWAP_SRC = "~/ai/claude-swap/src"
# Per-consultation spend cap. Unless NODE_FABLE_MAX_BUDGET_USD is set, this
# default IS the live cap.
DEFAULT_MAX_BUDGET_USD = "50.00"
# Chosen after a `plan`
# consult at high effort died at the previous 900s with 75 turns of work and
# no plan text — the CLAUDE.md corpus on the direct path makes every turn
# slow, and the CLI never prints its JSON when killed, so a timeout loses
# everything. The deployment env block sets no NODE_FABLE_TIMEOUT_S, so this
# default IS the live kill; the $50 budget cap above is the cost bound.
DEFAULT_TIMEOUT_S = 3600
# Rate-limit cooldown: how long a 429'd account is held out of the selector.
# The binding short window is the 5-hour one, but most 429s clear well before
# a full window rolls over; one hour is the conservative middle ground and is
# configurable per deployment via NODE_FABLE_COOLDOWN_S.
DEFAULT_COOLDOWN_S = 3600
DEFAULT_STATE_DIR = "~/.node-fable-mcp"


def state_dir() -> Path:
    raw = os.environ.get(ENV_STATE_DIR, "").strip() or DEFAULT_STATE_DIR
    p = Path(os.path.expanduser(raw))
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    # mkdir's mode only applies on creation — enforce on every resolve so a
    # pre-existing loose dir tightens too (the call-state file records the
    # consumer-account emails; credential-adjacent, owner-only).
    os.chmod(p, 0o700)
    return p


def locks_dir() -> Path:
    p = state_dir() / "locks"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(p, 0o700)
    return p


def call_state_path() -> Path:
    return state_dir() / "call-state.json"


def cswap_src() -> Path:
    raw = os.environ.get(ENV_CSWAP_SRC, "").strip() or DEFAULT_CSWAP_SRC
    return Path(os.path.expanduser(raw))


def secret_patterns_path() -> Path | None:
    """The override file for the secret-pattern list, or None to use the built-in list."""
    raw = os.environ.get(ENV_SECRET_PATTERNS_PATH, "").strip()
    return Path(os.path.expanduser(raw)) if raw else None


def extra_internal_tlds() -> list[str]:
    """Extra top-level domains the optional hostname aliasing treats as internal."""
    raw = os.environ.get(ENV_EXTRA_INTERNAL_TLDS, "")
    return [t.strip().lower().lstrip(".") for t in raw.split(",") if t.strip()]


def accounts_allowlist() -> list[str]:
    """Candidate account emails, comma-separated in NODE_FABLE_ACCOUNTS.

    Fails closed when unset/empty: portable, tenant-agnostic code carries no
    personal emails — the deployment registration supplies them.
    """
    raw = os.environ.get(ENV_ACCOUNTS, "")
    emails = [e.strip() for e in raw.split(",") if e.strip()]
    if not emails:
        raise FableError(
            "missing_env",
            f"{ENV_ACCOUNTS} is unset or empty — set it to a comma-separated "
            "list of cswap-managed account emails eligible for Fable "
            "consultations (deployment registration supplies these; the code "
            "ships none).",
        )
    return emails


def max_budget_usd() -> str:
    raw = os.environ.get(ENV_MAX_BUDGET_USD, "").strip() or DEFAULT_MAX_BUDGET_USD
    try:
        value = float(raw)
    except ValueError as exc:
        raise FableError(
            "missing_env",
            f"{ENV_MAX_BUDGET_USD}={raw!r} is not a number",
        ) from exc
    if value <= 0:
        raise FableError(
            "missing_env",
            f"{ENV_MAX_BUDGET_USD}={raw!r} must be > 0",
        )
    return raw


def timeout_s() -> float:
    raw = os.environ.get(ENV_TIMEOUT_S, "").strip() or str(DEFAULT_TIMEOUT_S)
    try:
        value = float(raw)
    except ValueError as exc:
        raise FableError(
            "missing_env",
            f"{ENV_TIMEOUT_S}={raw!r} is not a number",
        ) from exc
    if value <= 0:
        raise FableError("missing_env", f"{ENV_TIMEOUT_S}={raw!r} must be > 0")
    return value


def cooldown_s() -> float:
    raw = os.environ.get(ENV_COOLDOWN_S, "").strip() or str(DEFAULT_COOLDOWN_S)
    try:
        value = float(raw)
    except ValueError as exc:
        raise FableError(
            "missing_env",
            f"{ENV_COOLDOWN_S}={raw!r} is not a number",
        ) from exc
    if value <= 0:
        raise FableError("missing_env", f"{ENV_COOLDOWN_S}={raw!r} must be > 0")
    return value
