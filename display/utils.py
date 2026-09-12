"""Generic display-package helpers.

The command runner is re-exported from the shared utility module; this
package adds only its string coercion helper. Both display subsystems can
therefore share these helpers without introducing a dependency cycle.
"""

from __future__ import annotations

from typing import Any

from utils.utils import run_command

__all__ = ["run_command", "safe_string"]


def safe_string(value: Any) -> str:
    """Return ``value`` coerced to a stripped string, or ``""`` if falsy."""
    return str(value or "").strip()
