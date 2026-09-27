"""Typed display state: DisplayState dataclass replaces the Dict[str, Any] state."""
from __future__ import annotations

import json
from typing import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from display.constants import (
    AUDIO_MODULE_PATH,
    BIN_ROOT,
    CAPTURE_METHODS,
    DEFAULT_CAPTURE_METHOD,
    DEFAULT_SUNSHINE_UNIT,
    DISPLAY_ROOT,
    DISPLAY_SOCKET_PATH,
    DISPLAY_STATE_PATH,
    LAST_LAUNCH_LOG_PATH,
    LEGACY_STATE_PATH,
    PORTAL_ACTIVE_PATH,
    PORTAL_BUS_ADDRESS_PATH,
    PORTAL_BUS_PID_PATH,
    PORTAL_CONFIG_HOME,
    PORTAL_LOCK_PATH,
    PORTAL_LOG_PATH,
    PORTAL_PORTALS_DIRNAME,
    PORTAL_READY_PATH,
    PORTAL_ROUTING_CONF_NAME,
    PORTAL_RUNTIME_DIR,
    PORTAL_WLR_CONFIG_NAME,
    PORTAL_WLR_PORTAL_NAME,
    PROFILE_NAME,
    PROFILE_ROOT,
    UDEV_RULE_PATH,
    WAYLAND_DISPLAY_PATH,
    WIREPLUMBER_CONF_DIR,
    WIREPLUMBER_POLICY_CONF_NAME,
    WIREPLUMBER_POLICY_SCRIPT_NAME,
    WIREPLUMBER_SCRIPTS_DIR,
)
from display.modes import DisplayMode, normalized_custom_display_mode, normalized_refresh_rate_sync_mode
from display.utils import safe_string


@dataclass
class DisplayPaths:
    profile_root: str = ""
    bin_root: str = ""
    state_path: str = ""
    sway_config: str = ""
    sway_start_script: str = ""
    sunshine_start_script: str = ""
    sunshine_wrapper_script: str = ""
    audio_create_script: str = ""
    audio_cleanup_script: str = ""
    wireplumber_policy_script: str = ""
    wireplumber_policy_conf: str = ""
    launch_app_script: str = ""
    resolve_stream_fps_script: str = ""
    apply_exact_refresh_script: str = ""
    headless_prep_script: str = ""
    set_resolution_script: str = ""
    reset_resolution_script: str = ""
    portal_lock_file: str = ""
    portal_active_file: str = ""
    last_launch_log_file: str = ""
    wayland_display_file: str = ""
    audio_module_file: str = ""
    systemd_user_dir: str = ""
    sunshine_override_dir: str = ""
    sunshine_override: str = ""
    kwin_input_isolation_script: str = ""
    sunshine_conf: str = ""
    kwin_input_isolation_status_file: str = ""
    portal_bus_script: str = ""
    portal_start_script: str = ""
    portal_bus_address_file: str = ""
    portal_bus_pid_file: str = ""
    portal_ready_file: str = ""
    portal_log_file: str = ""
    portal_config_home: str = ""
    portal_routing_conf: str = ""
    portal_wlr_config: str = ""
    portal_portals_dir: str = ""
    portal_wlr_portal: str = ""
    portal_runtime_dir: str = ""


