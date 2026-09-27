"""Render display/scripts/ template files into concrete managed files.

Templates use @PLACEHOLDER@ markers substituted via str.replace().
Output is byte-identical to the previous f-string rendering that lived in
display.manager; conditional blocks are computed here exactly as before.
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Dict
from dataclasses import asdict

from display.state import DisplayState

from display import audio_policy
from display import portal
from display.constants import (
    FALLBACK_FPS,
    FALLBACK_HEIGHT,
    FALLBACK_WIDTH,
    FLATPAK_FLAG_OPTIONS,
    FLATPAK_PORTAL_ENV_KEYS,
    FLATPAK_PORTAL_RESTORE_GRACE,
    FLATPAK_PORTAL_SPAWN_TIMEOUT,
    FLATPAK_PORTAL_SWITCH_TIMEOUT,
    FLATPAK_PORTAL_UNIT,
    FLATPAK_VALUE_OPTIONS,
    HEADLESS_OUTPUT_NAME,
    PORTAL_BUS_SOCKET_NAME,
    PORTAL_DESKTOP_NAME,
    PORTAL_WLR_BACKEND_NAME,
    SUNSHINE_INPUT_NAME_MARKERS,
    SUNSHINE_INPUT_PRODUCT_ID,
    SUNSHINE_INPUT_VENDOR_ID,
)
from display.modes import (
    format_refresh_rate_hz,
    normalized_custom_display_mode,
    normalized_refresh_rate_sync_mode,
)
from display.sunshine_service import sunshine_unit
from display.utils import safe_string
from sunshine import installation as _installation

_SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"

# DisplayPaths attribute name -> template file name
_TEMPLATE_FILES = {
    "sway_config": "sway_config",
    "sway_start_script": "sway_start.sh",
    "sunshine_start_script": "sunshine_start.sh",
    "sunshine_wrapper_script": "sunshine_wrapper.sh",
    "kwin_input_isolation_script": "kwin_input_isolation.py.template",
    "headless_prep_script": "headless_prep.sh",
    "launch_app_script": "launch_app.sh",
    "resolve_stream_fps_script": "resolve_stream_fps.sh",
    "apply_exact_refresh_script": "apply_exact_refresh.sh",
    "set_resolution_script": "set_resolution.sh",
    "reset_resolution_script": "reset_resolution.sh",
    "sunshine_override": "sunshine_override.conf",
    "portal_bus_script": "portal_bus.sh",
    "portal_start_script": "portal_start.sh",
}


# Portal shell scripts are only rendered (and kept on disk) in portal capture
# mode; display.portal owns the portal config files themselves.
_PORTAL_TEMPLATE_KEYS = (
    "portal_bus_script",
    "portal_start_script",
)


def _conditional_blocks(state: DisplayState) -> Dict[str, str]:
    """Replicate the old conditional block strings from _script_templates."""
    paths = state.paths
    blocks: Dict[str, str] = {
        "mangohud_fps_limit_block": "",
        "mangohud_env_append_block": "",
        "gpu_detection_block": "",
        "gpu_env_vars_block": "",
        "gpu_launch_env_vars_block": "",
        "renderer_env_vars_block": "",
        "portal_setup_block": "",
        "portal_cleanup_block": "",
        "portal_bus_start_block": "",
        "portal_daemon_block": "",
        "portal_runtime_block": "",
    }
    if portal.portal_capture_enabled(state):
        blocks.update(_portal_blocks(state))
    if state.dynamic_mangohud_fps_limit:
        refresh_mode = normalized_refresh_rate_sync_mode(
            state.refresh_rate_sync_mode
        )
        blocks["mangohud_fps_limit_block"] = f"""
    mangohud_config_value=""
    local resolved_stream_fps
    resolved_stream_fps="$("{paths.resolve_stream_fps_script}" "{refresh_mode}" fallback)"
    if [ -n "$resolved_stream_fps" ]; then
        mangohud_config_value="read_cfg,fps_limit=$resolved_stream_fps"
    fi
""".rstrip()
        blocks["mangohud_env_append_block"] = """
    if [ -n "$mangohud_config_value" ]; then
        if is_flatpak_command "$command_to_run"; then
            command_to_run="$(python3 - "$command_to_run" "$mangohud_config_value" <<'PY'
import shlex, sys
tokens = shlex.split(sys.argv[1])
env_val = sys.argv[2]
idx = 0
if len(tokens) >= 2 and tokens[:2] == ["flatpak-spawn", "--host"]:
    idx = 2
