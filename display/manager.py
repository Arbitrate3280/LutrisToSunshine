import glob
import grp
import hashlib
import json
import os
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.input import get_user_input, get_yes_no_input
from display import audio_policy
from display import sunshine_service as _svc
from display.utils import run_command, safe_string
from display import scripts_render
from display.state import DisplayPaths, DisplayState, build_paths, default_state, load_state, save_state
_default_state = default_state
from display.gpu import gpu_status_label
from display.input_isolation import (
    clean_kde_libinput_config as _clean_kde_libinput_config,
    current_user_group,
    current_user_name,
    empty_kwin_input_isolation_status as _empty_kwin_input_isolation_status,
    host_session_name as _host_session_name,
    input_isolation_mode as _input_isolation_mode,
    install_udev_rule as _install_udev_rule,
    kwin_input_isolation_status as _kwin_input_isolation_status,
    remove_udev_rule as _remove_udev_rule,
    sunshine_virtual_input_devices as _sunshine_virtual_input_devices,
    udev_rule as _udev_rule,
)


from display.constants import (
    AUDIO_MODULE_PATH,
    BIN_ROOT,
    CONFIG_ROOT,
    DISPLAY_ROOT,
    DISPLAY_SOCKET_PATH,
    DISPLAY_STATE_PATH,
    FALLBACK_FPS,
    FALLBACK_HEIGHT,
    FALLBACK_WIDTH,
    FLATPAK_FLAG_OPTIONS,
    FLATPAK_PORTAL_ENV_KEYS,
    FLATPAK_PORTAL_RESTORE_GRACE,
    FLATPAK_PORTAL_SPAWN_TIMEOUT,
    FLATPAK_PORTAL_SWITCH_TIMEOUT,
    FLATPAK_PORTAL_UNIT,
    FLATPAK_SPAWN_HOST_PREFIX,
    FLATPAK_VALUE_OPTIONS,
    FLATPAK_VALUE_PREFIXES,
    LAST_LAUNCH_LOG_PATH,
    LEGACY_DISPLAY_DIRNAME,
    LEGACY_DISPLAY_ROOT,
    LEGACY_STATE_PATH,
    PORTAL_ACTIVE_PATH,
    PORTAL_LOCK_PATH,
    PROFILE_NAME,
    PROFILE_ROOT,
    REFRESH_RATE_SYNC_MODES,
    SUNSHINE_INPUT_NAME_MARKERS,
    SUNSHINE_INPUT_PRODUCT_ID,
    SUNSHINE_INPUT_VENDOR_ID,
    UDEV_RULE_PATH,
    VIRTUALDISPLAY_SANDBOX_UNSET_VARS,
    WAYLAND_DISPLAY_PATH,
    _PCI_IDS_PATHS,
)
from display.modes import (
    custom_display_mode_string as _custom_display_mode_string,
    format_refresh_rate_hz as _format_refresh_rate_hz,
    normalized_custom_display_mode as _normalized_custom_display_mode,
    normalized_refresh_rate_sync_mode as _normalized_refresh_rate_sync_mode,
    refresh_rate_sync_mode_summary as _refresh_rate_sync_mode_summary,
)


def _config_root_candidates() -> List[Path]:
    return [
        Path("~/.config/sunshine").expanduser(),
        Path("~/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine").expanduser(),
    ]


def detect_sunshine_config_root() -> Path:
    for candidate in _config_root_candidates():
        if candidate.exists():
            return candidate
    return _config_root_candidates()[0]


def _resolve_sunshine_config_root(unit_name: str) -> Path:
    if unit_name and unit_name.startswith("app-"):
        flatpak_id = unit_name.removeprefix("app-").removesuffix(".service")
        return Path.home() / ".var" / "app" / flatpak_id / "config" / "sunshine"
    return detect_sunshine_config_root()


def _active_launch_status(state: DisplayState) -> Dict[str, str]:
    portal_active_path = Path(state.paths.portal_active_file)
    if not portal_active_path.exists():
        return {}
    try:
        lines = portal_active_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    payload: Dict[str, str] = {}
    for line in lines:
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = safe_string(key)
        if key:
            payload[key] = value.strip()
    return payload