@dataclass
class DisplayState:
    enabled: bool = False
    dynamic_mangohud_fps_limit: bool = False
    refresh_rate_sync_mode: str = "client"
    custom_display_mode: DisplayMode = field(default_factory=lambda: normalized_custom_display_mode(None))
    sunshine_execstart: str = ""
    sunshine_unit_name: str = ""
    profile: str = PROFILE_NAME
    audio_sink: str = "lts-sunshine-stereo"
    sway_socket: str = DISPLAY_SOCKET_PATH
    udev_rule_path: str = UDEV_RULE_PATH
    # Legacy field retained so older generated cleanup scripts can still
    # preserve the managed Sunshine setting during an upgrade.
    sunshine_audio_sink: Optional[dict] = None
    gpu_mode: str = "auto"
    gpu_card_path: str = ""
    gpu_render_path: str = ""
    renderer_mode: str = "default"
    capture_method: str = DEFAULT_CAPTURE_METHOD
    # Sunshine ``capture``/``output_name`` values replaced by portal mode, kept
    # so switching back (or resetting) restores what the user had.
    sunshine_capture_override: Optional[dict] = None
    paths: DisplayPaths = field(default_factory=DisplayPaths)

    def to_dict(self) -> dict:
        """Serialize to the JSON-compatible dict format."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> DisplayState:
        """Build from a JSON-loaded dict, tolerating missing/extra keys."""
        data = dict(data)
        paths_data = data.pop("paths", {})
        paths = DisplayPaths(**{k: v for k, v in paths_data.items() if k in DisplayPaths.__dataclass_fields__})
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "paths"}
        return cls(paths=paths, **known)


def build_paths(
    unit_name: str,
    *,
    config_root_fn: Optional[Callable[[str], Path]] = None,
) -> DisplayPaths:
    systemd_user_dir = Path("~/.config/systemd/user").expanduser()
    effective_unit = unit_name or DEFAULT_SUNSHINE_UNIT
    override_dir = systemd_user_dir / f"{effective_unit}.d"
    return DisplayPaths(
        profile_root=str(PROFILE_ROOT),
        bin_root=str(BIN_ROOT),
        state_path=str(DISPLAY_STATE_PATH),
        sway_config=str(PROFILE_ROOT / "sway.conf"),
        sway_start_script=str(BIN_ROOT / "lutristosunshine-start-headless-sway.sh"),
        sunshine_start_script=str(BIN_ROOT / "lutristosunshine-start-display-sunshine.sh"),
        sunshine_wrapper_script=str(BIN_ROOT / "lutristosunshine-run-display-service.sh"),
        audio_create_script=str(BIN_ROOT / "lutristosunshine-create-audio-sink.sh"),
        audio_cleanup_script=str(BIN_ROOT / "lutristosunshine-cleanup-audio-sink.sh"),
        wireplumber_policy_script=str(WIREPLUMBER_SCRIPTS_DIR / WIREPLUMBER_POLICY_SCRIPT_NAME),
        wireplumber_policy_conf=str(WIREPLUMBER_CONF_DIR / WIREPLUMBER_POLICY_CONF_NAME),
        launch_app_script=str(BIN_ROOT / "lutristosunshine-launch-app.sh"),
        resolve_stream_fps_script=str(BIN_ROOT / "lutristosunshine-resolve-stream-fps.sh"),
        apply_exact_refresh_script=str(BIN_ROOT / "lutristosunshine-apply-exact-refresh.sh"),
        headless_prep_script=str(BIN_ROOT / "lutristosunshine-run-headless-prep.sh"),
        set_resolution_script=str(BIN_ROOT / "lutristosunshine-set-resolution.sh"),
        reset_resolution_script=str(BIN_ROOT / "lutristosunshine-reset-resolution.sh"),
        portal_lock_file=str(PORTAL_LOCK_PATH),
        portal_active_file=str(PORTAL_ACTIVE_PATH),
        last_launch_log_file=str(LAST_LAUNCH_LOG_PATH),
        wayland_display_file=str(WAYLAND_DISPLAY_PATH),
        audio_module_file=str(AUDIO_MODULE_PATH),
        systemd_user_dir=str(systemd_user_dir),
        sunshine_override_dir=str(override_dir),
        sunshine_override=str(override_dir / "override.conf"),
        kwin_input_isolation_script=str(BIN_ROOT / "lutristosunshine-kwin-input-isolation.py"),
        portal_bus_script=str(BIN_ROOT / "lutristosunshine-portal-bus.sh"),
        portal_start_script=str(BIN_ROOT / "lutristosunshine-start-portal.sh"),
        portal_bus_address_file=str(PORTAL_BUS_ADDRESS_PATH),
        portal_bus_pid_file=str(PORTAL_BUS_PID_PATH),
        portal_ready_file=str(PORTAL_READY_PATH),
        portal_log_file=str(PORTAL_LOG_PATH),
        portal_config_home=str(PORTAL_CONFIG_HOME),
        portal_routing_conf=str(PORTAL_CONFIG_HOME / "xdg-desktop-portal" / PORTAL_ROUTING_CONF_NAME),
        portal_wlr_config=str(PORTAL_CONFIG_HOME / "xdg-desktop-portal-wlr" / PORTAL_WLR_CONFIG_NAME),
        portal_portals_dir=str(PORTAL_CONFIG_HOME / PORTAL_PORTALS_DIRNAME),
        portal_wlr_portal=str(PORTAL_CONFIG_HOME / PORTAL_PORTALS_DIRNAME / PORTAL_WLR_PORTAL_NAME),
        portal_runtime_dir=str(PORTAL_RUNTIME_DIR),
        sunshine_conf=str((config_root_fn(effective_unit) if config_root_fn else Path("~/.config/sunshine").expanduser()) / "sunshine.conf"),
        kwin_input_isolation_status_file=str(PROFILE_ROOT / "kwin-input-isolation-status.json"),
    )


def with_sunshine_unit(paths: DisplayPaths, unit_name: str) -> DisplayPaths:
    """Point the unit-derived paths at ``unit_name``, keeping everything else.

    Only the service-override entries depend on the unit name.  Rebuilding every
    path here would silently escape a redirected layout (tests, temporary
    installs) back to the real ``~/.config/lutristosunshine`` install.
    """
    effective_unit = unit_name or DEFAULT_SUNSHINE_UNIT
    systemd_user_dir = (
        Path(paths.systemd_user_dir)
        if safe_string(paths.systemd_user_dir)
        else Path("~/.config/systemd/user").expanduser()
    )
    override_dir = systemd_user_dir / f"{effective_unit}.d"
    paths.sunshine_override_dir = str(override_dir)
    paths.sunshine_override = str(override_dir / "override.conf")
    return paths


def default_state() -> DisplayState:
    state = DisplayState()
    state.paths = build_paths(DEFAULT_SUNSHINE_UNIT)
    return state


def load_state() -> DisplayState:
    state_path = DISPLAY_STATE_PATH if DISPLAY_STATE_PATH.exists() else LEGACY_STATE_PATH
    if not state_path.exists():
        return default_state()
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_state()
    state = DisplayState.from_dict(data)
    if not safe_string(state.sunshine_unit_name):
        state.sunshine_unit_name = DEFAULT_SUNSHINE_UNIT
    state.refresh_rate_sync_mode = normalized_refresh_rate_sync_mode(state.refresh_rate_sync_mode)
    state.custom_display_mode = normalized_custom_display_mode(state.custom_display_mode)
    state.gpu_mode = safe_string(state.gpu_mode).lower() if safe_string(state.gpu_mode).lower() in {"auto", "manual"} else "auto"
    state.gpu_card_path = safe_string(state.gpu_card_path)
    state.gpu_render_path = safe_string(state.gpu_render_path)
    state.renderer_mode = safe_string(state.renderer_mode).lower() if safe_string(state.renderer_mode).lower() in {"default", "vulkan"} else "default"
    state.capture_method = normalized_capture_method(state.capture_method)
    persisted_sunshine_conf = state.paths.sunshine_conf
    state.paths = build_paths(state.sunshine_unit_name)
    if safe_string(persisted_sunshine_conf):
        state.paths.sunshine_conf = persisted_sunshine_conf
    return state


def is_enabled() -> bool:
    return load_state().enabled


def normalized_capture_method(value: object) -> str:
    """Return a supported Sunshine capture method, falling back to wlr."""
    candidate = safe_string(value).lower()
    return candidate if candidate in CAPTURE_METHODS else DEFAULT_CAPTURE_METHOD


def save_state(state: DisplayState) -> None:
    DISPLAY_ROOT.mkdir(parents=True, exist_ok=True)
    state.custom_display_mode = normalized_custom_display_mode(state.custom_display_mode)
    DISPLAY_STATE_PATH.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    if LEGACY_STATE_PATH.exists():
        try:
            LEGACY_STATE_PATH.unlink()
        except OSError:
            pass
