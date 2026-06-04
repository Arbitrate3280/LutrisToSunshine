"""Sunshine service ownership and systemd override policy.

This module owns everything about how the tool integrates with the
host's Sunshine service unit -- which unit name we manage, which
override file paths are worth visiting, and what counts as "managed
by us" vs "owned by the user".

The durable ownership key is :data:`state["sunshine_unit_name"]`.
Override paths are derived from that unit name plus
``state["paths"]["systemd_user_dir"]`` on every load.  A path being
"associated with" a unit does NOT mean the file at that path was
written by us: only the managed-wrapper marker in the file content
proves ownership.  See :func:`is_managed_sunshine_override`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from display.utils import run_command, safe_string
from sunshine.install import homebrew_sunshine_binary


SUNSHINE_UNIT = "app-dev.lizardbyte.app.Sunshine.service"
FALLBACK_SUNSHINE_UNIT = "sunshine.service"
SUNSHINE_FLATPAK_ID = "dev.lizardbyte.app.Sunshine"
HOMEBREW_SUNSHINE_UNITS: Tuple[str, ...] = (
    "homebrew.sunshine.service",
    "homebrew.sunshine-beta.service",
)
SUNSHINE_UNIT_CANDIDATES: Tuple[str, ...] = (
    SUNSHINE_UNIT,
    FALLBACK_SUNSHINE_UNIT,
    *HOMEBREW_SUNSHINE_UNITS,
)


def _systemctl_user(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return run_command(["systemctl", "--user", *args], check=check)


def _sunshine_unit_exists(unit: str) -> bool:
    result = _systemctl_user("show", "--property=LoadState", "--value", unit)
    if result.returncode != 0:
        return False
    return (result.stdout or "").strip() == "loaded"


def _systemd_unit_id(unit: str) -> str:
    result = _systemctl_user("show", "--property=Id", "--value", unit)
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def sunshine_unit() -> str:
    """Return the live Sunshine service unit name on the host.

    Prefers the first loaded + active unit among
    :data:`SUNSHINE_UNIT_CANDIDATES`; falls back to the first loaded
    unit; finally to the canonical :data:`SUNSHINE_UNIT`.
    """
    for unit in SUNSHINE_UNIT_CANDIDATES:
        if _sunshine_unit_exists(unit) and _systemctl_user("is-active", unit).returncode == 0:
            return _systemd_unit_id(unit) or unit
    for unit in SUNSHINE_UNIT_CANDIDATES:
        if _sunshine_unit_exists(unit):
            return _systemd_unit_id(unit) or unit
    return SUNSHINE_UNIT


def is_sunshine_service_active() -> bool:
    return _systemctl_user("is-active", sunshine_unit()).returncode == 0


def managed_sunshine_units(state: Optional[Dict[str, Any]] = None) -> List[str]:
    """Return known Sunshine service unit aliases.

    The saved unit from ``state`` (if present) is prepended so reset
    logic cleans up the override we wrote even when its name isn't in
    :data:`SUNSHINE_UNIT_CANDIDATES`.  Duplicates are preserved in
    order; callers that need a set can ``list(dict.fromkeys(...))``.
    """
    units: List[str] = list(SUNSHINE_UNIT_CANDIDATES)
    if state is not None:
        saved_unit = safe_string(state.get("sunshine_unit_name"))
        if saved_unit and saved_unit not in units:
            units.insert(0, saved_unit)
    return units


def sunshine_binary() -> Optional[str]:
    """Return the Sunshine executable path on the host, or ``None``.

    Resolution order: ``PATH`` -> Homebrew formula prefix -> Flatpak.
    """
    binary = shutil.which("sunshine")
    if binary:
        return binary
    homebrew_binary = homebrew_sunshine_binary()
    if homebrew_binary:
        return homebrew_binary
    flatpak = shutil.which("flatpak")
    if not flatpak:
        return None
    result = subprocess.run(
        [flatpak, "info", SUNSHINE_FLATPAK_ID],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        return f"{flatpak} run {SUNSHINE_FLATPAK_ID}"
    return None


def daemon_reload() -> None:
    """Reload the user systemd manager after unit file changes."""
    _systemctl_user("daemon-reload")


def show_unit_property(unit: str, property_name: str) -> str:
    """Return the value of ``property_name`` for ``unit`` as reported
    by ``systemctl --user show``.  Empty string on failure.
    """
    result = _systemctl_user("show", f"--property={property_name}", "--value", unit)
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def start_sunshine_unit(unit: str) -> subprocess.CompletedProcess:
    """Start ``unit`` via ``systemctl --user`` and return the result."""
    return _systemctl_user("start", unit)


def stop_sunshine_unit(unit: str) -> subprocess.CompletedProcess:
    """Stop ``unit`` via ``systemctl --user`` and return the result."""
    return _systemctl_user("stop", unit)


def restart_sunshine_unit(unit: str) -> subprocess.CompletedProcess:
    """Restart ``unit`` via ``systemctl --user`` and return the result."""
    return _systemctl_user("restart", unit)


def fetch_sunshine_journal(lines: int) -> subprocess.CompletedProcess:
    """Return the most recent ``lines`` journal entries for the
    live Sunshine service unit.

    Output is not captured so journalctl can use a pager if the
    caller requests it -- in practice this helper is paired with
    ``--no-pager`` to write to a file or stdout.
    """
    return run_command(
        [
            "journalctl",
            "--user",
            "-u",
            sunshine_unit(),
            "-n",
            str(lines),
            "--no-pager",
        ],
        capture_output=False,
    )


def managed_override_marker(state: Dict[str, Any]) -> str:
    """Return the unique substring that proves a Sunshine override
    file was written by this tool.  Empty when the wrapper script
    path is not known.
    """
    return safe_string(state.get("paths", {}).get("sunshine_wrapper_script"))


def is_managed_sunshine_override(path: Path, marker: str) -> bool:
    """Return True only if ``path`` is a file whose content contains
    the managed-wrapper ``marker``.
    """
    if not marker or not path.is_file():
        return False
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return marker in content


def _unlink_if_empty_dir(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def override_paths_for_unit(unit: str, state: Dict[str, Any]) -> List[Path]:
    """Return the (override_file, override_dir) pair for ``unit``.

    These are paths ASSOCIATED with the unit -- the marker check is
    the only way to determine whether this tool actually wrote the
    file at the override_file location.
    """
    systemd_user_dir = Path(state["paths"]["systemd_user_dir"])
    override_dir = systemd_user_dir / f"{unit}.d"
    return [override_dir / "override.conf", override_dir]


def current_unit_override_paths(state: Dict[str, Any]) -> List[Path]:
    """Return override paths for the unit recorded in
    ``state["sunshine_unit_name"]``.

    The state path is the durable ownership key.  Override paths are
    derived from it on every load, NOT persisted as the path of a
    file we once wrote -- so the marker check is required to confirm
    this tool still owns whatever is at those paths.
    """
    unit = safe_string(state.get("sunshine_unit_name"))
    if not unit:
        return []
    return override_paths_for_unit(unit, state)


def legacy_unit_override_paths(state: Dict[str, Any]) -> List[Path]:
    """Return override paths for known Sunshine service units other
    than the one in ``state["sunshine_unit_name"]``.

    These come from previous installs (Flatpak, native, Homebrew
    stable/beta) and may contain either managed overrides from a
    prior version of this tool or user-supplied overrides.  The
    marker check is the only way to tell them apart.
    """
    saved_unit = safe_string(state.get("sunshine_unit_name"))
    paths: List[Path] = []
    for unit in managed_sunshine_units(state):
        if unit == saved_unit:
            continue
        paths.extend(override_paths_for_unit(unit, state))
    return paths


def cleanup_managed_overrides(state: Dict[str, Any]) -> None:
    """Remove Sunshine service overrides installed by this tool.

    Visits every override path associated with known Sunshine
    service units (current saved unit plus legacy aliases).  A file
    is unlinked only when its content contains the managed-wrapper
    marker -- otherwise it is treated as user-supplied and left
    alone.  Empty override directories are cleaned up too.
    """
    marker = managed_override_marker(state)
    for path in current_unit_override_paths(state) + legacy_unit_override_paths(state):
        if path.is_dir():
            _unlink_if_empty_dir(path)
            continue
        if not is_managed_sunshine_override(path, marker):
            continue
        try:
            path.unlink()
        except OSError:
            pass

