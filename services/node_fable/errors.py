"""Structured error carrier for node-fable-mcp.

Every failure path in this package raises ``FableError`` with a machine-
branchable ``error_class`` and a human-actionable ``message``. Tools catch it
at the boundary and serialize ``payload()`` — nothing in this package fails
open or substitutes a silent default.
"""

from __future__ import annotations

from typing import Any


class FableError(Exception):
    """A structured, fail-closed error.

    ``error_class`` values used across the package:
      invalid_role, invalid_effort, invalid_model, invalid_brief,
      invalid_material, forbidden_path, missing_env, missing_dependency,
      state_corrupt, secret_refused, self_review, refused_session,
      unknown_session, no_headroom, account_mismatch, no_active_account
    """

    def __init__(self, error_class: str, message: str, **extra: Any) -> None:
        super().__init__(f"{error_class}: {message}")
        self.error_class = error_class
        self.message = message
        self.extra = extra

    def payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": "error",
            "error_class": self.error_class,
            "message": self.message,
        }
        out.update(self.extra)
        return out
