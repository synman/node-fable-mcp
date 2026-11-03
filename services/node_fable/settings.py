"""node_fable.settings — where a configured value comes from.

Nothing environment-specific is written in this repository. A value that depends on
the machine (a command, a path, a model id, an environment variable's name) is read
here, in this order:

1. the environment variable ``NODE_FABLE_<KEY>`` (the key in upper case);
2. the JSON config file, a flat object of string values;
3. the neutral default the caller passes, or None.

The config file is ``$NODE_FABLE_CONFIG`` when set, else
``$XDG_CONFIG_HOME/node-fable-mcp/config.json``, else
``~/.config/node-fable-mcp/config.json``. A missing file is empty. A file that cannot
be read or is not a flat object of strings raises ``SettingsError``: guessing a
default there could point a caller at the wrong command or account.

A list or a map is written as a JSON string and parsed with ``get_json_list`` or
``get_json_map``, which raise ``SettingsError`` on a value that is not what was asked.

This module is a leaf: standard library only, no state, no I/O at import.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

PREFIX = "NODE_FABLE_"


class SettingsError(ValueError):
    """A setting or the config file exists but cannot be used."""


def env_name(key: str) -> str:
    """The environment variable that carries ``key``."""
    return PREFIX + key.upper()


def config_path() -> Path:
    explicit = os.environ.get(env_name("config"))
    if explicit:
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "node-fable-mcp" / "config.json"


def _file_values() -> "dict[str, str]":
    path = config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise SettingsError(f"config file {path} cannot be read ({type(exc).__name__})") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SettingsError(f"config file {path} is not valid JSON") from exc
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise SettingsError(f"config file {path} must be one flat object of string values")
    return data


def get(key: str, default: "str | None" = None) -> "str | None":
    """The value for ``key``: environment, then config file, then ``default``.

    An empty or all-blank value means "not set" and falls through to the default, so a
    blank environment variable never overrides the file with nothing.
    """
    raw = os.environ.get(env_name(key))
    if raw is None or not raw.strip():
        raw = _file_values().get(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


def get_path(key: str, default: "str | Path") -> Path:
    """A path-valued setting with ``~`` expanded."""
    return Path(get(key, str(default))).expanduser()


def get_json_list(key: str) -> "list[str] | None":
    """A setting holding a JSON list of strings, or None when it is not set."""
    raw = get(key)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise SettingsError(f"setting {key!r} is not valid JSON") from exc
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise SettingsError(f"setting {key!r} must be a JSON list of strings")
    return value


def get_json_map(key: str) -> "dict[str, str] | None":
    """A setting holding a JSON object of string values, or None when it is not set."""
    raw = get(key)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise SettingsError(f"setting {key!r} is not valid JSON") from exc
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise SettingsError(f"setting {key!r} must be a JSON object of string values")
    return value