tokens.insert(idx + 2, f"--env=MANGOHUD_CONFIG={env_val}")
print(shlex.join(tokens))
PY
)"
        else
            launch_command+=("MANGOHUD_CONFIG=$mangohud_config_value")
        fi
    fi
""".rstrip()
    gpu_card_path = safe_string(state.gpu_card_path)
    gpu_render_path = safe_string(state.gpu_render_path)
    if state.gpu_mode == "manual" and gpu_card_path:
        blocks["gpu_detection_block"] = f"""
wlr_drm_devices_value=""
wlr_render_drm_device_value=""
if [[ -e "{gpu_card_path}" ]] && [[ -e "{gpu_render_path}" ]]; then
    wlr_drm_devices_value="{gpu_card_path}"
    wlr_render_drm_device_value="{gpu_render_path}"
else
    echo "lts-gpu: saved GPU paths not found, skipping WLR GPU vars" >&2
fi
"""
        blocks["gpu_env_vars_block"] = """
    if [[ -n $wlr_drm_devices_value ]]; then
        sway_cmd+=(
            "WLR_DRM_DEVICES=$wlr_drm_devices_value"
            "WLR_RENDER_DRM_DEVICE=$wlr_render_drm_device_value"
        )
    fi
"""
        blocks["gpu_launch_env_vars_block"] = """
    if [[ -n $wlr_drm_devices_value ]]; then
        launch_command+=(
            "WLR_DRM_DEVICES=$wlr_drm_devices_value"
            "WLR_RENDER_DRM_DEVICE=$wlr_render_drm_device_value"
        )
    fi
"""
    if state.renderer_mode == "vulkan":
        blocks["renderer_env_vars_block"] = """
    sway_cmd+=(
        "WLR_RENDERER=vulkan"
    )
"""
    return blocks


def _portal_blocks(state: DisplayState) -> Dict[str, str]:
    """Conditional shell blocks for the private portal stack.

    The wrapper owns the bus and the portal daemons so they start before
    Sunshine (Sunshine probes the capture backend at startup and would fail
    with ``capture = portal`` against a portal that is not up yet).
    """
    paths = state.paths
    return {
        "portal_setup_block": f"""
portal_bus_script="{paths.portal_bus_script}"
portal_start_script="{paths.portal_start_script}"
portal_bus_address_file="{paths.portal_bus_address_file}"
portal_bus_pid_file="{paths.portal_bus_pid_file}"
portal_ready_file="{paths.portal_ready_file}"
portal_runtime_dir="{paths.portal_runtime_dir}"

stop_portal_bus() {{
    if [ -s "$portal_bus_pid_file" ]; then
        kill "$(cat "$portal_bus_pid_file")" >/dev/null 2>&1 || true
        rm -f "$portal_bus_pid_file"
    fi
    rm -f "$portal_bus_address_file" "$portal_ready_file"
    rm -rf "$portal_runtime_dir"
}}
""".rstrip(),
        "portal_cleanup_block": """    stop_child "$portal_pid"
    stop_portal_bus""",
        "portal_bus_start_block": '"$portal_bus_script"',
        "portal_daemon_block": """setsid "$portal_start_script" &
portal_pid=$!

for _ in $(seq 1 150); do
    if [ -s "$portal_ready_file" ]; then
        break
    fi
    if ! kill -0 "$portal_pid" 2>/dev/null; then
        break
    fi
    sleep 0.1
done

if [ ! -s "$portal_ready_file" ]; then
    echo "Virtual display portal stack did not become ready." >&2
    exit 1
fi
""".rstrip(),
        "portal_runtime_block": f"""portal_address_file="{paths.portal_bus_address_file}"
