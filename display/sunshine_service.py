"""Sunshine service ownership and systemd override policy.

This module owns everything about how the tool integrates with the
host's Sunshine service unit -- which unit name we manage, which
override file paths are worth visiting, and what counts as "managed
by us" vs "owned by the user".  Installation facts (package probes,
config root, binary resolution, install audit) live in
``sunshine.installation``; this module keeps the live unit as its
truth for service operations.

The durable ownership key is :data:`state.sunshine_unit_name`.
Override paths are derived from that unit name plus
``state.paths.systemd_user_dir`` on every load.  A path being
"associated with" a unit does NOT mean the file at that path was
written by us: only the managed-wrapper marker in the file content
proves ownership.  See :func:`is_managed_sunshine_override`.
"""

from __future__ import annotations

import subprocess
import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional

from display.utils import run_command, safe_string
from sunshine import installation
from sunshine.installation import SUNSHINE_UNIT_CANDIDATES, probe_sunshine_service_unit

if TYPE_CHECKING:
    from display.state import DisplayState


def _systemctl_user(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return run_command(["systemctl", "--user", *args], check=check)


def sunshine_unit() -> str:
    """Return the live Sunshine service unit name on the host.

    Prefers the first loaded + active unit among
    :data:`SUNSHINE_UNIT_CANDIDATES`; falls back to the first loaded
    unit; finally to the canonical ``sunshine.installation.SUNSHINE_UNIT``.
    """
    return probe_sunshine_service_unit(systemctl_runner=_systemctl_user).unit_name


def resolve_sunshine_unit(state: DisplayState) -> str:
    return safe_string(state.sunshine_unit_name) or sunshine_unit()


def parse_systemd_execstart(value: str) -> str:
    raw_value = safe_string(value)
    if not raw_value:
        return ""
    for line in raw_value.splitlines():
        match = re.search(r"argv\[]=(.*?) ;", line)
        if match:
            return safe_string(match.group(1))
    if "argv[]=" in raw_value:
        match = re.search(r"argv\[]=(.*)", raw_value, re.DOTALL)
        if match:
            return safe_string(match.group(1))
    return raw_value


def current_sunshine_execstart(unit: str) -> str:
    return parse_systemd_execstart(show_unit_property(unit, "ExecStart"))


def remember_sunshine_execstart(
    state: DisplayState,
    *,
    resolve_unit_fn: Callable[[DisplayState], str] = resolve_sunshine_unit,
    current_execstart_fn: Callable[[str], str] = current_sunshine_execstart,
    fragment_execstart_fn: Optional[Callable[[str], str]] = None,
    binary_fn: Optional[Callable[[], Optional[str]]] = None,
) -> DisplayState:
    fragment_execstart_fn = fragment_execstart_fn or installation.fragment_sunshine_execstart
    binary_fn = binary_fn or installation.sunshine_binary
    target_unit = resolve_unit_fn(state)
    current_execstart = current_execstart_fn(target_unit) or fragment_execstart_fn(target_unit)
    wrapper_path = state.paths.sunshine_wrapper_script
    if current_execstart and wrapper_path and current_execstart != wrapper_path:
        state.sunshine_execstart = current_execstart
        return state
    if state.sunshine_execstart:
        return state
    state.sunshine_execstart = binary_fn() or "sunshine"
    return state


def is_sunshine_service_active() -> bool:
    return _systemctl_user("is-active", sunshine_unit()).returncode == 0


def managed_sunshine_units(state: Optional[DisplayState] = None) -> List[str]:
    """Return known Sunshine service unit aliases.

    The saved unit from ``state`` (if present) is prepended so reset
    logic cleans up the override we wrote even when its name isn't in
    :data:`SUNSHINE_UNIT_CANDIDATES`.  Duplicates are preserved in
    order; callers that need a set can ``list(dict.fromkeys(...))``.
    """
    units: List[str] = list(SUNSHINE_UNIT_CANDIDATES)
    if state is not None:
        saved_unit = safe_string(state.sunshine_unit_name)
        if saved_unit and saved_unit not in units:
            units.insert(0, saved_unit)
    return units


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


def managed_override_marker(state: DisplayState) -> str:
    """Return the unique substring that proves a Sunshine override
    file was written by this tool.  Empty when the wrapper script
    path is not known.
    """
    return safe_string(state.paths.sunshine_wrapper_script)


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


def override_paths_for_unit(unit: str, state: DisplayState) -> List[Path]:
    """Return the (override_file, override_dir) pair for ``unit``.

    These are paths ASSOCIATED with the unit -- the marker check is
    the only way to determine whether this tool actually wrote the
    file at the override_file location.
    """
    systemd_user_dir = Path(state.paths.systemd_user_dir)
    override_dir = systemd_user_dir / f"{unit}.d"
    return [override_dir / "override.conf", override_dir]


def current_unit_override_paths(state: DisplayState) -> List[Path]:
    """Return override paths for the unit recorded in
    ``state.sunshine_unit_name``.

    The state path is the durable ownership key.  Override paths are
    derived from it on every load, NOT persisted as the path of a
    file we once wrote -- so the marker check is required to confirm
    this tool still owns whatever is at those paths.
    """
    unit = safe_string(state.sunshine_unit_name)
    if not unit:
        return []
    return override_paths_for_unit(unit, state)


def legacy_unit_override_paths(state: DisplayState) -> List[Path]:
    """Return override paths for known Sunshine service units other
    than the one in ``state.sunshine_unit_name``.

    These come from previous installs (Flatpak, native, Homebrew
    stable/beta) and may contain either managed overrides from a
    prior version of this tool or user-supplied overrides.  The
    marker check is the only way to tell them apart.
    """
    saved_unit = safe_string(state.sunshine_unit_name)
    paths: List[Path] = []
    for unit in managed_sunshine_units(state):
        if unit == saved_unit:
            continue
        paths.extend(override_paths_for_unit(unit, state))
    return paths


def cleanup_managed_overrides(state: DisplayState) -> None:
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