def _current_headless_mode(state: DisplayState, sunshine_active: bool, sway_active: bool) -> str:
    if not state.enabled or not sunshine_active or not sway_active:
        return ""
    sway_socket = state.sway_socket
    if not sway_socket or not Path(sway_socket).exists():
        return ""
    result = run_command(
        ["swaymsg", "-s", sway_socket, "-t", "get_outputs", "-r"],
        check=False,
    )
    if result.returncode != 0 or not safe_string(result.stdout):
        return ""
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, list):
        return ""
    for output in payload:
        if not isinstance(output, dict) or safe_string(output.get("name")) != "HEADLESS-1":
            continue
        current_mode = output.get("current_mode")
        if not isinstance(current_mode, dict):
            return ""
        width = current_mode.get("width")
        height = current_mode.get("height")
        refresh_hz = _format_refresh_rate_hz(current_mode.get("refresh"))
        if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0 or not refresh_hz:
            return ""
        return f"{width}x{height} @ {refresh_hz} Hz"
    return ""


def _resolve_sunshine_unit(state: DisplayState) -> str:
    """Return the Sunshine unit to operate on.

    Prefers the unit saved in state; falls back to live detection.
    """
    return state.sunshine_unit_name or _svc.sunshine_unit()


def _sunshine_verb(
    state: DisplayState,
    verb_fn: Callable[[str], subprocess.CompletedProcess],
) -> subprocess.CompletedProcess:
    """Run a systemd verb (restart/stop) on the Sunshine unit and print stderr on failure."""
    result = verb_fn(_resolve_sunshine_unit(state))
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if stderr:
            print(stderr)
    return result


def is_enabled() -> bool:
    return load_state().enabled


def dynamic_mangohud_fps_limit_enabled() -> bool:
    return load_state().dynamic_mangohud_fps_limit


def refresh_rate_sync_mode() -> str:
    return load_state().refresh_rate_sync_mode


def custom_display_mode() -> Dict[str, Any]:
    return load_state().custom_display_mode


def set_dynamic_mangohud_fps_limit(enabled: bool) -> Tuple[bool, DisplayState]:
    state = load_state()
    previous = state.dynamic_mangohud_fps_limit
    state.dynamic_mangohud_fps_limit = bool(enabled)
    return previous, refresh_managed_files(state)


def set_refresh_rate_sync_mode(mode: str) -> Tuple[str, DisplayState]:
    state = load_state()
    previous = state.refresh_rate_sync_mode
    state.refresh_rate_sync_mode = _normalized_refresh_rate_sync_mode(mode)
    return previous, refresh_managed_files(state)


def set_custom_display_mode(width: int, height: int, refresh: float) -> Tuple[Dict[str, Any], DisplayState]:
    state = load_state()
    previous = state.custom_display_mode
    state.custom_display_mode = _normalized_custom_display_mode(
        {
            "width": width,
            "height": height,
            "refresh": refresh,
        }
    )
    return previous, refresh_managed_files(state)


def renderer_status_label(state: DisplayState) -> str:
    mode = state.renderer_mode
    if mode == "vulkan":
        return "[VULKAN] HDR capable, may have less GPU support"
    return "[DEFAULT] GLES2 — stable, broad GPU support, no HDR"


def set_renderer_mode(mode: str) -> DisplayState:
    state = load_state()
    if mode not in ("default", "vulkan"):
        mode = "default"
    state.renderer_mode = mode
    return refresh_managed_files(state)


def configure_renderer_mode() -> int:
    state = load_state()
    print("")
    print("Virtual display renderer")
    print("Controls how graphics are drawn on the virtual display.")
    print("GLES2: works on most GPUs, no HDR.")
    print("Vulkan: HDR capable, may not work on all GPUs.")
    current = state.renderer_mode
    print(f"Current: {'[VULKAN] HDR capable, may have less GPU support' if current == 'vulkan' else '[DEFAULT] GLES2 — stable, broad GPU support, no HDR'}")
    print("")
    print("  0. GLES2 — stable, broad GPU support (no HDR)")
    print("  1. Vulkan — HDR pass-through (may have less GPU support)")
    print("")
    choice = get_user_input(
        "Choose renderer mode: ",
        lambda value: value.strip() if value.strip() in {"0", "1"} else (_ for _ in ()).throw(ValueError()),
        "Enter 0 for GLES2 or 1 for Vulkan.",
    )
    if choice == "0":
        set_renderer_mode("default")
        print("Renderer set to GLES2. Stable and broad GPU support, no HDR.")
    else:
        set_renderer_mode("vulkan")
        print("Renderer set to Vulkan. HDR pass-through enabled (may have less GPU support).")

    print("")
    if get_yes_no_input("Restart the virtual display for the change to take effect?", default=True):
        return restart_display()
    return 0


