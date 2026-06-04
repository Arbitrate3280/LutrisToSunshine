"""Sunshine installation and service unit detection policy."""

from __future__ import annotations

import os
import shutil
import glob
import subprocess
from dataclasses import dataclass, field
from typing import Tuple, Optional, List, Callable

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

@dataclass
class SunshinePackageProbes:
    flatpak_installed: bool = False
    homebrew_binary: Optional[str] = None
    path_binary: Optional[str] = None
    appimage_binary: Optional[str] = None
    detected_types: List[str] = field(default_factory=list)


@dataclass
class SunshineServiceUnitProbe:
    unit_name: str
    exists: bool = False
    active: bool = False
    execstart: str = ""
    fragment_path: str = ""
    installation_type: str = "unknown"


def classify_service_unit(unit: str, execstart: str, fragment_path: str) -> str:
    """Classify a service unit name, ExecStart, and FragmentPath into an installation type."""
    probe = " ".join([unit, execstart, fragment_path]).lower()
    if "homebrew" in unit or ".linuxbrew" in probe or "/linuxbrew/" in probe:
        return "homebrew"
    if "flatpak" in probe:
        return "flatpak"
    if unit in {SUNSHINE_UNIT, FALLBACK_SUNSHINE_UNIT} or "/usr/bin/sunshine" in probe:
        return "native"
    return "unknown"


def probe_packages() -> SunshinePackageProbes:
    """Run package-only probes for Sunshine installations."""
    flatpak_installed = False
    flatpak = shutil.which("flatpak")
    if flatpak:
        try:
            result = subprocess.run(
                [flatpak, "info", "dev.lizardbyte.app.Sunshine"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            flatpak_installed = (result.returncode == 0)
        except (OSError, subprocess.SubprocessError):
            flatpak_installed = False

    homebrew_bin = homebrew_sunshine_binary()

    raw_path_binary = shutil.which("sunshine")
    path_binary = None
    if raw_path_binary:
        is_brew = ".linuxbrew" in raw_path_binary or "/linuxbrew/" in raw_path_binary
        if is_brew:
            if not homebrew_bin:
                homebrew_bin = raw_path_binary
        else:
            path_binary = raw_path_binary

    appimage_bin = None
    appimage_paths = (
        glob.glob(os.path.expanduser("~/sunshine.AppImage")) +
        glob.glob(os.path.expanduser("~/.local/share/applications/sunshine.AppImage")) +
        glob.glob(os.path.expanduser("~/AppImages/sunshine.AppImage")) +
        glob.glob(os.path.expanduser("~/bin/sunshine.AppImage")) +
        glob.glob(os.path.expanduser("~/Downloads/sunshine.AppImage"))
    )
    if appimage_paths:
        appimage_bin = appimage_paths[0]

    detected_types = []
    if flatpak_installed:
        detected_types.append("flatpak")
    if homebrew_bin:
        detected_types.append("homebrew")
    if path_binary:
        detected_types.append("native")
    if appimage_bin:
        detected_types.append("appimage")

    return SunshinePackageProbes(
        flatpak_installed=flatpak_installed,
        homebrew_binary=homebrew_bin,
        path_binary=path_binary,
        appimage_binary=appimage_bin,
        detected_types=detected_types,
    )


def preferred_install_type(detected_types: List[str]) -> Optional[str]:
    """Return the preferred installation type based on detected types.

    Preference order: flatpak -> homebrew -> native -> appimage.
    """
    for t in ("flatpak", "homebrew", "native", "appimage"):
        if t in detected_types:
            return t
    return None


def preferred_launch_binary(probes: SunshinePackageProbes) -> Optional[str]:
    """Return the preferred binary path/command to run Sunshine.

    Preference order: Native PATH -> Homebrew -> Flatpak -> AppImage.
    """
    if probes.path_binary:
        return probes.path_binary
    if probes.homebrew_binary:
        return probes.homebrew_binary
    if probes.flatpak_installed:
        flatpak = shutil.which("flatpak")
        if flatpak:
            return f"{flatpak} run {SUNSHINE_FLATPAK_ID}"
    if probes.appimage_binary:
        return probes.appimage_binary
    return None


def _default_systemctl_runner(*args: str) -> subprocess.CompletedProcess:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return subprocess.CompletedProcess(list(args), 1, "", "")
    try:
        return subprocess.run(
            [systemctl, "--user", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(list(args), 1, "", "")


def probe_sunshine_service_unit(
    systemctl_runner: Optional[Callable[..., subprocess.CompletedProcess]] = None
) -> SunshineServiceUnitProbe:
    """Query systemd to find the active/loaded Sunshine unit and its properties."""
    runner = systemctl_runner or _default_systemctl_runner

    # Find loaded units among candidates
    loaded_units = []
    for candidate in SUNSHINE_UNIT_CANDIDATES:
        try:
            res = runner("show", "--property=LoadState", "--value", candidate)
            if res.returncode == 0 and (res.stdout or "").strip() == "loaded":
                loaded_units.append(candidate)
        except Exception:
            continue

    if not loaded_units:
        return SunshineServiceUnitProbe(unit_name=SUNSHINE_UNIT)

    # Find the active unit
    active_unit = None
    for unit in loaded_units:
        try:
            is_active_res = runner("is-active", unit)
            if is_active_res.returncode == 0:
                active_unit = unit
                break
        except Exception:
            continue

    selected_unit = active_unit or loaded_units[0]

    # Query Id, ExecStart, and FragmentPath for the selected unit
    unit_name = selected_unit
    execstart = ""
    fragment_path = ""
    try:
        res_id = runner("show", "--property=Id", "--value", selected_unit)
        if res_id.returncode == 0:
            val = (res_id.stdout or "").strip()
            if val:
                unit_name = val
        res_exec = runner("show", "--property=ExecStart", "--value", selected_unit)
        if res_exec.returncode == 0:
            execstart = (res_exec.stdout or "").strip()
        res_frag = runner("show", "--property=FragmentPath", "--value", selected_unit)
        if res_frag.returncode == 0:
            fragment_path = (res_frag.stdout or "").strip()
    except Exception:
        pass

    installation_type = classify_service_unit(unit_name, execstart, fragment_path)

    return SunshineServiceUnitProbe(
        unit_name=unit_name,
        exists=True,
        active=(active_unit is not None),
        execstart=execstart,
        fragment_path=fragment_path,
        installation_type=installation_type,
    )


def detect_sunshine_installation(service_unit_type: Optional[str] = None) -> Tuple[bool, str]:
    """Detect if Sunshine is installed and how."""
    if service_unit_type:
        return True, service_unit_type

    probes = probe_packages()
    if not probes.detected_types:
        return False, ""

    if len(probes.detected_types) == 1:
        return True, probes.detected_types[0]

    # Resolve ambiguity using active systemd user unit (if any)
    probe_unit = probe_sunshine_service_unit()
    if probe_unit.exists and probe_unit.installation_type in probes.detected_types:
        return True, probe_unit.installation_type

    pref = preferred_install_type(probes.detected_types)
    if pref:
        return True, pref
    return False, ""
