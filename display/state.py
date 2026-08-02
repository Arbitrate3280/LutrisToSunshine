"""Typed display state: DisplayState dataclass replaces the Dict[str, Any] state."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from display.constants import (
    AUDIO_MODULE_PATH,
    BIN_ROOT,
    DISPLAY_ROOT,
    DISPLAY_SOCKET_PATH,
    DISPLAY_STATE_PATH,
    LAST_LAUNCH_LOG_PATH,
    LEGACY_STATE_PATH,
    PORTAL_ACTIVE_PATH,
    PORTAL_LOCK_PATH,
    PROFILE_NAME,
    PROFILE_ROOT,
    UDEV_RULE_PATH,
    WAYLAND_DISPLAY_PATH,
)
from display.modes import normalized_custom_display_mode, normalized_refresh_rate_sync_mode
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
    get_gpu_addr: str = ""
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


@dataclass
class DisplayState:
    enabled: bool = False
    dynamic_mangohud_fps_limit: bool = False
    refresh_rate_sync_mode: str = "client"
    custom_display_mode: dict = field(default_factory=lambda: normalized_custom_display_mode(None))
    sunshine_execstart: str = ""
    sunshine_unit_name: str = ""
    profile: str = PROFILE_NAME
    audio_sink: str = "lts-sunshine-stereo"
    sway_socket: str = DISPLAY_SOCKET_PATH
    udev_rule_path: str = UDEV_RULE_PATH
    sunshine_audio_sink: Optional[dict] = None
    gpu_mode: str = "auto"
    gpu_card_path: str = ""
    gpu_render_path: str = ""
    renderer_mode: str = "default"
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


def build_paths(unit_name: str) -> DisplayPaths:
    from display import audio_policy
    from display.manager import _resolve_sunshine_config_root

    systemd_user_dir = Path("~/.config/systemd/user").expanduser()
    override_dir = systemd_user_dir / f"{unit_name}.d"
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
        wireplumber_policy_script=str(audio_policy.WIREPLUMBER_SCRIPTS_DIR / audio_policy.WIREPLUMBER_POLICY_SCRIPT_NAME),
        wireplumber_policy_conf=str(audio_policy.WIREPLUMBER_CONF_DIR / audio_policy.WIREPLUMBER_POLICY_CONF_NAME),
        launch_app_script=str(BIN_ROOT / "lutristosunshine-launch-app.sh"),
        resolve_stream_fps_script=str(BIN_ROOT / "lutristosunshine-resolve-stream-fps.sh"),
        apply_exact_refresh_script=str(BIN_ROOT / "lutristosunshine-apply-exact-refresh.sh"),
        headless_prep_script=str(BIN_ROOT / "lutristosunshine-run-headless-prep.sh"),
        set_resolution_script=str(BIN_ROOT / "lutristosunshine-set-resolution.sh"),
        reset_resolution_script=str(BIN_ROOT / "lutristosunshine-reset-resolution.sh"),
        get_gpu_addr=str(BIN_ROOT / "lutristosunshine-get-gpu-addr.sh"),
        portal_lock_file=str(PORTAL_LOCK_PATH),
        portal_active_file=str(PORTAL_ACTIVE_PATH),
        last_launch_log_file=str(LAST_LAUNCH_LOG_PATH),
        wayland_display_file=str(WAYLAND_DISPLAY_PATH),
        audio_module_file=str(AUDIO_MODULE_PATH),
        systemd_user_dir=str(systemd_user_dir),
        sunshine_override_dir=str(override_dir),
        sunshine_override=str(override_dir / "override.conf"),
        kwin_input_isolation_script=str(BIN_ROOT / "lutristosunshine-kwin-input-isolation.py"),
        sunshine_conf=str(_resolve_sunshine_config_root(unit_name) / "sunshine.conf"),
        kwin_input_isolation_status_file=str(PROFILE_ROOT / "kwin-input-isolation-status.json"),
    )


def default_state() -> DisplayState:
    from display.sunshine_service import sunshine_unit

    state = DisplayState()
    state.paths = build_paths(sunshine_unit())
    return state


def load_state() -> DisplayState:
    from display.sunshine_service import sunshine_unit

    state_path = DISPLAY_STATE_PATH if DISPLAY_STATE_PATH.exists() else LEGACY_STATE_PATH
    if not state_path.exists():
        return default_state()
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_state()
    state = DisplayState.from_dict(data)
    if not safe_string(state.sunshine_unit_name):
        state.sunshine_unit_name = sunshine_unit()
    state.refresh_rate_sync_mode = normalized_refresh_rate_sync_mode(state.refresh_rate_sync_mode)
    state.custom_display_mode = normalized_custom_display_mode(state.custom_display_mode)
    state.gpu_mode = safe_string(state.gpu_mode).lower() if safe_string(state.gpu_mode).lower() in {"auto", "manual"} else "auto"
    state.gpu_card_path = safe_string(state.gpu_card_path)
    state.gpu_render_path = safe_string(state.gpu_render_path)
    state.renderer_mode = safe_string(state.renderer_mode).lower() if safe_string(state.renderer_mode).lower() in {"default", "vulkan"} else "default"
    state.paths = build_paths(state.sunshine_unit_name)
    return state


def save_state(state: DisplayState) -> None:
    DISPLAY_ROOT.mkdir(parents=True, exist_ok=True)
    state.custom_display_mode = normalized_custom_display_mode(state.custom_display_mode)
    DISPLAY_STATE_PATH.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    if LEGACY_STATE_PATH.exists():
        try:
            LEGACY_STATE_PATH.unlink()
        except OSError:
            pass
