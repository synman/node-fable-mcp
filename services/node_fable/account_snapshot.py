"""The accounts snapshot, read from a configured command.

``NODE_FABLE_ACCOUNTS_COMMAND`` (JSON argv list) names a command that prints one JSON
object on stdout::

    {"accounts": [
        {"email": "alpha@example.com", "number": "1", "is_active": false,
         "disabled": false, "usage_age_s": 12.0,
         "windows": [{"label": "5h", "pct_used": 10.0, "resets_at": "..."},
                     {"label": "7d", "pct_used": 20.0, "resets_at": "..."},
                     {"label": "Fable", "pct_used": 30.0, "resets_at": null}]}]}

``pct_used`` is utilization (percent used). ``windows`` lists only the windows the
command measured; an account with no reading carries an empty list. ``usage_age_s``
is null when there is no reading. The command is read-only by contract: this module
never asks it to switch, add or remove an account.

The command runs with this process's own environment (it is the same trust as an
in-process read of the accounts), not the minimal environment a consultation child
gets. An unset command is "not configured". A command that fails, times out, or prints
anything else is a structured ``missing_dependency`` error. Nothing is guessed.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from node_fable import config
from node_fable.errors import FableError


@dataclass(frozen=True)
class AccountSnapshot:
    email: str
    number: str | None
    is_active: bool
    disabled: bool
    usage_age_s: float | None
    windows: tuple[tuple[str, float, object], ...]


@dataclass(frozen=True)
class AccountsSnapshot:
    accounts: tuple[AccountSnapshot, ...]


def _bad(detail: str) -> FableError:
    return FableError("missing_dependency", f"the accounts command output is unusable: {detail}")


def _window(raw: object, email: str) -> tuple[str, float, object]:
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("label"), str)
        or isinstance(raw.get("pct_used"), bool)
        or not isinstance(raw.get("pct_used"), (int, float))
    ):
        raise _bad(f"a window of {email!r} needs a string label and a numeric pct_used")
    return raw["label"], float(raw["pct_used"]), raw.get("resets_at")


def _account(raw: object) -> AccountSnapshot:
    if not isinstance(raw, dict) or not isinstance(raw.get("email"), str) or not raw["email"]:
        raise _bad("every account needs a string email")
    email = raw["email"]
    windows = raw.get("windows") or []
    if not isinstance(windows, list):
        raise _bad(f"windows of {email!r} must be a list")
    age = raw.get("usage_age_s")
    if age is not None and (isinstance(age, bool) or not isinstance(age, (int, float))):
        raise _bad(f"usage_age_s of {email!r} must be a number or null")
    number = raw.get("number")
    return AccountSnapshot(
        email=email,
        number=None if number is None else str(number),
        is_active=bool(raw.get("is_active")),
        disabled=bool(raw.get("disabled")),
        usage_age_s=age,
        windows=tuple(_window(w, email) for w in windows),
    )


def parse_snapshot(text: str) -> AccountsSnapshot:
    """Parse the accounts command's stdout. Raises FableError on anything unexpected."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise _bad("stdout is not JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
        raise _bad('expected an object with an "accounts" list')
    return AccountsSnapshot(accounts=tuple(_account(a) for a in data["accounts"]))


def accounts_snapshot() -> AccountsSnapshot:
    """Run the configured accounts command and return its parsed snapshot (read-only)."""
    argv = config.accounts_command()
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=config.accounts_timeout_s(),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise FableError("missing_dependency", "the accounts command timed out") from exc
    except OSError as exc:
        raise FableError(
            "missing_dependency", f"the accounts command could not start: {type(exc).__name__}"
        ) from exc
    if done.returncode != 0:
        raise FableError(
            "missing_dependency", f"the accounts command exited {done.returncode}"
        )
    return parse_snapshot(done.stdout)