portal_runtime_dir="{paths.portal_runtime_dir}"
if [ -s "$portal_address_file" ]; then
    portal_bus_address="$(cat "$portal_address_file")"
    portal_bus_socket="${{portal_bus_address#unix:path=}}"
    if [ ! -S "$portal_bus_socket" ]; then
        echo "Virtual display portal bus socket is not ready." >&2
        exit 1
    fi
    # Sunshine carries file capabilities, so the kernel marks it AT_SECURE and
    # GLib ignores DBUS_SESSION_BUS_ADDRESS: it only accepts the session bus it
    # finds at $XDG_RUNTIME_DIR/bus.  Hand it a private runtime dir whose `bus`
    # points at the portal bus, with every other entry linked from the real
    # runtime dir so Wayland, PipeWire and PulseAudio keep working.
    #
    # Directories are mirrored one level deep (real directory, linked entries):
    # libpulse refuses a runtime dir that is a symlink (pa_make_secure_dir()
    # fails with "Too many levels of symbolic links"), which would break both
    # Sunshine's own PulseAudio capture and the managed audio-sink scripts.
    # Never walk deeper than that level: $XDG_RUNTIME_DIR/doc is a FUSE mount
    # whose tree can be huge, and crawling it stalls Sunshine's start.
    # Mount points are linked, never inspected: stat()ing into a FUSE mount whose
    # daemon is gone (the document portal's $XDG_RUNTIME_DIR/doc) blocks forever.
    mounted_paths=" "
    while read -r _ _ _ _ mount_point _; do
        mounted_paths="$mounted_paths$mount_point "
    done < /proc/self/mountinfo
    rm -rf "$portal_runtime_dir"
    mkdir -m 700 "$portal_runtime_dir"
    for entry in "$runtime_dir"/* "$runtime_dir"/.[!.]*; do
        entry_name="$(basename "$entry")"
        case "$entry_name" in
            ".[!.]*") continue ;;
        esac
        if [ "$entry_name" = "bus" ]; then
            continue
        fi
        case "$mounted_paths" in
            *" $entry "*) ln -sf "$entry" "$portal_runtime_dir/$entry_name"; continue ;;
        esac
        if [ -d "$entry" ] && [ ! -L "$entry" ]; then
            mkdir -m 700 "$portal_runtime_dir/$entry_name" 2>/dev/null || true
            for nested in "$entry"/* "$entry"/.[!.]*; do
                [ -e "$nested" ] || continue
                ln -sf "$nested" "$portal_runtime_dir/$entry_name/$(basename "$nested")"
            done
        else
            ln -sf "$entry" "$portal_runtime_dir/$entry_name"
        fi
    done
    ln -s "$portal_bus_socket" "$portal_runtime_dir/bus"
    runtime_export_value="$portal_runtime_dir"
    dbus_value="$portal_bus_address"
fi""",
    }


def _sunshine_log_file(state: DisplayState) -> str:
    """Sunshine's own log file, which sits next to sunshine.conf.

    The systemd journal is not a reliable source: Flatpak runs Sunshine
    inside a transient scope, so the log lines are not attributed to the
    managed unit.
    """
    sunshine_conf = safe_string(state.paths.sunshine_conf)
    if sunshine_conf:
        return str(Path(sunshine_conf).parent / "sunshine.log")
    return str(_installation.detect_sunshine_config_root() / "sunshine.log")


def render_managed_files(state: DisplayState) -> Dict[Path, str]:
    """Render every managed script/systemd file for the given state."""
    paths = state.paths
    blocks = _conditional_blocks(state)

    custom_mode = normalized_custom_display_mode(state.custom_display_mode)
    refresh_mode = normalized_refresh_rate_sync_mode(
        state.refresh_rate_sync_mode
    )
    custom_refresh = (
        format_refresh_rate_hz(custom_mode["refresh"]) or str(FALLBACK_FPS)
    )
    sunshine_command = (
        safe_string(state.sunshine_execstart)
        or _installation.sunshine_binary()
        or "sunshine"
    )

    values = {
        "@SWAY_SOCKET@": state.sway_socket,
        "@HEADLESS_OUTPUT@": HEADLESS_OUTPUT_NAME,
        "@HOST_SESSION_BUS_ADDRESS@": portal.host_bus_address(),
        "@HOST_RUNTIME_DIR@": portal.host_runtime_dir(),
        "@PORTAL_BUS_SOCKET_NAME@": PORTAL_BUS_SOCKET_NAME,
        "@PORTAL_PORTALS_DIR@": paths.portal_portals_dir,
        "@PORTAL_DESKTOP_NAME@": PORTAL_DESKTOP_NAME,
        "@PORTAL_WLR_BACKEND_NAME@": PORTAL_WLR_BACKEND_NAME,
        "@PROFILE_ROOT@": paths.profile_root,
        "@AUDIO_INJECT_ENV@": audio_policy.render_flatpak_audio_env(
            state.audio_sink, FLATPAK_FLAG_OPTIONS, FLATPAK_VALUE_OPTIONS
        ),
        "@CLEAR_ACTIVATION_ENV_SCRIPT@": audio_policy.clear_activation_env_script(),
        "@AUDIO_SINK@": state.audio_sink,
        "@AUDIO_STREAM_PULSE_PROP@": audio_policy.AUDIO_STREAM_PULSE_PROP,
        "@AUDIO_STREAM_PIPEWIRE_PROPS@": audio_policy.AUDIO_STREAM_PIPEWIRE_PROPS,
        "@PORTAL_ENV_KEYS@": " ".join(FLATPAK_PORTAL_ENV_KEYS),
        "@PORTAL_ENV_KEYS_BLOCK@": "\n".join(FLATPAK_PORTAL_ENV_KEYS),
        "@FLATPAK_PORTAL_SWITCH_TIMEOUT@": str(FLATPAK_PORTAL_SWITCH_TIMEOUT),
        "@FLATPAK_PORTAL_SPAWN_TIMEOUT@": str(FLATPAK_PORTAL_SPAWN_TIMEOUT),
        "@FLATPAK_PORTAL_RESTORE_GRACE@": str(FLATPAK_PORTAL_RESTORE_GRACE),
        "@FLATPAK_PORTAL_UNIT@": FLATPAK_PORTAL_UNIT,
        "@FALLBACK_MODE@": f"{FALLBACK_WIDTH}x{FALLBACK_HEIGHT}@{FALLBACK_FPS}Hz",
        "@CUSTOM_WIDTH@": str(custom_mode["width"]),
        "@CUSTOM_HEIGHT@": str(custom_mode["height"]),
        "@CUSTOM_REFRESH@": custom_refresh,
        "@REFRESH_RATE_SYNC_MODE@": refresh_mode,
        "@SUNSHINE_LOG_FILE@": _sunshine_log_file(state),
        "@SUNSHINE_UNIT@": safe_string(state.sunshine_unit_name) or sunshine_unit(),
        "@SUNSHINE_COMMAND@": shlex.quote(sunshine_command),
        "@PYTHON_EXECUTABLE@": sys.executable or "/usr/bin/env python3",
        "@KWIN_INPUT_ISOLATION_STATUS_FILE@": paths.kwin_input_isolation_status_file,
        "@SUNSHINE_VENDOR_ID@": str(SUNSHINE_INPUT_VENDOR_ID),
        "@SUNSHINE_PRODUCT_ID@": str(SUNSHINE_INPUT_PRODUCT_ID),
        "@NAME_MARKERS@": repr(SUNSHINE_INPUT_NAME_MARKERS),
        "@MANGOHUD_FPS_LIMIT_BLOCK@": blocks["mangohud_fps_limit_block"],
        "@MANGOHUD_ENV_APPEND_BLOCK@": blocks["mangohud_env_append_block"],
        "@GPU_DETECTION_BLOCK@": blocks["gpu_detection_block"],
        "@GPU_ENV_VARS_BLOCK@": blocks["gpu_env_vars_block"],
        "@GPU_LAUNCH_ENV_VARS_BLOCK@": blocks["gpu_launch_env_vars_block"],
        "@RENDERER_BLOCK@": blocks["renderer_env_vars_block"],
        "@PORTAL_SETUP_BLOCK@": blocks["portal_setup_block"],
        "@PORTAL_CLEANUP_BLOCK@": blocks["portal_cleanup_block"],
        "@PORTAL_BUS_START_BLOCK@": blocks["portal_bus_start_block"],
        "@PORTAL_DAEMON_BLOCK@": blocks["portal_daemon_block"],
        "@PORTAL_RUNTIME_BLOCK@": blocks["portal_runtime_block"],
    }
    # Path placeholders: @<PATHS_KEY_UPPER>@ -> concrete path.
    values.update({f"@{key.upper()}@": value for key, value in asdict(paths).items()})

    rendered: Dict[Path, str] = {}
    portal_enabled = portal.portal_capture_enabled(state)
    for path_key, template_name in _TEMPLATE_FILES.items():
        # Portal files are cleared by display.portal.remove_files() whenever
        # portal capture is off, so never re-create them in wlr mode.
        if path_key in _PORTAL_TEMPLATE_KEYS and not portal_enabled:
            continue
        content = (_SCRIPTS_DIR / template_name).read_text(encoding="utf-8")
        for placeholder, value in values.items():
            content = content.replace(placeholder, value)
        rendered[Path(getattr(paths, path_key))] = content
    return rendered