def _parse_systemd_execstart(value: str) -> str:
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


def _current_sunshine_execstart(unit: str) -> str:
    return _parse_systemd_execstart(_svc.show_unit_property(unit, "ExecStart"))


def _fragment_sunshine_execstart(unit: str) -> str:
    fragment_path = Path(safe_string(_svc.show_unit_property(unit, "FragmentPath")))
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
            if not in_service_section or not line.startswith("ExecStart="):
                continue
            execstart = safe_string(line.split("=", 1)[1])
            if execstart:
                return execstart
    except OSError:
        return ""

    return ""


def _remember_sunshine_execstart(state: DisplayState) -> DisplayState:
    target_unit = _resolve_sunshine_unit(state)
    current_execstart = _current_sunshine_execstart(target_unit) or _fragment_sunshine_execstart(target_unit)
    wrapper_path = state.paths.sunshine_wrapper_script

    if current_execstart and wrapper_path and current_execstart != wrapper_path:
        state.sunshine_execstart = current_execstart
        return state

    if state.sunshine_execstart:
        return state

    state.sunshine_execstart = _svc.sunshine_binary() or "sunshine"
    return state

def _write_file(path: Path, content: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _ensure_dependencies() -> List[str]:
    missing = []
    for binary in ["flock", "gdbus", "pactl", "python3", "setfacl", "stdbuf", "sway", "swaybg", "swaymsg", "systemctl"]:
        if shutil.which(binary) is None:
            missing.append(binary)
    if _svc.sunshine_binary() is None and not _current_sunshine_execstart(_svc.sunshine_unit()):
        missing.append("sunshine")
    return missing


def _write_managed_files(state: DisplayState) -> None:
    PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
    BIN_ROOT.mkdir(parents=True, exist_ok=True)
    Path(state.paths.systemd_user_dir).mkdir(parents=True, exist_ok=True)

    for path, content in scripts_render.render_managed_files(state).items():
        _write_file(path, content, executable=path.suffix == ".sh")
    for path, content in audio_policy.managed_files(state).items():
        _write_file(path, content, executable=path.suffix == ".sh")


def refresh_managed_files(state: Optional[DisplayState] = None) -> DisplayState:
    if state is None:
        state = load_state()
    if not state.sunshine_execstart:
        state = _remember_sunshine_execstart(state)
    _write_managed_files(state)
    save_state(state)
    _daemon_reload()
    return state


def _managed_setup_paths(state: DisplayState) -> List[Path]:
    paths = state.paths
    keys = [
        "sway_config",
        "sway_start_script",
        "sunshine_start_script",
        "sunshine_wrapper_script",
        "audio_create_script",
        "audio_cleanup_script",
        "wireplumber_policy_script",
        "wireplumber_policy_conf",
        "launch_app_script",
        "resolve_stream_fps_script",
        "apply_exact_refresh_script",
        "headless_prep_script",
        "set_resolution_script",
        "reset_resolution_script",
        "kwin_input_isolation_script",
        "get_gpu_addr",
    ]
    return [Path(getattr(paths, key)) for key in keys if getattr(paths, key, "")]


def _daemon_reload() -> None:
    _svc.daemon_reload()


def setup_display() -> int:
    missing = _ensure_dependencies()
    if missing:
        print("Missing required commands:", ", ".join(missing))
        return 1

    state = load_state()
    sunshine_was_active = _svc.is_sunshine_service_active()

    # Re-detect and persist the unit name so overrides go to the right directory,
    # even if the user switched Sunshine installations since the last run.
    state.sunshine_unit_name = _svc.sunshine_unit()
    state.paths = build_paths(state.sunshine_unit_name)

    state = _remember_sunshine_execstart(state)
    state = refresh_managed_files(state)
    audio_policy.setup(state)
    save_state(state)

    if not _install_udev_rule(state):
        audio_policy.stop(state)
        state.sunshine_audio_sink = None
        audio_policy.remove(state)
        save_state(state)
        print("Error: unable to install the Sunshine input isolation udev rule.")
        print("Install sudo or pkexec, then rerun the command.")
        return 1

    _daemon_reload()
    state.enabled = True
    save_state(state)
    if sunshine_was_active:
        result = _sunshine_verb(state, _svc.restart_sunshine_unit)
        if result.returncode != 0:
            print("Error: failed to restart Sunshine after installing virtual display files.")
            return 1

    print("Virtual display files installed.")
    return 0


def start_display() -> int:
    state = load_state()
    if not state.enabled:
        print("Virtual display is not set up. Run 'python3 lutristosunshine.py display enable' first.")
        return 1
    state = refresh_managed_files(state)
    audio_policy.start(state)
    save_state(state)
    sunshine_unit = _resolve_sunshine_unit(state)
    result = _svc.start_sunshine_unit(sunshine_unit)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        _svc.stop_sunshine_unit(sunshine_unit)
        audio_policy.stop(state)
        save_state(state)
        if stderr:
            print(stderr)
        print("Error: unable to start the virtual display Sunshine service.")
        return 1
    print("Virtual display started.")
    return 0


def restart_display() -> int:
    state = load_state()
    if not state.enabled:
        print("Virtual display is not set up. Run 'python3 lutristosunshine.py display enable' first.")
        return 1
    result = stop_display()
    if result != 0:
        return result
    return start_display()


def stop_display() -> int:
    state = load_state()
    if not state.enabled:
        print("Virtual display is not set up.")
        return 1
    result = _sunshine_verb(state, _svc.stop_sunshine_unit)
    if result.returncode != 0:
        return 1
    try:
        Path(state.paths.kwin_input_isolation_status_file).unlink()
    except OSError:
        pass
    audio_policy.stop(state)
    save_state(state)
    print("Virtual display stopped.")
    return 0


def display_snapshot() -> Dict[str, Any]:
    state = load_state()
    configured = state.enabled
    custom_mode = state.custom_display_mode
    active_launch_status = _active_launch_status(state)
    sunshine_active = _svc.is_sunshine_service_active() if configured else False
    host_session = _host_session_name()
    input_isolation_mode = _input_isolation_mode()
    wayland_display = ""
    if WAYLAND_DISPLAY_PATH.exists():
        wayland_display = WAYLAND_DISPLAY_PATH.read_text(encoding="utf-8").strip()
    sway_active = bool(
        configured
        and sunshine_active
        and Path(state.sway_socket).exists()
        and WAYLAND_DISPLAY_PATH.exists()
    )
    current_headless_mode = _current_headless_mode(state, sunshine_active, sway_active)
    portal_handoff_active = PORTAL_ACTIVE_PATH.exists()
    wp_policy_state = "installed" if Path(state.paths.wireplumber_policy_conf).exists() else "absent"
    kwin_status = _kwin_input_isolation_status(state) if configured else _empty_kwin_input_isolation_status()
    sunshine_input_devices = _sunshine_virtual_input_devices() if configured else []

    snapshot = {
        "configured": configured,
        "dynamic_mangohud_fps_limit": state.dynamic_mangohud_fps_limit,
        "refresh_rate_sync_mode": state.refresh_rate_sync_mode,
        "custom_display_mode": custom_mode,
        "custom_display_mode_summary": _custom_display_mode_string(custom_mode),
        "profile": state.profile,
        "host_session": host_session,
        "input_isolation_mode": input_isolation_mode,
        # Resolved via state (saved or live-detected) so the snapshot shows
        # the unit the display lifecycle would actually operate on.
        "sunshine_unit": _resolve_sunshine_unit(state),
        "sunshine_install_audit": _svc.sunshine_installation_audit(_resolve_sunshine_unit(state)),
        "sunshine_active": sunshine_active,
        "sway_active": sway_active,
        "wireplumber_policy": wp_policy_state,
        "audio_sink": state.audio_sink,
        "wayland_display": wayland_display,
        "current_headless_mode": current_headless_mode,
        "sway_socket": state.sway_socket,
        "udev_rule_path": state.udev_rule_path,
        "udev_rule_present": Path(state.udev_rule_path).exists(),
        "kwin_isolation_state": kwin_status["state"],
        "kwin_isolation_service": kwin_status["service"],
        "kwin_isolation_error": kwin_status["last_error"],
        "kwin_isolation_seen_device_count": kwin_status["seen_device_count"],
        "kwin_isolation_devices": kwin_status["disabled_devices"],
        "kwin_isolation_failed_devices": kwin_status["failed_devices"],
        "sunshine_input_devices": sunshine_input_devices,
        "sunshine_input_device_count": len(sunshine_input_devices),
        "portal_handoff_active": portal_handoff_active,
        "current_mangohud_config": safe_string(active_launch_status.get("mangohud_config")),
        "last_launch_log_file": state.paths.last_launch_log_file,
        "dependencies_missing": _ensure_dependencies(),
        "gpu_mode": state.gpu_mode,
        "gpu_card_path": state.gpu_card_path,
        "gpu_render_path": state.gpu_render_path,
        "gpu_status_label": gpu_status_label(state),
        "renderer_mode": state.renderer_mode,
        "renderer_status_label": renderer_status_label(state),
        "next_step": "",
    }
    if not configured:
        snapshot["next_step"] = "Run 'python3 lutristosunshine.py display enable' to set up the headless stack."
    elif not sunshine_active:
        snapshot["next_step"] = "Run 'python3 lutristosunshine.py display start' to start the managed stack."
    elif not sway_active:
        snapshot["next_step"] = "Run 'python3 lutristosunshine.py display status' or '... display logs' to inspect why headless Sway is not ready."
    else:
        snapshot["next_step"] = "Virtual display is ready. Use 'status' or 'logs' for follow-up actions."

    if snapshot["dependencies_missing"] or not configured:
        status_summary = "NOT SET UP"
    elif sunshine_active and sway_active:
        status_summary = "READY"
    elif sunshine_active or sway_active:
        status_summary = "PARTIAL"
    else:
        status_summary = "STOPPED"
    snapshot["status_summary"] = status_summary

    mode_summary = _refresh_rate_sync_mode_summary(snapshot["refresh_rate_sync_mode"])
    if snapshot["refresh_rate_sync_mode"] == "custom":
        mode_summary = f"{mode_summary} ({_custom_display_mode_string(custom_mode)})"
    snapshot["display_sync_summary"] = (
        f"{mode_summary}; MangoHud FPS sync "
        f"{'on' if snapshot['dynamic_mangohud_fps_limit'] else 'off'}"
    )
    snapshot["refresh_rate_sync_mode_summary"] = _refresh_rate_sync_mode_summary(
        snapshot["refresh_rate_sync_mode"]
    )

    isolation_level = "success"
    isolation_summary = "rule ready"
    if input_isolation_mode == "kwin-runtime-disable":
        if kwin_status["last_error"]:
            isolation_level = "error"
            isolation_summary = kwin_status["last_error"]
        elif kwin_status["state"] == "inactive":
            isolation_level = "warning"
            isolation_summary = "KWin helper is not running"
        elif kwin_status["state"] == "starting":
            isolation_level = "info"
            isolation_summary = "KWin helper is starting"
        elif kwin_status["disabled_devices"]:
            isolation_summary = (
                f"disabled {len(kwin_status['disabled_devices'])} of "
                f"{len(sunshine_input_devices)} Sunshine device(s) in KWin"
            )
        elif len(sunshine_input_devices) > 0:
            isolation_level = "warning"
            isolation_summary = (
                f"matched {kwin_status['seen_device_count']} of "
                f"{len(sunshine_input_devices)} Sunshine device(s), but disabled 0"
            )
        else:
            isolation_level = "warning"
            isolation_summary = "waiting for Sunshine virtual inputs"
    snapshot["isolation_level"] = isolation_level
    snapshot["isolation_summary"] = isolation_summary
    return snapshot


def display_doctor_report() -> Dict[str, Any]:
    snapshot = display_snapshot()
    checks = []
    missing = snapshot["dependencies_missing"]
    checks.append(
        {
            "label": "Host session",
            "status": "pass" if snapshot["host_session"] != "unknown" else "info",
            "message": (
                f"{snapshot['host_session']} detected; using {snapshot['input_isolation_mode']}."
                if snapshot["host_session"] != "unknown"
                else f"Host session not detected; using {snapshot['input_isolation_mode']}."
            ),
        }
    )
    if missing:
        checks.append(
            {
                "label": "Dependencies",
                "status": "fail",
                "message": f"Missing required commands: {', '.join(missing)}",
            }
        )
    else:
        checks.append(
            {
                "label": "Dependencies",
                "status": "pass",
                "message": "Required commands are available.",
            }
        )

    audit = snapshot["sunshine_install_audit"]
    detected_types = audit.get("detected_types") or []
    managed_type = safe_string(audit.get("managed_type"))
    package_probe_type = safe_string(audit.get("package_probe_type"))
    resolved_type = safe_string(audit.get("resolved_type"))
    if len(detected_types) > 1 and managed_type and managed_type != "unknown" and package_probe_type and package_probe_type != managed_type:
        checks.append(
            {
                "label": "Sunshine installation",
                "status": "warn",
                "message": (
                    f"Multiple Sunshine installs detected ({', '.join(detected_types)}). "
                    f"Virtual display manages {audit['managed_unit']} ({managed_type}) and resolves Sunshine as {resolved_type}; "
                    f"package-only probing would resolve {package_probe_type}."
                ),
            }
        )

    if not snapshot["configured"]:
        checks.append(
            {
                "label": "Setup",
                "status": "fail",
                "message": "Virtual display is not configured.",
            }
        )
    else:
        checks.append(
            {
                "label": "Setup",
                "status": "pass",
                "message": "Managed files are installed.",
            }
        )
        checks.append(
            {
                "label": "Managed udev rule",
                "status": (
                    "pass" if snapshot["udev_rule_present"] else "warn"
                ),
                "message": (
                    "Input permissions rule is present." if snapshot["udev_rule_present"] else "Managed udev rule is missing."
                ),
            }
        )
        if snapshot["input_isolation_mode"] == "kwin-runtime-disable":
            kwin_active_but_not_isolated = (
                snapshot["kwin_isolation_state"] == "active"
                and snapshot["sunshine_input_device_count"] > 0
                and not snapshot["kwin_isolation_devices"]
            )
            checks.append(
                {
                    "label": "KWin isolation",
                    "status": (
                        "warn"
                        if snapshot["kwin_isolation_state"] in {"inactive", "failed"} or kwin_active_but_not_isolated
                        else "pass"
                    ),
                    "message": (
                        snapshot["kwin_isolation_error"]
                        if snapshot["kwin_isolation_state"] == "failed" and snapshot["kwin_isolation_error"]
                        else (
                            "KWin helper is not running."
                            if snapshot["kwin_isolation_state"] == "inactive"
                            else (
                                "KWin helper is starting."
                                if snapshot["kwin_isolation_state"] == "starting"
                                else (
                                    (
                                        f"KWin helper active via {snapshot['kwin_isolation_service'] or 'gdbus'}; "
                                        f"disabled {len(snapshot['kwin_isolation_devices'])} of "
                                        f"{snapshot['sunshine_input_device_count']} Sunshine input device(s)."
                                    )
                                    if snapshot["kwin_isolation_state"] == "active" and snapshot["kwin_isolation_devices"]
                                    else (
                                        (
                                            f"KWin helper active via {snapshot['kwin_isolation_service'] or 'gdbus'}; "
                                            f"matched {snapshot['kwin_isolation_seen_device_count']} of "
                                            f"{snapshot['sunshine_input_device_count']} host Sunshine input device(s), "
                                            "but disabled 0."
                                        )
                                        if kwin_active_but_not_isolated
                                        else "KWin helper is waiting for Sunshine virtual input devices to appear."
                                    )
                                )
                            )
                        )
                    ),
                }
            )
        checks.append(
            {
                "label": "Sunshine service",
                "status": "pass" if snapshot["sunshine_active"] else "warn",
                "message": "Managed Sunshine unit is active." if snapshot["sunshine_active"] else "Managed Sunshine unit is not active.",
            }
        )
        checks.append(
            {
                "label": "Headless display",
                "status": "pass" if snapshot["sway_active"] else "warn",
                "message": (
                    f"Headless Wayland display is ready ({snapshot['wayland_display']})."
                    if snapshot["sway_active"]
                    else "Headless Wayland display is not ready."
                ),
            }
        )

    summary = "healthy"
    if any(item["status"] == "fail" for item in checks):
        summary = "needs_attention"
    elif any(item["status"] == "warn" for item in checks):
        summary = "degraded"

    return {
        "summary": summary,
        "checks": checks,
        "next_step": snapshot["next_step"],
    }


def display_status() -> int:
    snapshot = display_snapshot()
    if not snapshot["configured"]:
        print("Virtual display is not configured.")
        return 1

    print("Virtual display status")
    print(f"Profile: {snapshot['profile']}")
    print(f"Host session: {snapshot['host_session']}")
    isolation_state = snapshot["kwin_isolation_state"] if snapshot["input_isolation_mode"] == "kwin-runtime-disable" else "ready"
    print(f"Input isolation: {snapshot['input_isolation_mode']} ({isolation_state})")
    print(
        "Runtime: "
        f"Sunshine={'active' if snapshot['sunshine_active'] else 'inactive'}, "
        f"Sway={'active' if snapshot['sway_active'] else 'inactive'}, "
        f"WP policy={snapshot['wireplumber_policy']}"
    )
    print(
        "Dynamic MangoHud FPS limit: "
        f"{'enabled' if snapshot['dynamic_mangohud_fps_limit'] else 'disabled'}"
    )
    print(f"MangoHud env value: {snapshot['current_mangohud_config'] or 'not active'}")
    print(f"Refresh rate sync mode: {snapshot['refresh_rate_sync_mode']}")
    if snapshot["refresh_rate_sync_mode"] == "custom":
        print(f"Custom display target: {snapshot['custom_display_mode_summary'] or 'not configured'}")
    print(f"Audio sink: {snapshot['audio_sink']}")
    print(f"Headless display: {snapshot['wayland_display'] or 'not detected'}")
    print(f"Current headless mode: {snapshot['current_headless_mode'] or 'not detected'}")
    if snapshot["input_isolation_mode"] == "kwin-runtime-disable":
        print(f"Host Sunshine inputs: {snapshot['sunshine_input_device_count']}")
        if snapshot["kwin_isolation_error"]:
            print(f"KWin isolation error: {snapshot['kwin_isolation_error']}")
        elif snapshot["kwin_isolation_state"] == "inactive":
            print("KWin-isolated devices: helper not running")
        elif snapshot["kwin_isolation_state"] == "starting":
            print("KWin-isolated devices: helper starting")
        elif snapshot["kwin_isolation_devices"]:
            print(
                "KWin-isolated devices: "
                f"{len(snapshot['kwin_isolation_devices'])} "
                f"(matched {snapshot['kwin_isolation_seen_device_count']})"
            )
        elif snapshot["sunshine_input_device_count"] > 0:
            print(
                "KWin-isolated devices: 0 "
                f"(matched {snapshot['kwin_isolation_seen_device_count']} of {snapshot['sunshine_input_device_count']})"
            )
        else:
            print("KWin-isolated devices: waiting for Sunshine virtual inputs")
        if snapshot["kwin_isolation_failed_devices"]:
            print(f"KWin isolation failures: {len(snapshot['kwin_isolation_failed_devices'])}")
    print(f"Portal handoff: {'active' if snapshot['portal_handoff_active'] else 'idle'}")
    print(f"Logs: {snapshot['last_launch_log_file']}")
    print(f"Virtual display GPU: {snapshot['gpu_status_label']}")
    print(f"Display renderer: {snapshot['renderer_status_label']}")
    print(f"Next step: {snapshot['next_step']}")
    return 0


def display_logs(lines: int = 80) -> int:
    return _svc.fetch_sunshine_journal(lines).returncode


def remove_display() -> int:
    state = load_state()
    if not state.enabled:
        print("Virtual display is not configured.")
        return 1

    stop_result = stop_display()
    if stop_result != 0:
        print("Warning: could not stop the managed Sunshine service; continuing with file cleanup.")
    if not _remove_udev_rule(state):
        print("Warning: failed to remove the managed udev rule.")
    _clean_kde_libinput_config()
    audio_policy.remove(state)

    for path in _managed_setup_paths(state):
        try:
            path.unlink()
        except OSError:
            pass

    _svc.cleanup_managed_overrides(state)

    for path_key in [
        "portal_active_file",
        "portal_lock_file",
        "kwin_input_isolation_status_file",
        "wayland_display_file",
        "audio_module_file",
    ]:
        try:
            path_value = getattr(state.paths, path_key, "")
            if not path_value:
                continue
            Path(path_value).unlink()
        except OSError:
            pass

    try:
        Path(state.paths.state_path).unlink()
    except OSError:
        pass
    try:
        PROFILE_ROOT.rmdir()
    except OSError:
        pass
    try:
        BIN_ROOT.rmdir()
    except OSError:
        pass
    try:
        DISPLAY_ROOT.rmdir()
    except OSError:
        pass
    try:
        LEGACY_DISPLAY_ROOT.rmdir()
    except OSError:
        pass
    _daemon_reload()

    print("Virtual display setup removed.")
    return 0
