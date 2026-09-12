"""Sunshine installation resolution.

Owns the resolved fact set of which Sunshine exists on this host:
package/systemd probes, the persisted user choice when several
installs are detected, config-root and binary resolution, and the
install audit consumed by doctor output.

Resolution is stateless: every :func:`resolve_installation` call runs
one full probe pass; nothing is cached.  Multiple detections require
an explicit user choice, persisted in the tool settings store
(``~/.config/lutristosunshine/settings.json``); this module never prompts.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple, TypedDict

from display.utils import safe_string
from config.settings import load_settings, update_setting
from utils.utils import run_command

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

HOMEBREW_SUNSHINE_FORMULAE: Tuple[str, ...] = ("sunshine", "sunshine-beta")

INSTALL_CHOICE_FILENAME = "server_connection.json"


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
        result = run_command([brew, "--prefix", formula])
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
            result = run_command([flatpak, "info", "dev.lizardbyte.app.Sunshine"])
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
        return run_command([systemctl, "--user", *args])
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


def _unit_property(unit: str, property_name: str) -> str:
    result = _default_systemctl_runner("show", f"--property={property_name}", "--value", unit)
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def _unit_loaded(unit: str) -> bool:
    return _unit_property(unit, "LoadState") == "loaded"


def config_root_candidates() -> List[Path]:
    return [
        Path("~/.config/sunshine").expanduser(),
        Path("~/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine").expanduser(),
    ]


def detect_sunshine_config_root() -> Path:
    candidates = config_root_candidates()
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def fragment_sunshine_execstart(unit_name: str) -> str:
    fragment_path = Path(safe_string(_unit_property(unit_name, "FragmentPath")))
    if not fragment_path.is_file():
        return ""
    in_service_section = False
    try:
        for raw_line in fragment_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                in_service_section = line == "[Service]"
                continue
            if in_service_section and line.startswith("ExecStart="):
                return safe_string(line.split("=", 1)[1])
    except OSError:
        pass
    return ""


def resolve_sunshine_config_root(
    unit_name: str,
    *,
    fragment_execstart_fn: Optional[Callable[[str], str]] = None,
    detect_root_fn: Optional[Callable[[], Path]] = None,
) -> Path:
    """Resolve the config root from the managed unit's actual executable."""
    fragment_execstart_fn = fragment_execstart_fn or fragment_sunshine_execstart
    detect_root_fn = detect_root_fn or detect_sunshine_config_root
    executable = fragment_execstart_fn(unit_name)
    if executable and "flatpak" in executable.lower():
        flatpak_id = unit_name.removeprefix("app-").removesuffix(".service")
        return Path.home() / ".var" / "app" / flatpak_id / "config" / "sunshine"
    if executable:
        return detect_root_fn()
    if unit_name and unit_name.startswith("app-"):
        flatpak_id = unit_name.removeprefix("app-").removesuffix(".service")
        return Path.home() / ".var" / "app" / flatpak_id / "config" / "sunshine"
    return detect_root_fn()


def sunshine_binary() -> Optional[str]:
    """Return the Sunshine executable path on the host, or ``None``.

    Resolution order: ``PATH`` -> Homebrew formula prefix -> Flatpak.
    """
    return preferred_launch_binary(probe_packages())


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


def sunshine_installation_audit(unit: Optional[str] = None) -> SunshineInstallAudit:
    """Return install signals and managed-unit classification for doctor output."""
    probe = probe_sunshine_service_unit()
    managed_unit = unit or probe.unit_name

    if managed_unit == probe.unit_name:
        execstart = probe.execstart
        fragment_path = probe.fragment_path
        managed_type = probe.installation_type
        unit_loaded = probe.exists
    else:
        execstart = _unit_property(managed_unit, "ExecStart")
        fragment_path = _unit_property(managed_unit, "FragmentPath")
        unit_loaded = bool(managed_unit and _unit_loaded(managed_unit))
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


@dataclass(frozen=True)
class SunshineInstallation:
    installed: bool
    install_type: Optional[str]      # None ⇔ ambiguous: caller prompts or refuses
    detected_types: List[str]
    unit: SunshineServiceUnitProbe
    packages: SunshinePackageProbes


def _install_choice_path() -> Path:
    return Path(detect_sunshine_config_root()) / INSTALL_CHOICE_FILENAME


def _legacy_install_choice() -> Optional[str]:
    """Read the install choice from the pre-settings.json location."""
    try:
        settings = json.loads(_install_choice_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(settings, dict):
        return None
    sunshine_section = settings.get("sunshine")
    if not isinstance(sunshine_section, dict):
        return None
    saved = sunshine_section.get("install_type")
    return saved if isinstance(saved, str) else None


def load_install_choice(detected_types: List[str]) -> Optional[str]:
    """Return the saved install choice if it is still valid, else ``None``."""
    saved = load_settings().get("sunshine_install")
    if not isinstance(saved, str):
        saved = _legacy_install_choice()
    if isinstance(saved, str) and saved in detected_types:
        return saved
    return None


def save_install_choice(install_type: str) -> None:
    """Persist the user's install choice to the tool settings store."""
    update_setting("sunshine_install", install_type)


def resolve_installation() -> SunshineInstallation:
    """Run one full probe pass and resolve the installation facts."""
    packages = probe_packages()
    detected_types = packages.detected_types
    if not detected_types:
        return SunshineInstallation(
            installed=False,
            install_type=None,
            detected_types=detected_types,
            unit=SunshineServiceUnitProbe(unit_name=SUNSHINE_UNIT),
            packages=packages,
        )
    if len(detected_types) == 1:
        install_type: Optional[str] = detected_types[0]
    else:
        install_type = load_install_choice(detected_types)
    unit = probe_sunshine_service_unit()
    return SunshineInstallation(
        installed=True,
        install_type=install_type,
        detected_types=detected_types,
        unit=unit,
        packages=packages,
    )
