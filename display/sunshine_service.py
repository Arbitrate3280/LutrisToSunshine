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
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from display.utils import run_command, safe_string
from sunshine.detection import (
    SUNSHINE_UNIT,
    FALLBACK_SUNSHINE_UNIT,
    SUNSHINE_FLATPAK_ID,
    HOMEBREW_SUNSHINE_UNITS,
    SUNSHINE_UNIT_CANDIDATES,
    probe_packages,
    classify_service_unit,
    preferred_launch_binary,
    probe_sunshine_service_unit,
)


def _systemctl_user(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return run_command(["systemctl", "--user", *args], check=check)


def _sunshine_unit_exists(unit: str) -> bool:
    result = _systemctl_user("show", "--property=LoadState", "--value", unit)
    if result.returncode != 0:
        return False
    return (result.stdout or "").strip() == "loaded"


def sunshine_unit() -> str:
    """Return the live Sunshine service unit name on the host.

    Prefers the first loaded + active unit among
    :data:`SUNSHINE_UNIT_CANDIDATES`; falls back to the first loaded
    unit; finally to the canonical :data:`SUNSHINE_UNIT`.
    """
    return probe_sunshine_service_unit(systemctl_runner=_systemctl_user).unit_name


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


class SunshineInstallAudit(TypedDict):
    managed_unit: str
    managed_type: str
    detected_types: List[str]
    package_probe_type: str
    resolved_type: str
    probe_type: str
    path_binary: str
    homebrew_binary: str
    execstart: str
    fragment_path: str


def make_sunshine_install_audit(
    managed_unit: str,
    managed_type: str,
    detected_types: List[str],
    package_probe_type: Optional[str] = None,
    resolved_type: Optional[str] = None,
    path_binary: str = "",
    homebrew_binary: str = "",
    execstart: str = "",
    fragment_path: str = "",
) -> SunshineInstallAudit:
    """Helper constructor for SunshineInstallAudit to avoid duplicating incidental keys."""
    resolved_package_probe = package_probe_type if package_probe_type is not None else (detected_types[0] if detected_types else "")
    resolved_res = resolved_type if resolved_type is not None else (managed_type if managed_type != "unknown" else resolved_package_probe)
    return {
        "managed_unit": managed_unit,
        "managed_type": managed_type,
        "detected_types": detected_types,
        "package_probe_type": resolved_package_probe,
        "resolved_type": resolved_res,
        "probe_type": resolved_res,
        "path_binary": path_binary,
        "homebrew_binary": homebrew_binary,
        "execstart": execstart,
        "fragment_path": fragment_path,
    }


def sunshine_binary() -> Optional[str]:
    """Return the Sunshine executable path on the host, or ``None``.

    Resolution order: ``PATH`` -> Homebrew formula prefix -> Flatpak.
    """
    return preferred_launch_binary(probe_packages())


def sunshine_installation_audit(unit: Optional[str] = None) -> SunshineInstallAudit:
    """Return install signals and managed-unit classification for doctor output."""
    managed_unit = unit or sunshine_unit()
    probe = probe_sunshine_service_unit(systemctl_runner=_systemctl_user)

    if managed_unit == probe.unit_name:
        execstart = probe.execstart
        fragment_path = probe.fragment_path
        managed_type = probe.installation_type
        unit_loaded = probe.exists
    else:
        execstart = show_unit_property(managed_unit, "ExecStart")
        fragment_path = show_unit_property(managed_unit, "FragmentPath")
        unit_loaded = bool(managed_unit and _sunshine_unit_exists(managed_unit))
        managed_type = classify_service_unit(managed_unit, execstart, fragment_path)

    probes = probe_packages()
    package_probe_type = probes.detected_types[0] if probes.detected_types else ""
    resolved_type = managed_type if unit_loaded and managed_type != "unknown" else package_probe_type

    return make_sunshine_install_audit(
        managed_unit=managed_unit,
        managed_type=managed_type,
        detected_types=probes.detected_types,
        package_probe_type=package_probe_type,
        resolved_type=resolved_type,
        path_binary=probes.path_binary or "",
        homebrew_binary=probes.homebrew_binary or "",
        execstart=execstart,
        fragment_path=fragment_path,
    )


def detected_sunshine_installation_type() -> str:
    """Return the install type that should drive global Sunshine behavior.

    A loaded systemd unit is the strongest signal because it is the
    service this tool can actually start, stop, and wrap.  Package
    probes are only a fallback for systems where no known user unit is
    loaded yet.
    """
    audit = sunshine_installation_audit()
    return safe_string(audit.get("resolved_type"))


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
