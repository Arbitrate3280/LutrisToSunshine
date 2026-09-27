import shutil
import stat
import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from display import audio_policy
from display import input_isolation as _input_isolation
from display import portal
from display import state as _state
from display import sunshine_service as _svc
from display import scripts_render
from display.diagnostics import DisplaySnapshot, DoctorReport
from display.modes import DisplayMode
from display.state import DisplayState, default_state
from sunshine import installation
_default_state = default_state
from display.input_isolation import (
    clean_kde_libinput_config as _clean_kde_libinput_config,
    install_udev_rule as _install_udev_rule,
    remove_udev_rule as _remove_udev_rule,
)


from display.constants import (
    BIN_ROOT,
    LEGACY_DISPLAY_ROOT,
    PROFILE_ROOT,
)
from display.modes import (
    normalized_custom_display_mode as _normalized_custom_display_mode,
    normalized_refresh_rate_sync_mode as _normalized_refresh_rate_sync_mode,
)


def _sunshine_verb(
    state: DisplayState,
    verb_fn: Callable[[str], subprocess.CompletedProcess],
) -> subprocess.CompletedProcess:
    """Run a systemd verb (restart/stop) on the Sunshine unit and print stderr on failure."""
    result = verb_fn(_svc.resolve_sunshine_unit(state))
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if stderr:
            print(stderr)
    return result


def set_dynamic_mangohud_fps_limit(enabled: bool) -> Tuple[bool, DisplayState]:
    state = _state.load_state()
    previous = state.dynamic_mangohud_fps_limit
    state.dynamic_mangohud_fps_limit = bool(enabled)
    return previous, refresh_managed_files(state)


def set_refresh_rate_sync_mode(mode: str) -> Tuple[str, DisplayState]:
    state = _state.load_state()
    previous = state.refresh_rate_sync_mode
    state.refresh_rate_sync_mode = _normalized_refresh_rate_sync_mode(mode)
    return previous, refresh_managed_files(state)


def set_custom_display_mode(width: int, height: int, refresh: float) -> Tuple[DisplayMode, DisplayState]:
    state = _state.load_state()
    previous = state.custom_display_mode
    state.custom_display_mode = _normalized_custom_display_mode(
        {
            "width": width,
            "height": height,
            "refresh": refresh,
        }
    )
    return previous, refresh_managed_files(state)


def set_renderer_mode(mode: str) -> DisplayState:
    state = _state.load_state()
    if mode not in ("default", "vulkan"):
        mode = "default"
    state.renderer_mode = mode
    return refresh_managed_files(state)


def set_capture_method(
    method: str,
    *,
    load_state_fn: Optional[Callable[[], DisplayState]] = None,
    refresh_managed_files_fn: Optional[Callable[[DisplayState], DisplayState]] = None,
) -> Tuple[str, DisplayState]:
    """Switch Sunshine's capture backend and reconcile the managed files."""
    load_state_fn = load_state_fn or _state.load_state
    refresh_managed_files_fn = refresh_managed_files_fn or refresh_managed_files
    state = load_state_fn()
    previous = state.capture_method
    state.capture_method = _state.normalized_capture_method(method)
    return previous, refresh_managed_files_fn(state)


def _write_file(path: Path, content: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_managed_files(state: DisplayState) -> None:
    PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
    BIN_ROOT.mkdir(parents=True, exist_ok=True)
    Path(state.paths.systemd_user_dir).mkdir(parents=True, exist_ok=True)

    for path, content in scripts_render.render_managed_files(state).items():
        _write_file(path, content, executable=path.suffix == ".sh")
    for path, content in audio_policy.managed_files(state).items():
        _write_file(path, content, executable=path.suffix == ".sh")
    for path, content in portal.managed_files(state).items():
        _write_file(path, content)
    if not portal.portal_capture_enabled(state):
        portal.remove_files(state)


def _ensure_dependencies(state: Optional[DisplayState] = None) -> List[str]:
    """Legacy lifecycle seam; dependency probing remains in diagnostics."""
    from display import diagnostics

    if state is None:
        state = _state.load_state()
    return diagnostics.missing_dependencies(
        which_fn=shutil.which,
        capture_method=state.capture_method,
    )


def refresh_managed_files(state: Optional[DisplayState] = None) -> DisplayState:
    if state is None:
        state = _state.load_state()
    if not state.sunshine_execstart:
        state = _svc.remember_sunshine_execstart(state)
    _write_managed_files(state)
    # Portal capture has to be pinned in Sunshine's own config: the automatic
    # capture order prefers KMS over the portal.
    state = portal.sync_capture_config(state)
    _state.save_state(state)
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
        "portal_bus_script",
        "portal_start_script",
        "portal_routing_conf",
        "portal_wlr_config",
        "portal_wlr_portal",
    ]
    return [Path(getattr(paths, key)) for key in keys if getattr(paths, key, "")]


