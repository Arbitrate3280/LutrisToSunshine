"""Generic display-package helpers.

These utilities are not specific to any one subsystem in the
display package: they wrap ``subprocess.run`` with our standard
options and coerce arbitrary values to stripped strings.  Both
``display.manager`` and ``display.sunshine_service`` import them so
the dependencies form a clean tree with no cycles.
"""

from __future__ import annotations

import subprocess
from typing import Any, Dict, List, Optional


def safe_string(value: Any) -> str:
    """Return ``value`` coerced to a stripped string, or ``""`` if falsy."""
    return str(value or "").strip()


def run_command(
    command: List[str],
    *,
    capture_output: bool = True,
    check: bool = False,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """Run ``command`` with the display package's standard subprocess options."""
    return subprocess.run(
        command,
        text=True,
        capture_output=capture_output,
        check=check,
        env=env,
    )
