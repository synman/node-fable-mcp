"""Read-only in-process access to cswap's account/usage APIs.

Imports ``claude_swap`` from NODE_FABLE_CSWAP_SRC (default
``~/ai/claude-swap/src`` by default) at call time. STRICTLY read-only: this module (and
this whole package) never calls cswap switch/add/remove and never executes
anything except ``cswap run`` — and even that lives in runner.py, not here.

Import failure is a structured error naming the missing dependency and the
env var that points at it (fail closed, actionable).
"""

from __future__ import annotations

import sys

from node_fable import config
from node_fable.errors import FableError


def load_cswap():
    """Return ``(ClaudeAccountSwitcher, oauth_module)`` or raise FableError.

    sys.path-inserts the configured cswap src dir, then imports. Cached by
    Python's ordinary module cache; safe to call per tool invocation.
    """
    src = config.cswap_src()
    if not src.is_dir():
        raise FableError(
            "missing_dependency",
            f"cswap source directory not found at {src} — install claude-swap "
            f"or point {config.ENV_CSWAP_SRC} at its src/ directory.",
        )
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    def _do_import():
        from claude_swap import oauth
        from claude_swap.switcher import ClaudeAccountSwitcher
        return ClaudeAccountSwitcher, oauth

    try:
        return _do_import()
    except Exception as exc:
        import importlib.metadata as _md

        if not (
            isinstance(exc, _md.PackageNotFoundError)
            or "No package metadata" in str(exc)
        ):
            raise FableError(
                "missing_dependency",
                f"failed to import claude_swap from {src}: {exc} — verify the "
                f"checkout and {config.ENV_CSWAP_SRC}.",
            ) from exc
        # Source-checkout shim: claude_swap/__init__.py resolves __version__
        # via importlib.metadata.version("claude-swap"), which exists only
        # for a pip-INSTALLED package. A plain source checkout on sys.path is
        # the supported deployment here (empirical: a typical `main`
        # venv has mcp but no claude-swap install; cswap's own venv has the
        # inverse). The version string is decorative, so shim exactly that
        # one lookup for exactly that one package rather than requiring a
        # pip install into a shared venv. The real function is restored in
        # finally; the shim never affects any other package's metadata.
        real_version = _md.version

        def _shim(name, *a, **k):
            if name == "claude-swap":
                return "0.0.0+source-checkout"
            return real_version(name, *a, **k)

        _md.version = _shim
        try:
            sys.modules.pop("claude_swap", None)
            return _do_import()
        except Exception as exc2:
            raise FableError(
                "missing_dependency",
                f"failed to import claude_swap from {src} (even with the "
                f"source-checkout metadata shim): {exc2} — verify the "
                f"checkout and {config.ENV_CSWAP_SRC}.",
            ) from exc2
        finally:
            _md.version = real_version


def accounts_snapshot():
    """Fetch a coherent accounts snapshot via cswap (read-only).

    Returns the ``AccountsSnapshot`` (``.accounts`` tuple of
    ``AccountSnapshot``: number, email, is_active, kind, switchable,
    usage.last_good, usage.age_s).
    """
    switcher_cls, _oauth = load_cswap()
    try:
        switcher = switcher_cls()
        return switcher.accounts_snapshot()
    except FableError:
        raise
    except Exception as exc:
        raise FableError(
            "missing_dependency",
            f"cswap accounts_snapshot() failed: {exc}",
        ) from exc


def oauth_module():
    """Return cswap's oauth module (relevant_windows / account_headroom)."""
    _switcher_cls, oauth = load_cswap()
    return oauth