# Files written by older releases that no longer generate them. Without this
# a reset leaves them on disk forever: it only unlinks the paths the current
# release manages, which then also blocks the directory rmdir below.
LEGACY_BIN_FILES = (
    "lutristosunshine-input-bridge.py",
    "lutristosunshine-start-headless-hyprland.sh",
    "lutristosunshine-get-gpu-addr.sh",
)
LEGACY_PROFILE_FILES = (
    "hyprland.conf",
    "hyprland-instance",
)
# Leftovers from runs that died before their trap could clean up.
LEGACY_PROFILE_GLOBS = (
    "portal-env-*",
    "systemd-env-*",
    "portal-monitor-*.log",
)


def _legacy_managed_paths(state: DisplayState) -> List[Path]:
    """Paths left behind by older releases that this one no longer writes."""
    legacy: List[Path] = []
    bin_root = state.paths.bin_root
    if bin_root:
        legacy.extend(Path(bin_root) / name for name in LEGACY_BIN_FILES)
    profile_root = state.paths.profile_root
    if profile_root:
        artifacts = [Path(profile_root) / name for name in LEGACY_PROFILE_FILES]
        for pattern in LEGACY_PROFILE_GLOBS:
            artifacts.extend(sorted(Path(profile_root).glob(pattern)))
        legacy.extend(artifacts)
    return legacy


def _daemon_reload() -> None:
    _svc.daemon_reload()


def setup_display(
    *,
    refresh_managed_files_fn: Optional[Callable[[DisplayState], DisplayState]] = None,
    install_udev_rule_fn: Callable[[DisplayState], bool] = _install_udev_rule,
    audio_setup_fn: Optional[Callable[[DisplayState], None]] = None,
) -> int:
    refresh_managed_files_fn = refresh_managed_files_fn or refresh_managed_files
    audio_setup_fn = audio_setup_fn or audio_policy.setup
    missing = _ensure_dependencies(_state.load_state())
    if missing:
        print("Missing required commands:", ", ".join(missing))
        return 1

    state = _state.load_state()
    sunshine_was_active = _svc.is_sunshine_service_active()

    # Re-detect and persist the unit name so overrides go to the right directory,
    # even if the user switched Sunshine installations since the last run.
    state.sunshine_unit_name = _svc.sunshine_unit()
    state.paths = _state.with_sunshine_unit(state.paths, state.sunshine_unit_name)
    state.paths.sunshine_conf = str(
        installation.resolve_sunshine_config_root(state.sunshine_unit_name)
        / "sunshine.conf"
    )

    # The privileged step comes first: it is the one that can still fail, and a
    # half-written managed stack (override without its scripts) leaves the
    # Sunshine unit pointing at files that were never installed.
    if not install_udev_rule_fn(state):
        print("Error: unable to install the Sunshine input isolation udev rule.")
        print("Install sudo or pkexec, then rerun the command.")
        return 1

    state = _svc.remember_sunshine_execstart(state)
    state = refresh_managed_files_fn(state)
    audio_setup_fn(state)
    _state.save_state(state)

    _daemon_reload()
    state.enabled = True
    _state.save_state(state)
    if sunshine_was_active:
        result = _sunshine_verb(state, _svc.restart_sunshine_unit)
        if result.returncode != 0:
            print("Error: failed to restart Sunshine after installing virtual display files.")
            return 1

    print("Virtual display files installed.")
    return 0


def start_display(
    *,
    refresh_managed_files_fn: Callable[[DisplayState], DisplayState] = refresh_managed_files,
    audio_start_fn: Callable[[DisplayState], None] = audio_policy.start,
    audio_stop_fn: Callable[[DisplayState], None] = audio_policy.stop,
) -> int:
    state = _state.load_state()
    if not state.enabled:
        print("Virtual display is not set up. Run 'python3 lutristosunshine.py display enable' first.")
        return 1
    state = refresh_managed_files_fn(state)
    if not _svc.is_sunshine_service_active():
        # Recover a sink left behind by a crash before Sunshine starts and
        # before the create script decides the sink is already present.
            audio_stop_fn(state)
    audio_start_fn(state)
    _state.save_state(state)
    sunshine_unit = _svc.resolve_sunshine_unit(state)
    result = _svc.start_sunshine_unit(sunshine_unit)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        _svc.stop_sunshine_unit(sunshine_unit)
        audio_stop_fn(state)
        _state.save_state(state)
        if stderr:
            print(stderr)
        print("Error: unable to start the virtual display Sunshine service.")
        return 1
    print("Virtual display started.")
    return 0


