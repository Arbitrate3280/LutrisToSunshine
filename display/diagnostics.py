"""Runtime facts and derived health for the virtual display.

The lifecycle manager owns transitions; this module owns the read-only report
consumed by the CLI views.  The small mapping compatibility methods are kept
only for callers from older releases while production consumers use attrs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Protocol, Sequence

from display import sunshine_service as _svc
from display.constants import (
    PORTAL_ACTIVE_PATH,
    WAYLAND_DISPLAY_PATH,
)
from display.gpu import gpu_status_label
from display.input_isolation import (
    InputDevice,
    KwinDevice,
    KwinInputIsolationStatus,
    empty_kwin_input_isolation_status,
    host_session_name,
    input_isolation_mode,
    kwin_input_isolation_status,
    sunshine_virtual_input_devices,
)
from display.modes import (
    DisplayMode,
    custom_display_mode_string,
    format_refresh_rate_hz,
    refresh_rate_sync_mode_summary,
)
from display.state import DisplayState, load_state
from display.sunshine_service import SunshineInstallAudit
from display.utils import run_command, safe_string


@dataclass(frozen=True)
class DisplaySnapshot:
    configured: bool = False
    dynamic_mangohud_fps_limit: bool = False
    refresh_rate_sync_mode: str = "client"
    custom_display_mode: DisplayMode = field(default_factory=lambda: {"width": 0, "height": 0, "refresh": 0.0})
    custom_display_mode_summary: str = ""
    profile: str = ""
    host_session: str = "unknown"
    input_isolation_mode: str = "permissions-only"
    sunshine_unit: str = ""
    sunshine_install_audit: SunshineInstallAudit = field(default_factory=lambda: {
        "managed_unit": "",
        "managed_type": "unknown",
        "detected_types": [],
        "package_probe_type": "",
        "resolved_type": "",
        "probe_type": "",
        "path_binary": "",
        "homebrew_binary": "",
        "execstart": "",
        "fragment_path": "",
    })
    sunshine_active: bool = False
    sway_active: bool = False
    wireplumber_policy: str = "absent"
    audio_sink: str = ""
    wayland_display: str = ""
    current_headless_mode: str = ""
    sway_socket: str = ""
    udev_rule_path: str = ""
    udev_rule_present: bool = False
    kwin_isolation_state: str = "inactive"
    kwin_isolation_service: str = ""
    kwin_isolation_error: str = ""
    kwin_isolation_seen_device_count: int = 0
    kwin_isolation_devices: List[KwinDevice] = field(default_factory=list)
    kwin_isolation_failed_devices: List[KwinDevice] = field(default_factory=list)
    sunshine_input_devices: List[InputDevice] = field(default_factory=list)
    sunshine_input_device_count: int = 0
    portal_handoff_active: bool = False
    current_mangohud_config: str = ""
    last_launch_log_file: str = ""
    dependencies_missing: List[str] = field(default_factory=list)
    gpu_mode: str = "auto"
    gpu_card_path: str = ""
    gpu_render_path: str = ""
    gpu_status_label: str = ""
    renderer_mode: str = "default"
    renderer_status_label: str = ""
    next_step: str = ""
    status_summary: str = "NOT SET UP"
    display_sync_summary: str = ""
    refresh_rate_sync_mode_summary: str = ""
    isolation_level: str = "success"
    isolation_summary: str = "rule ready"

    def __getitem__(self, key: str) -> object:
        return asdict(self)[key]

    def get(self, key: str, default: object = None) -> object:
        return asdict(self).get(key, default)


def _legacy_string(value: Mapping[str, object], key: str, default: str) -> str:
    candidate = value.get(key)
    return candidate if isinstance(candidate, str) else default


def _legacy_bool(value: Mapping[str, object], key: str, default: bool) -> bool:
    candidate = value.get(key)
    return candidate if isinstance(candidate, bool) else default


def _legacy_int(value: Mapping[str, object], key: str, default: int) -> int:
    candidate = value.get(key)
    return candidate if isinstance(candidate, int) and not isinstance(candidate, bool) else default


def _legacy_string_list(value: Mapping[str, object], key: str) -> List[str]:
    candidate = value.get(key)
    return [item for item in candidate if isinstance(item, str)] if isinstance(candidate, list) else []


def _legacy_display_mode(value: Mapping[str, object]) -> DisplayMode:
    candidate = value.get("custom_display_mode")
    if not isinstance(candidate, Mapping):
        return {"width": 0, "height": 0, "refresh": 0.0}
    width = candidate.get("width")
    height = candidate.get("height")
    refresh = candidate.get("refresh")
    if (
        isinstance(width, int)
        and not isinstance(width, bool)
        and isinstance(height, int)
        and not isinstance(height, bool)
        and isinstance(refresh, (int, float))
        and not isinstance(refresh, bool)
    ):
        return {"width": width, "height": height, "refresh": float(refresh)}
    return {"width": 0, "height": 0, "refresh": 0.0}


def _legacy_sunshine_audit(value: Mapping[str, object]) -> SunshineInstallAudit:
    defaults: SunshineInstallAudit = {
        "managed_unit": "",
        "managed_type": "unknown",
        "detected_types": [],
        "package_probe_type": "",
        "resolved_type": "",
        "probe_type": "",
        "path_binary": "",
        "homebrew_binary": "",
        "execstart": "",
        "fragment_path": "",
    }
    candidate = value.get("sunshine_install_audit")
    if not isinstance(candidate, Mapping):
        return defaults
    for key in (
        "managed_unit",
        "managed_type",
        "package_probe_type",
        "resolved_type",
        "probe_type",
        "path_binary",
        "homebrew_binary",
        "execstart",
        "fragment_path",
    ):
        raw = candidate.get(key)
        if isinstance(raw, str):
            defaults[key] = raw
    defaults["detected_types"] = [
        item for item in candidate.get("detected_types", []) if isinstance(item, str)
    ] if isinstance(candidate.get("detected_types"), list) else []
    return defaults


def _legacy_devices(
    value: Mapping[str, object],
    key: str,
    path_key: str,
) -> List[dict[str, str]]:
    candidate = value.get(key)
    if not isinstance(candidate, list):
        return []
    devices: List[dict[str, str]] = []
    for item in candidate:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        path = item.get(path_key)
        if isinstance(name, str) and isinstance(path, str):
            devices.append({"name": name, path_key: path})
    return devices


def _legacy_snapshot(value: Mapping[str, object]) -> DisplaySnapshot:
    """Validate the old mapping surface before entering typed code."""
    string_fields = (
        "refresh_rate_sync_mode",
        "custom_display_mode_summary",
        "profile",
        "host_session",
        "input_isolation_mode",
        "sunshine_unit",
        "wireplumber_policy",
        "audio_sink",
        "wayland_display",
        "current_headless_mode",
        "sway_socket",
        "udev_rule_path",
        "kwin_isolation_state",
        "kwin_isolation_service",
        "kwin_isolation_error",
        "current_mangohud_config",
        "last_launch_log_file",
        "gpu_mode",
        "gpu_card_path",
        "gpu_render_path",
        "gpu_status_label",
        "renderer_mode",
        "renderer_status_label",
        "next_step",
        "status_summary",
        "display_sync_summary",
        "refresh_rate_sync_mode_summary",
        "isolation_level",
        "isolation_summary",
    )
    bool_fields = (
        "configured",
        "dynamic_mangohud_fps_limit",
        "sunshine_active",
        "sway_active",
        "udev_rule_present",
        "portal_handoff_active",
    )
    int_fields = ("kwin_isolation_seen_device_count", "sunshine_input_device_count")
    string_defaults = {
        "refresh_rate_sync_mode": "client",
        "host_session": "unknown",
        "input_isolation_mode": "permissions-only",
        "wireplumber_policy": "absent",
        "gpu_mode": "auto",
        "renderer_mode": "default",
        "status_summary": "NOT SET UP",
        "isolation_level": "success",
        "isolation_summary": "rule ready",
    }
    values: Dict[str, object] = {
        key: _legacy_string(value, key, string_defaults.get(key, ""))
        for key in string_fields
    }
    values.update({key: _legacy_bool(value, key, False) for key in bool_fields})
    values.update({key: _legacy_int(value, key, 0) for key in int_fields})
    values["dependencies_missing"] = _legacy_string_list(value, "dependencies_missing")
    values["custom_display_mode"] = _legacy_display_mode(value)
    values["sunshine_install_audit"] = _legacy_sunshine_audit(value)
    values["kwin_isolation_devices"] = _legacy_devices(value, "kwin_isolation_devices", "path")
    values["kwin_isolation_failed_devices"] = _legacy_devices(value, "kwin_isolation_failed_devices", "path")
    values["sunshine_input_devices"] = _legacy_devices(value, "sunshine_input_devices", "event_path")
    return DisplaySnapshot(**values)


def as_display_snapshot(value: DisplaySnapshot | Mapping[str, object]) -> DisplaySnapshot:
    """Adapt the pre-DTO mapping API at one validated compatibility boundary."""
    if isinstance(value, DisplaySnapshot):
        return value
    return _legacy_snapshot(value)


@dataclass(frozen=True)
class DoctorCheck:
    label: str
    status: str
    message: str

    def __getitem__(self, key: str) -> str:
        if key == "label":
            return self.label
        if key == "status":
            return self.status
        if key == "message":
            return self.message
        raise KeyError(key)

    def get(self, key: str, default: object = None) -> object:
        try:
            return self[key]
        except KeyError:
            return default


@dataclass(frozen=True)
class DoctorReport:
    summary: str
    checks: List[DoctorCheck]
    next_step: str

    def __getitem__(self, key: str) -> object:
        if key == "summary":
            return self.summary
        if key == "checks":
            return self.checks
        if key == "next_step":
            return self.next_step
        raise KeyError(key)

    def get(self, key: str, default: object = None) -> object:
        try:
            return self[key]
        except KeyError:
            return default


class CommandRunner(Protocol):
    def __call__(
        self,
        command: Sequence[str],
        *,
        check: bool = False,
    ) -> subprocess.CompletedProcess: ...


def _active_launch_status(state: DisplayState) -> Dict[str, str]:
    path = Path(state.paths.portal_active_file)
    if not path.exists():
        return {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    result: Dict[str, str] = {}
    for line in lines:
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = safe_string(key)
        if key:
            result[key] = value.strip()
    return result


def current_headless_mode(
    state: DisplayState,
    sunshine_active: bool,
    sway_active: bool,
    runner: CommandRunner = run_command,
) -> str:
    if not state.enabled or not sunshine_active or not sway_active:
        return ""
    if not state.sway_socket or not Path(state.sway_socket).exists():
        return ""
    result = runner(["swaymsg", "-s", state.sway_socket, "-t", "get_outputs", "-r"], check=False)
    if result.returncode != 0 or not safe_string(result.stdout):
        return ""
    try:
        outputs = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(outputs, list):
        return ""
    for output in outputs:
        if not isinstance(output, dict) or safe_string(output.get("name")) != "HEADLESS-1":
            continue
        mode = output.get("current_mode")
        if not isinstance(mode, dict):
            return ""
        width, height = mode.get("width"), mode.get("height")
        refresh = format_refresh_rate_hz(mode.get("refresh"))
        if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0 or not refresh:
            return ""
        return f"{width}x{height} @ {refresh} Hz"
    return ""


def missing_dependencies(which_fn: Callable[[str], Optional[str]] = shutil.which) -> List[str]:
    missing = [
        name
        for name in ["flock", "gdbus", "pactl", "python3", "setfacl", "stdbuf", "sway", "swaymsg", "systemctl"]
        if which_fn(name) is None
    ]
    if _svc.sunshine_binary() is None and not _svc.show_unit_property(_svc.sunshine_unit(), "ExecStart"):
        missing.append("sunshine")
    return missing


def renderer_status_label(state: DisplayState) -> str:
    if state.renderer_mode == "vulkan":
        return "[VULKAN] HDR capable, may have less GPU support"
    return "[DEFAULT] GLES2 — stable, broad GPU support, no HDR"


def _with_derived_status(
    snapshot: DisplaySnapshot,
    *,
    configured: bool,
    sunshine_active: bool,
    sway_active: bool,
    input_mode: str,
    input_devices: List[InputDevice],
) -> DisplaySnapshot:
    if not configured:
        next_step = "Run 'python3 lutristosunshine.py display enable' to set up the headless stack."
    elif not sunshine_active:
        next_step = "Run 'python3 lutristosunshine.py display start' to start the managed stack."
    elif not sway_active:
        next_step = "Run 'python3 lutristosunshine.py display status' or '... display logs' to inspect why headless Sway is not ready."
    else:
        next_step = "Virtual display is ready. Use 'status' or 'logs' for follow-up actions."

    if snapshot.dependencies_missing or not configured:
        status_summary = "NOT SET UP"
    elif sunshine_active and sway_active:
        status_summary = "READY"
    elif sunshine_active or sway_active:
        status_summary = "PARTIAL"
    else:
        status_summary = "STOPPED"

    isolation_level = "success"
    isolation_summary = "rule ready"
    if input_mode == "kwin-runtime-disable":
        if snapshot.kwin_isolation_error:
            isolation_level, isolation_summary = "error", snapshot.kwin_isolation_error
        elif snapshot.kwin_isolation_state == "inactive":
            isolation_level, isolation_summary = "warning", "KWin helper is not running"
        elif snapshot.kwin_isolation_state == "starting":
            isolation_level, isolation_summary = "info", "KWin helper is starting"
        elif snapshot.kwin_isolation_devices:
            isolation_summary = f"disabled {len(snapshot.kwin_isolation_devices)} of {len(input_devices)} Sunshine device(s) in KWin"
        elif input_devices:
            isolation_level = "warning"
            isolation_summary = f"matched {snapshot.kwin_isolation_seen_device_count} of {len(input_devices)} Sunshine device(s), but disabled 0"
        else:
            isolation_level, isolation_summary = "warning", "waiting for Sunshine virtual inputs"

    values = asdict(snapshot)
    values.update(
        next_step=next_step,
        status_summary=status_summary,
        isolation_level=isolation_level,
        isolation_summary=isolation_summary,
    )
    return DisplaySnapshot(**values)


def display_snapshot(
    *,
    load_state_fn: Optional[Callable[[], DisplayState]] = None,
    ensure_dependencies_fn: Optional[Callable[[], List[str]]] = None,
    current_headless_mode_fn: Optional[Callable[[DisplayState, bool, bool], str]] = None,
    sunshine_active_fn: Optional[Callable[[], bool]] = None,
    sunshine_unit_fn: Optional[Callable[[], str]] = None,
    host_session_fn: Optional[Callable[[], str]] = None,
    input_isolation_mode_fn: Optional[Callable[[], str]] = None,
    kwin_status_fn: Optional[Callable[[DisplayState], KwinInputIsolationStatus]] = None,
    empty_kwin_status_fn: Optional[Callable[[], KwinInputIsolationStatus]] = None,
    sunshine_input_devices_fn: Optional[Callable[[], List[InputDevice]]] = None,
    renderer_status_fn: Optional[Callable[[DisplayState], str]] = None,
) -> DisplaySnapshot:
    load_state_fn = load_state_fn or load_state
    ensure_dependencies_fn = ensure_dependencies_fn or missing_dependencies
    current_headless_mode_fn = current_headless_mode_fn or current_headless_mode
    sunshine_active_fn = sunshine_active_fn or _svc.is_sunshine_service_active
    sunshine_unit_fn = sunshine_unit_fn or _svc.sunshine_unit
    host_session_fn = host_session_fn or host_session_name
    input_isolation_mode_fn = input_isolation_mode_fn or input_isolation_mode
    kwin_status_fn = kwin_status_fn or kwin_input_isolation_status
    empty_kwin_status_fn = empty_kwin_status_fn or empty_kwin_input_isolation_status
    sunshine_input_devices_fn = sunshine_input_devices_fn or sunshine_virtual_input_devices
    renderer_status_fn = renderer_status_fn or renderer_status_label
    state = load_state_fn()
    configured = state.enabled
    sunshine_active = sunshine_active_fn() if configured else False
    sunshine_unit = state.sunshine_unit_name or sunshine_unit_fn()
    wayland_display = ""
    if WAYLAND_DISPLAY_PATH.exists():
        wayland_display = WAYLAND_DISPLAY_PATH.read_text(encoding="utf-8").strip()
    sway_active = bool(
        configured
        and sunshine_active
        and Path(state.sway_socket).exists()
        and WAYLAND_DISPLAY_PATH.exists()
    )
    active_launch_status = _active_launch_status(state)
    input_mode = input_isolation_mode_fn()
    kwin_status = kwin_status_fn(state) if configured else empty_kwin_status_fn()
    input_devices = sunshine_input_devices_fn() if configured else []
    current_mode = current_headless_mode_fn(state, sunshine_active, sway_active)
    custom_mode = state.custom_display_mode
    mode_summary = refresh_rate_sync_mode_summary(state.refresh_rate_sync_mode)
    if state.refresh_rate_sync_mode == "custom":
        mode_summary = f"{mode_summary} ({custom_display_mode_string(custom_mode)})"

    snapshot = DisplaySnapshot(
        configured=configured,
        dynamic_mangohud_fps_limit=state.dynamic_mangohud_fps_limit,
        refresh_rate_sync_mode=state.refresh_rate_sync_mode,
        custom_display_mode=custom_mode,
        custom_display_mode_summary=custom_display_mode_string(custom_mode),
        profile=state.profile,
        host_session=host_session_fn(),
        input_isolation_mode=input_mode,
        sunshine_unit=sunshine_unit,
        sunshine_install_audit=_svc.sunshine_installation_audit(sunshine_unit),
        sunshine_active=sunshine_active,
        sway_active=sway_active,
        wireplumber_policy=("installed" if Path(state.paths.wireplumber_policy_conf).exists() else "absent"),
        audio_sink=state.audio_sink,
        wayland_display=wayland_display,
        current_headless_mode=current_mode,
        sway_socket=state.sway_socket,
        udev_rule_path=state.udev_rule_path,
        udev_rule_present=Path(state.udev_rule_path).exists(),
        kwin_isolation_state=safe_string(kwin_status.get("state")),
        kwin_isolation_service=safe_string(kwin_status.get("service")),
        kwin_isolation_error=safe_string(kwin_status.get("last_error")),
        kwin_isolation_seen_device_count=int(kwin_status.get("seen_device_count", 0) or 0),
        kwin_isolation_devices=kwin_status.get("disabled_devices") or [],
        kwin_isolation_failed_devices=kwin_status.get("failed_devices") or [],
        sunshine_input_devices=input_devices,
        sunshine_input_device_count=len(input_devices),
        portal_handoff_active=PORTAL_ACTIVE_PATH.exists(),
        current_mangohud_config=safe_string(active_launch_status.get("mangohud_config")),
        last_launch_log_file=state.paths.last_launch_log_file,
        dependencies_missing=ensure_dependencies_fn(),
        gpu_mode=state.gpu_mode,
        gpu_card_path=state.gpu_card_path,
        gpu_render_path=state.gpu_render_path,
        gpu_status_label=gpu_status_label(state),
        renderer_mode=state.renderer_mode,
        renderer_status_label=renderer_status_fn(state),
        refresh_rate_sync_mode_summary=refresh_rate_sync_mode_summary(state.refresh_rate_sync_mode),
        display_sync_summary=(
            f"{mode_summary}; MangoHud FPS sync "
            f"{'on' if state.dynamic_mangohud_fps_limit else 'off'}"
        ),
    )

    return _with_derived_status(
        snapshot,
        configured=configured,
        sunshine_active=sunshine_active,
        sway_active=sway_active,
        input_mode=input_mode,
        input_devices=input_devices,
    )


def _doctor_host_and_dependencies(snapshot: DisplaySnapshot) -> List[DoctorCheck]:
    checks = [
        DoctorCheck(
            "Host session",
            "pass" if snapshot.host_session != "unknown" else "info",
            (
                f"{snapshot.host_session} detected; using {snapshot.input_isolation_mode}."
                if snapshot.host_session != "unknown"
                else f"Host session not detected; using {snapshot.input_isolation_mode}."
            ),
        )
    ]
    checks.append(
        DoctorCheck(
            "Dependencies",
            "fail" if snapshot.dependencies_missing else "pass",
            f"Missing required commands: {', '.join(snapshot.dependencies_missing)}"
            if snapshot.dependencies_missing
            else "Required commands are available.",
        )
    )
    return checks


def _doctor_installation_check(snapshot: DisplaySnapshot) -> Optional[DoctorCheck]:
    audit = snapshot.sunshine_install_audit
    detected_types = audit.get("detected_types") or []
    managed_type = safe_string(audit.get("managed_type"))
    package_probe_type = safe_string(audit.get("package_probe_type"))
    resolved_type = safe_string(audit.get("resolved_type"))
    if not (
        len(detected_types) > 1
        and managed_type
        and managed_type != "unknown"
        and package_probe_type
        and package_probe_type != managed_type
    ):
        return None
    return DoctorCheck(
        "Sunshine installation",
        "warn",
        f"Multiple Sunshine installs detected ({', '.join(detected_types)}). Virtual display manages {audit['managed_unit']} ({managed_type}) and resolves Sunshine as {resolved_type}; package-only probing would resolve {package_probe_type}.",
    )


def _doctor_kwin_check(snapshot: DisplaySnapshot) -> DoctorCheck:
    active_but_not_isolated = (
        snapshot.kwin_isolation_state == "active"
        and snapshot.sunshine_input_device_count > 0
        and not snapshot.kwin_isolation_devices
    )
    if snapshot.kwin_isolation_state == "failed" and snapshot.kwin_isolation_error:
        message = snapshot.kwin_isolation_error
    elif snapshot.kwin_isolation_state == "inactive":
        message = "KWin helper is not running."
    elif snapshot.kwin_isolation_state == "starting":
        message = "KWin helper is starting."
    elif snapshot.kwin_isolation_state == "active" and snapshot.kwin_isolation_devices:
        message = f"KWin helper active via {snapshot.kwin_isolation_service or 'gdbus'}; disabled {len(snapshot.kwin_isolation_devices)} of {snapshot.sunshine_input_device_count} Sunshine input device(s)."
    elif active_but_not_isolated:
        message = f"KWin helper active via {snapshot.kwin_isolation_service or 'gdbus'}; matched {snapshot.kwin_isolation_seen_device_count} of {snapshot.sunshine_input_device_count} host Sunshine input device(s), but disabled 0."
    else:
        message = "KWin helper is waiting for Sunshine virtual input devices to appear."
    status = "warn" if snapshot.kwin_isolation_state in {"inactive", "failed"} or active_but_not_isolated else "pass"
    return DoctorCheck("KWin isolation", status, message)


def _doctor_configured_checks(snapshot: DisplaySnapshot) -> List[DoctorCheck]:
    if not snapshot.configured:
        return [DoctorCheck("Setup", "fail", "Virtual display is not configured.")]

    checks = [
        DoctorCheck("Setup", "pass", "Managed files are installed."),
        DoctorCheck(
            "Managed udev rule",
            "pass" if snapshot.udev_rule_present else "warn",
            "Input permissions rule is present." if snapshot.udev_rule_present else "Managed udev rule is missing.",
        ),
    ]
    if snapshot.input_isolation_mode == "kwin-runtime-disable":
        checks.append(_doctor_kwin_check(snapshot))
    checks.extend(
        [
            DoctorCheck(
                "Sunshine service",
                "pass" if snapshot.sunshine_active else "warn",
                "Managed Sunshine unit is active." if snapshot.sunshine_active else "Managed Sunshine unit is not active.",
            ),
            DoctorCheck(
                "Headless display",
                "pass" if snapshot.sway_active else "warn",
                f"Headless Wayland display is ready ({snapshot.wayland_display})."
                if snapshot.sway_active
                else "Headless Wayland display is not ready.",
            ),
        ]
    )
    return checks


def display_doctor_report(snapshot: Optional[DisplaySnapshot] = None) -> DoctorReport:
    snapshot = as_display_snapshot(snapshot or display_snapshot())
    checks = _doctor_host_and_dependencies(snapshot)
    installation_check = _doctor_installation_check(snapshot)
    if installation_check:
        checks.append(installation_check)
    checks.extend(_doctor_configured_checks(snapshot))
    summary = (
        "needs_attention"
        if any(item.status == "fail" for item in checks)
        else "degraded"
        if any(item.status == "warn" for item in checks)
        else "healthy"
    )
    return DoctorReport(summary, checks, snapshot.next_step)
