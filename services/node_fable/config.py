"""Setting resolution for node-fable-mcp (NODE_FABLE_* prefix, optional config file).

Every lookup goes through ``node_fable.settings`` (environment, then the config file,
then a neutral default) and happens at CALL time, never import time, so a registration
can set env per-server and tests can isolate state with monkeypatch. Anything that
depends on the machine has no default here: a missing required value raises
FableError("not_configured") naming the setting, and the call spends nothing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from node_fable import settings
from node_fable.errors import FableError

ENV_ACCOUNTS = settings.env_name("accounts")
ENV_SECRET_PATTERNS_PATH = settings.env_name("secret_patterns_path")
ENV_EXTRA_INTERNAL_TLDS = settings.env_name("extra_internal_tlds")
ENV_MAX_BUDGET_USD = settings.env_name("max_budget_usd")
ENV_TIMEOUT_S = settings.env_name("timeout_s")
ENV_COOLDOWN_S = settings.env_name("cooldown_s")
ENV_STATE_DIR = settings.env_name("state_dir")
ENV_MCP_CENSUS_PATH = settings.env_name("mcp_census_path")
ENV_MCP_REGISTRY_PATHS = settings.env_name("mcp_registry_paths")
ENV_WORKSPACE_PATHS_PATH = settings.env_name("workspace_paths_path")

# Per-consultation spend cap. Unless NODE_FABLE_MAX_BUDGET_USD is set, this
# default IS the live cap.
DEFAULT_MAX_BUDGET_USD = "50.00"
# A `plan` consult at high effort died at a 900s kill with 75 turns of work and
# no plan text, and the command never prints its JSON when killed, so a timeout
# loses everything. Unless NODE_FABLE_TIMEOUT_S is set this default IS the live
# kill; the budget cap above is the cost bound.
DEFAULT_TIMEOUT_S = 3600
# Rate-limit cooldown: how long a 429'd account is held out of the selector.
# The binding short window is the 5-hour one, but most 429s clear well before
# a full window rolls over; one hour is the conservative middle ground and is
# configurable per deployment via NODE_FABLE_COOLDOWN_S.
DEFAULT_COOLDOWN_S = 3600
DEFAULT_ACCOUNTS_TIMEOUT_S = 60
DEFAULT_STATE_DIR = "~/.node-fable-mcp"
DEFAULT_CENSUS_PATH = "~/.node-fable-mcp/mcp-tool-census.json"
DEFAULT_SOURCE_ROOTS_PATH = "~/.node-fable-mcp/workspace-paths"


def _not_configured(key: str, what: str) -> FableError:
    return FableError(
        "not_configured",
        f"{settings.env_name(key)} (setting '{key}') is not set — {what} The call "
        "is refused before anything launches and spends nothing.",
    )


def _guard(fn, key: str):
    """Run a settings read, re-raising a bad value as a structured FableError."""
    try:
        return fn(key)
    except settings.SettingsError as exc:
        raise FableError("invalid_setting", f"{exc}") from exc


def _text(key: str, default: str | None = None) -> str | None:
    return _guard(lambda k: settings.get(k, default), key)


def _list(key: str) -> list[str] | None:
    return _guard(settings.get_json_list, key)


def _map(key: str) -> dict[str, str] | None:
    return _guard(settings.get_json_map, key)


def _path(key: str, default: str) -> Path:
    return Path(os.path.expanduser(_text(key, default)))


def _required_list(key: str, what: str) -> list[str]:
    value = _list(key)
    if not value:
        raise _not_configured(key, what)
    return value


def state_dir() -> Path:
    p = _path("state_dir", DEFAULT_STATE_DIR)
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


def secret_patterns_path() -> Path | None:
    """The override file for the secret-pattern list, or None to use the built-in list."""
    raw = _text("secret_patterns_path")
    return Path(os.path.expanduser(raw)) if raw else None


def extra_internal_tlds() -> list[str]:
    """Extra top-level domains the optional hostname aliasing treats as internal."""
    raw = _text("extra_internal_tlds") or ""
    return [t.strip().lower().lstrip(".") for t in raw.split(",") if t.strip()]


def accounts_allowlist() -> list[str]:
    """Candidate account emails, comma-separated in NODE_FABLE_ACCOUNTS.

    Fails closed when unset/empty: portable, tenant-agnostic code carries no
    personal emails — the deployment registration supplies them.
    """
    raw = _text("accounts") or ""
    emails = [e.strip() for e in raw.split(",") if e.strip()]
    if not emails:
        raise FableError(
            "missing_env",
            f"{ENV_ACCOUNTS} is unset or empty — set it to a comma-separated "
            "list of account emails eligible for Fable consultations "
            "(deployment registration supplies these; the code ships none).",
        )
    return emails


def _positive_number(key: str, default) -> str:
    raw = _text(key) or str(default)
    env = settings.env_name(key)
    try:
        value = float(raw)
    except ValueError as exc:
        raise FableError("missing_env", f"{env}={raw!r} is not a number") from exc
    if value <= 0:
        raise FableError("missing_env", f"{env}={raw!r} must be > 0")
    return raw


def max_budget_usd() -> str:
    return _positive_number("max_budget_usd", DEFAULT_MAX_BUDGET_USD)


def timeout_s() -> float:
    return float(_positive_number("timeout_s", DEFAULT_TIMEOUT_S))


def cooldown_s() -> float:
    return float(_positive_number("cooldown_s", DEFAULT_COOLDOWN_S))


def accounts_timeout_s() -> float:
    return float(_positive_number("accounts_timeout_s", DEFAULT_ACCOUNTS_TIMEOUT_S))


# --- models -------------------------------------------------------------------------------------


def models() -> list[str]:
    """The model ids a consult may name (JSON list). None configured: refuse."""
    return _required_list("models", "the list of model ids a consult may use.")


def default_model() -> str:
    """The model a consult uses when the caller names none: the setting, else the first listed."""
    return _text("default_model") or models()[0]


def primary_model_prefix() -> str:
    """A reply from a model whose id starts with this is the primary tier, not a fallback.

    Defaults to the default model's own id.
    """
    return _text("primary_model_prefix") or default_model()


# --- launch commands ----------------------------------------------------------------------------


def command() -> list[str]:
    """The argv prefix that runs the external command directly (JSON list)."""
    return _required_list("command", "the command that runs a consultation directly.")


def switcher_command() -> list[str]:
    """The argv template that runs the command under a selected account (JSON list, `{account}`)."""
    return _required_list(
        "switcher_command", "the account-switcher launch template (it may use {account})."
    )


def args_template() -> list[str]:
    """The argument template for every launch (JSON list)."""
    return _required_list("command_args", "the argument template for a consultation.")


def template(key: str, what: str) -> list[str]:
    """A required argument-group template (JSON list). Absent is "not configured":
    a group is never silently dropped, because dropping a tool-deny group would
    fail open."""
    return _required_list(key, what)


def string_list(key: str) -> list[str]:
    """A JSON list of strings setting that may be absent (absent means empty)."""
    return _list(key) or []


def payload_template(key: str, what: str) -> dict:
    """A required JSON object template for a generated payload."""
    raw = _text(key)
    if raw is None:
        raise _not_configured(key, what)
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise FableError("invalid_setting", f"setting {key!r} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise FableError("invalid_setting", f"setting {key!r} must be a JSON object")
    return value


def accounts_command() -> list[str]:
    """The command whose stdout is the accounts snapshot JSON (JSON list)."""
    return _required_list(
        "accounts_command", "the command that prints the accounts snapshot as JSON."
    )


def child_env() -> dict[str, str]:
    """The environment handed to a launched child — never a copy of the parent's.

    Names listed in ``child_env_passthrough`` are copied from this process when set.
    The variable named by ``child_env_user_var`` always reaches the child: its
    value is this process's own, else the home directory's base name (a
    launcher-started parent may carry none). ``child_env_set`` adds fixed values
    last. All three default to nothing.
    """
    env: dict[str, str] = {}
    for name in _list("child_env_passthrough") or []:
        if os.environ.get(name):
            env[name] = os.environ[name]
    user_var = _text("child_env_user_var")
    if user_var:
        env[user_var] = os.environ.get(user_var) or os.path.basename(os.path.expanduser("~"))
    env.update(_map("child_env_set") or {})
    return env


# --- access and grant ---------------------------------------------------------------------------


def _explicit_list(key: str, what: str) -> list[str]:
    """A safety list that must be stated: unset is refused, and an explicit empty
    list (``[]``) is the operator's deliberate choice of none. A safety control that
    silently defaults to nothing would weaken itself on an unconfigured node."""
    value = _list(key)
    if value is None:
        raise _not_configured(key, what + " Set it to a JSON list; [] means none.")
    return value


def forbidden_roots() -> list[str]:
    """Paths that may never be granted to a consultation, directly or by an ancestor (JSON list)."""
    return _explicit_list("forbidden_roots", "the paths that may never be granted to a consultation.")


def git_write_deny_tools() -> list[str]:
    """Tool patterns denied so a consultation cannot commit or push (JSON list)."""
    return _explicit_list("git_write_deny_tools", "the tool patterns that deny git commit and push.")


def git_read_tool_prefix() -> str:
    """Text placed before ``git_status`` in the unarmed prompt, so the prompt can name
    the granted git read tool by its qualified name (for example ``mcp__<server>__``)."""
    return _text("git_read_tool_prefix") or ""


def excluded_servers() -> frozenset[str]:
    """Server names never granted, whatever the census says (JSON list)."""
    return frozenset(_explicit_list("excluded_servers", "the MCP servers that are never granted."))


def denied_tools() -> tuple[str, ...]:
    """Qualified tool names never granted, whatever the census says (JSON list)."""
    return tuple(_explicit_list("denied_tools", "the qualified tool names that are never granted."))


def jargon_map() -> dict[str, str]:
    """Builder-prompt vocabulary to neutralize: term -> replacement (JSON map)."""
    return _map("jargon_map") or {}


def mcp_registry_paths() -> list[Path]:
    """Files holding ``mcpServers`` registrations, merged in order (a later file wins)."""
    return [Path(os.path.expanduser(p)) for p in _list("mcp_registry_paths") or []]


def census_path() -> Path:
    return _path("mcp_census_path", DEFAULT_CENSUS_PATH)


def source_roots_path() -> Path:
    return _path("workspace_paths_path", DEFAULT_SOURCE_ROOTS_PATH)