def restart_display(
    *,
    stop_display_fn: Optional[Callable[[], int]] = None,
    start_display_fn: Optional[Callable[[], int]] = None,
) -> int:
    stop_display_fn = stop_display_fn or stop_display
    start_display_fn = start_display_fn or start_display
    state = _state.load_state()
    if not state.enabled:
        print("Virtual display is not set up. Run 'python3 lutristosunshine.py display enable' first.")
        return 1
    result = stop_display_fn()
    if result != 0:
        return result
    return start_display_fn()


def stop_display(
    *,
    audio_stop_fn: Callable[[DisplayState], None] = audio_policy.stop,
) -> int:
    state = _state.load_state()
    if not state.enabled:
        print("Virtual display is not set up.")
        return 1
    result = _sunshine_verb(state, _svc.stop_sunshine_unit)
    if result.returncode != 0:
        # A unit can already be inactive while its wrapper children or sink
        # survived. Cleanup is safe once systemd confirms Sunshine is gone.
        if not _svc.is_sunshine_service_active():
            audio_stop_fn(state)
        return 1
    audio_stop_fn(state)
    try:
        Path(state.paths.kwin_input_isolation_status_file).unlink()
    except OSError:
        pass
    _state.save_state(state)
    print("Virtual display stopped.")
    return 0


def display_snapshot() -> DisplaySnapshot:
    """Compatibility entry point preserving the manager's injectable test seams."""
    from display import diagnostics

    return diagnostics.display_snapshot(
        load_state_fn=_state.load_state,
        ensure_dependencies_fn=_ensure_dependencies,
        sunshine_input_devices_fn=_input_isolation.sunshine_virtual_input_devices,
    )


def display_doctor_report() -> DoctorReport:
    """Compatibility entry point for the diagnostics-owned doctor report."""
    from display import diagnostics

    return diagnostics.display_doctor_report(display_snapshot())

def display_logs(lines: int = 80) -> int:
    return _svc.fetch_sunshine_journal(lines).returncode


def remove_display(
    *,
    stop_display_fn: Callable[[], int] = stop_display,
    remove_udev_rule_fn: Callable[[DisplayState], bool] = _remove_udev_rule,
    daemon_reload_fn: Callable[[], None] = _daemon_reload,
    audio_remove_fn: Callable[[DisplayState], None] = audio_policy.remove,
) -> int:
    state = _state.load_state()
    if not state.enabled:
        print("Virtual display is not configured.")
        return 1

    stop_result = stop_display_fn()
    if stop_result != 0:
        print("Warning: could not stop the managed Sunshine service; continuing with file cleanup.")
    if not remove_udev_rule_fn(state):
        print("Warning: failed to remove the managed udev rule.")
    _clean_kde_libinput_config()
    audio_remove_fn(state)

    for path in [*_managed_setup_paths(state), *_legacy_managed_paths(state)]:
        try:
            path.unlink()
        except OSError:
            pass

    _svc.cleanup_managed_overrides(state)
    portal.remove_files(state)
    portal.clear_capture_config(state)

    for path_key in [
        "portal_active_file",
        "portal_lock_file",
        "kwin_input_isolation_status_file",
        "wayland_display_file",
        "audio_module_file",
        "portal_bus_address_file",
        "portal_bus_pid_file",
        "portal_ready_file",
        "portal_log_file",
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
    # Derived from the state so the temp layouts used by tests are respected;
    # in production these are the same directories the constants name.
    for directory in [
        state.paths.profile_root,
        state.paths.bin_root,
        state.paths.portal_runtime_dir,
        str(Path(state.paths.state_path).parent) if state.paths.state_path else "",
        str(LEGACY_DISPLAY_ROOT),
    ]:
        if not directory:
            continue
        try:
            Path(directory).rmdir()
        except OSError:
            pass
    daemon_reload_fn()

    print("Virtual display setup removed.")
    return 0
