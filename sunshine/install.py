"""Homebrew Sunshine install helpers.

This module owns probing for Homebrew-managed Sunshine installs
(prefix lookup, executable resolution, formula iteration).  Flatpak,
native, and AppImage detection live in :func:`sunshine.sunshine.
detect_sunshine_installation`; only the Homebrew branch is delegated
here so the install root and runtime configuration cannot drift
between ``sunshine/sunshine.py`` and the display code that consumes
the resolved binary.
"""

import os
import shutil
import subprocess
from typing import Optional, Tuple

HOMEBREW_SUNSHINE_FORMULAE: Tuple[str, ...] = ("sunshine", "sunshine-beta")


def homebrew_prefix(formula: str) -> Optional[str]:
    """Return the Homebrew install prefix for ``formula`` or ``None``.

    Runs ``brew --prefix <formula>`` and returns the first non-empty
    line of stdout.  Returns ``None`` when ``brew`` is missing, the
    formula is not installed, or the subprocess fails.
    """
    brew = shutil.which("brew")
    if not brew:
        return None
    try:
        result = subprocess.run(
            [brew, "--prefix", formula],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    stdout = (result.stdout or "").strip()
    if not stdout:
        return None
    first = stdout.splitlines()[0].strip()
    return first or None


def homebrew_sunshine_executable() -> Optional[Tuple[str, str]]:
    """Return ``(formula, executable_path)`` for a Homebrew Sunshine install.

    Iterates :data:`HOMEBREW_SUNSHINE_FORMULAE` and returns the first
    formula whose installed prefix contains an executable
    ``bin/sunshine``.  Returns ``None`` if no Homebrew Sunshine is
    installed.
    """
    for formula in HOMEBREW_SUNSHINE_FORMULAE:
        prefix = homebrew_prefix(formula)
        if not prefix:
            continue
        candidate = os.path.join(prefix, "bin", "sunshine")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return formula, candidate
    return None


def homebrew_sunshine_binary() -> Optional[str]:
    """Return the Sunshine executable path from a Homebrew install.

    Convenience wrapper around :func:`homebrew_sunshine_executable`
    that returns only the path.  Returns ``None`` when no Homebrew
    Sunshine is installed.
    """
    found = homebrew_sunshine_executable()
    return found[1] if found is not None else None
