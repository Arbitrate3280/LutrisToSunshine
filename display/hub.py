"""Display hub: orchestration and presentation for the virtual display CLI.

The CLI dispatches to these functions; all interactive menus, status
rendering, and multi-step orchestration live here.
"""
import re
from typing import List, Optional, Tuple

from display.gpu import configure_gpu
from display.diagnostics import DisplaySnapshot, as_display_snapshot
from display.modes import DisplayMode
from display import portal
from display.state import is_enabled, load_state
from display.manager import (
    display_doctor_report,
    display_logs,
    display_snapshot,
    refresh_managed_files,
    remove_display,
    restart_display,
    set_capture_method,
    set_dynamic_mangohud_fps_limit,
    set_renderer_mode,
    set_refresh_rate_sync_mode,
    setup_display,
    start_display,
    stop_display,
)
from sunshine import catalog
from sunshine.connection import CONNECTION
from utils.input import get_menu_choice, get_user_input, get_yes_no_input
from utils.terminal import accent, badge, heading, muted, state_text


def _format_kv(label: str, value: str) -> str:
    return f"{accent(label)} {value}"


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


def configure_capture_method() -> int:
    state = load_state()
    print("")
    print("Virtual display capture backend")
    print("How Sunshine reads the frames from the headless display.")
    print("wlr: Sunshine captures the headless output directly. Lightest option.")
    print("portal: Sunshine captures through a private xdg-desktop-portal session.")
    print("        Standardized API and PipeWire transport; needs the portal packages.")
    print(f"Current: {portal.capture_method_label(state)}")
    print("")
    print("  0. wlr — direct wlroots screencopy (no extra packages)")
    print("  1. portal — private xdg-desktop-portal + PipeWire session")
    print("")
    choice = get_user_input(
        "Choose capture backend: ",
        lambda value: value.strip() if value.strip() in {"0", "1"} else (_ for _ in ()).throw(ValueError()),
        "Enter 0 for wlr or 1 for portal.",
    )
    method = portal.CAPTURE_WLR if choice == "0" else portal.CAPTURE_PORTAL
    previous, _ = set_capture_method(method)
    if previous == method:
        print(f"Capture backend already set to {method}.")
    else:
        print(f"Capture backend set to {method}.")
    if method == portal.CAPTURE_PORTAL:
        print("Sunshine's capture setting is pinned to 'portal' while this is enabled.")
        print("Input isolation and the virtual display are unchanged.")
    else:
        print("Sunshine's capture setting was restored; the portal files were removed.")

    print("")
    if get_yes_no_input("Restart the virtual display for the change to take effect?", default=True):
        return restart_display()
    return 0


def _custom_display_mode_value(mode: DisplayMode) -> str:
    width = int(mode.get("width", 0) or 0)
    height = int(mode.get("height", 0) or 0)
    refresh = float(mode.get("refresh", 0) or 0)
    nearest = round(refresh)
    refresh_text = str(int(nearest)) if abs(refresh - nearest) < 0.01 else f"{refresh:.2f}"
    return f"{width}x{height}@{refresh_text}"


def _parse_custom_display_mode_value(value: str, current_mode: DisplayMode) -> Tuple[int, int, float]:
    raw_value = value.strip()
    if raw_value == "":
        return (
            int(current_mode["width"]),
            int(current_mode["height"]),
            float(current_mode["refresh"]),
        )

    match = re.fullmatch(r"(\d+)\s*[xX]\s*(\d+)\s*@\s*(\d+(?:\.\d+)?)", raw_value)
    if not match:
        raise ValueError()

    width = int(match.group(1))
    height = int(match.group(2))
    refresh = float(match.group(3))
    if width <= 0 or height <= 0 or refresh <= 0:
        raise ValueError()
    return width, height, refresh


def blocked_apps_report() -> Tuple[List[Tuple[str, str]], Optional[str]]:
    return catalog.get_display_blocked_apps()


def hub_status_summary(snapshot: DisplaySnapshot) -> str:
    level = {
        "READY": "success",
        "PARTIAL": "warning",
        "STOPPED": "info",
        "NOT SET UP": "warning",
    }.get(snapshot.status_summary, "warning")
    return badge(snapshot.status_summary, level)


def hub_display_sync_summary(snapshot: DisplaySnapshot) -> str:
    return snapshot.display_sync_summary


def hub_attention_items(
    snapshot: DisplaySnapshot,
    blocked_apps: List[Tuple[str, str]],
    blocked_error: Optional[str],
) -> List[str]:
    items: List[str] = []
    if snapshot.dependencies_missing:
        items.append(f"Missing dependencies: {', '.join(snapshot.dependencies_missing)}.")
    if snapshot.configured and not snapshot.sunshine_active:
        items.append("Managed Sunshine is not running.")
    if snapshot.configured and not snapshot.sway_active:
        items.append("Headless Sway is not ready.")
    if blocked_error:
        items.append(f"Flatpak launch audit unavailable: {blocked_error}")
    elif blocked_apps:
        items.append(
            f"{len(blocked_apps)} Sunshine app(s) cannot launch in virtual-display mode. Use 'status' for details."
        )
    if snapshot.capture_method == portal.CAPTURE_PORTAL and not snapshot.portal_names_active:
        items.append(
            snapshot.portal_error
            or "The private portal session is not serving the virtual display yet."
        )
    return items


def print_hub_overview() -> None:
    snapshot = as_display_snapshot(display_snapshot())
    blocked_apps, blocked_error = blocked_apps_report()
    print(heading("Virtual display"))
    print(_format_kv("Status:", hub_status_summary(snapshot)))
    print(_format_kv("Display sync:", hub_display_sync_summary(snapshot)))
    print(_format_kv("Display GPU:", snapshot.gpu_status_label))
    print(_format_kv("Display renderer:", snapshot.renderer_status_label))
    print(_format_kv("Capture backend:", snapshot.capture_method_label))
    attention_items = hub_attention_items(snapshot, blocked_apps, blocked_error)
    if attention_items:
        print(_format_kv("Attention:", badge("CHECK", "warning")))
        for item in attention_items:
            print(f"- {item}")
    else:
        print(_format_kv("Attention:", f"{badge('OK', 'success')} nothing needs action right now"))
    print(_format_kv("Next step:", state_text(snapshot.next_step, "accent")))


def print_dashboard(
    include_blocked_apps: bool = True,
    *,
    snapshot: Optional[DisplaySnapshot] = None,
) -> None:
    snapshot = snapshot or as_display_snapshot(display_snapshot())
    print(heading("Virtual display"))
    status_parts = [
        f"setup={state_text('configured' if snapshot.configured else 'not configured', 'success' if snapshot.configured else 'warning')}",
        f"Sunshine={state_text('active' if snapshot.sunshine_active else 'inactive', 'success' if snapshot.sunshine_active else 'warning')}",
        f"Sway={state_text('active' if snapshot.sway_active else 'inactive', 'success' if snapshot.sway_active else 'warning')}",
    ]
    runtime_parts = [
        f"audio={state_text(snapshot.wireplumber_policy, 'info')}",
        f"launch helper={state_text('active' if snapshot.portal_handoff_active else 'idle', 'success' if snapshot.portal_handoff_active else 'warning')}",
    ]
    print(_format_kv("Status:", ", ".join(status_parts)))
    print(_format_kv("Runtime:", ", ".join(runtime_parts)))
    print(_format_kv("Host session:", snapshot.host_session))
    print(
        _format_kv(
            "Input isolation:",
            f"{state_text(snapshot.input_isolation_mode, snapshot.isolation_level)} {muted('- ' + snapshot.isolation_summary)}",
        )
    )
    mangohud_status = (
        f"{badge('ENABLED', 'success')} MangoHud games cap FPS to match the stream"
        if snapshot.dynamic_mangohud_fps_limit
        else f"{badge('DISABLED', 'info')} MangoHud games keep their normal FPS"
    )
    print(_format_kv("Auto FPS limit (MangoHud):", mangohud_status))
    print(
        _format_kv(
            "MangoHud env value:",
            snapshot.current_mangohud_config or state_text("not active", "warning"),
        )
    )
    print(
        _format_kv(
            "Refresh rate sync mode:",
            snapshot.refresh_rate_sync_mode_summary,
        )
    )
    if snapshot.refresh_rate_sync_mode == "custom":
        print(_format_kv("Custom display target:", snapshot.custom_display_mode_summary))
    if snapshot.dependencies_missing:
        print(
            _format_kv(
                "Dependencies:",
                f"{badge('MISSING', 'error')} {', '.join(snapshot.dependencies_missing)}",
            )
        )
    else:
        print(_format_kv("Dependencies:", f"{badge('OK', 'success')} all required commands available"))
    print(_format_kv("Headless display:", snapshot.wayland_display or state_text("not detected", "warning")))
    print(
        _format_kv(
            "Current headless mode:",
            snapshot.current_headless_mode or state_text("not detected", "warning"),
        )
    )
    print(_format_kv("Virtual display GPU:", snapshot.gpu_status_label))
    print(_format_kv("Display renderer:", snapshot.renderer_status_label))
    print(
        _format_kv(
            "Capture backend:",
            f"{snapshot.capture_method_label} {muted('- ' + snapshot.capture_method_summary)}",
        )
    )
    if snapshot.capture_method == portal.CAPTURE_PORTAL:
        if snapshot.portal_names_active:
            portal_state = state_text("ready", "success")
        elif snapshot.portal_bus_active:
            portal_state = state_text("starting", "warning")
        else:
            portal_state = state_text("not running", "warning")
        portal_note = f" {muted('- ' + snapshot.portal_error)}" if snapshot.portal_error else ""
        print(_format_kv("Portal session:", f"{portal_state}{portal_note}"))
    if include_blocked_apps:
        blocked_apps, error = blocked_apps_report()
        if error:
            print(_format_kv("Blocked apps:", f"{badge('WARN', 'warning')} {error}"))
        elif blocked_apps:
            print(_format_kv("Blocked apps:", badge("WARN", "warning")))
            for app_name, issue in blocked_apps:
                print(f"- {app_name}: {issue}")
        else:
            print(_format_kv("Blocked apps:", f"{badge('OK', 'success')} none detected"))
    print(_format_kv("Next step:", state_text(snapshot.next_step, "accent")))


def print_doctor_report() -> None:
    report = display_doctor_report()
    status_labels = {
        "pass": "PASS",
        "warn": "WARN",
        "fail": "FAIL",
        "info": "INFO",
    }
    print(heading("Virtual display doctor"))
    for check in report.checks:
        level = {
            "pass": "success",
            "warn": "warning",
            "fail": "error",
            "info": "info",
        }.get(check.status, "info")
        print(f"{badge(status_labels.get(check.status, 'INFO'), level)} {check.label}: {check.message}")
    blocked_apps, error = blocked_apps_report()
    if error:
        print(f"{badge('WARN', 'warning')} Flatpak launch audit: {error}")
    elif blocked_apps:
        print(f"{badge('WARN', 'warning')} Flatpak launch audit: some Sunshine apps cannot be launched in virtual display mode.")
        for app_name, issue in blocked_apps:
            print(f"- {app_name}: {issue}")
    else:
        print(f"{badge('PASS', 'success')} Flatpak launch audit: no blocked apps detected.")
    print(_format_kv("Next step:", state_text(report.next_step, "accent")))


def reconcile_apps(enable_display: bool) -> int:
    if enable_display and is_enabled():
        refresh_managed_files()

    started_here = False
    if not CONNECTION.is_server_running("sunshine"):
        start_status = start_display()
        if start_status != 0:
            return start_status
        started_here = True

    updated, error = catalog.reconcile_display_apps(enable_display=enable_display)
    if error:
        print(error)
        return 1

    verb = "Reconciled" if enable_display else "Restored"
    print(f"{verb} {updated} Sunshine app(s) {'for' if enable_display else 'from'} virtual display mode.")
    if enable_display:
        blocked_apps, blocked_error = blocked_apps_report()
        if blocked_error:
            print(f"{badge('WARN', 'warning')} unable to inspect Sunshine apps for blocked Flatpak launches. {blocked_error}")
        elif blocked_apps:
            print(_format_kv("Blocked Flatpak apps:", badge("WARN", "warning")))
            for app_name, issue in blocked_apps:
                print(f"- {app_name}: {issue}")

    if started_here and not enable_display:
        return stop_display()
    return 0


def enable() -> int:
    setup_status = setup_display()
    if setup_status != 0:
        return setup_status
    start_status = start_display()
    if start_status != 0:
        return start_status
    reconcile_status = reconcile_apps(True)
    if reconcile_status != 0:
        print(f"{badge('WARN', 'warning')} the virtual display stack is running, but Sunshine app sync did not complete.")
        print("Run 'python3 lutristosunshine.py display status' or '... display logs', then try '... display enable' again if needed.")
        return 0

    return 0


def reset() -> int:
    reconcile_status = reconcile_apps(False)
    if reconcile_status != 0:
        print(f"{badge('WARN', 'warning')} could not reach Sunshine to restore app launches; continuing with file cleanup.")
    remove_status = remove_display()
    if remove_status == 0:
        if reconcile_status == 0:
            print("Headless streaming removed. Sunshine apps restored to normal.")
        else:
            print("Headless streaming files removed. Sunshine apps may still point to old wrappers; re-add them if needed.")
    return remove_status


def start() -> int:
    return start_display()


def restart() -> int:
    return restart_display()


def status() -> int:
    snapshot = as_display_snapshot(display_snapshot())
    print_dashboard(snapshot=snapshot)
    return 0 if snapshot.configured else 1


def set_fps_limit(enabled: bool) -> int:
    previous, _ = set_dynamic_mangohud_fps_limit(enabled)
    if previous == enabled:
        state = "on" if enabled else "off"
        print(f"Auto FPS limit already {state}.")
    else:
        state = "on" if enabled else "off"
        print(f"Auto FPS limit turned {state}.")
    print("Only affects games that already use MangoHud.")
    return 0


def set_custom_display_mode(width: int, height: int, refresh: float) -> None:
    """Set custom display mode dimensions (thin wrapper for CLI dispatch)."""
    from display.manager import set_custom_display_mode as _set_custom
    _set_custom(width, height, refresh)


def set_sync_mode(mode: str) -> int:
    normalized_mode = mode if mode in {"exact", "custom"} else "client"
    previous, _ = set_refresh_rate_sync_mode(normalized_mode)
    summary = {
        "exact": "client's exact refresh rate (fractional, e.g. 59.94/119.88)",
        "custom": "custom fixed display mode",
    }.get(normalized_mode, "Moonlight's requested FPS (integer, e.g. 60/90/120)")
    if previous == normalized_mode:
        print(f"Refresh rate sync mode is already set to {summary} for virtual-display launches.")
    else:
        print(f"Refresh rate sync mode set to {summary} for virtual-display launches.")
    if normalized_mode == "custom":
        print(f"Custom display target: {as_display_snapshot(display_snapshot()).custom_display_mode_summary}")
    print("This controls both the virtual-display output mode and the dynamic MangoHud FPS limit source.")
    return 0


def configure_custom_mode(
    interactive: bool,
    width: Optional[int] = None,
    height: Optional[int] = None,
    refresh: Optional[float] = None,
) -> int:
    snapshot = as_display_snapshot(display_snapshot())
    current_mode = snapshot.custom_display_mode
    width = width if width is not None else current_mode["width"]
    height = height if height is not None else current_mode["height"]
    refresh = refresh if refresh is not None else current_mode["refresh"]

    if interactive:
        print("")
        print("Custom fixed display mode")
        print(f"Current target: {snapshot.custom_display_mode_summary}")
        width, height, refresh = get_user_input(
            f"Enter the desired resolution and refresh rate as WidthxHeight@RefreshRate (e.g. {_custom_display_mode_value(current_mode)}): ",
            lambda value: _parse_custom_display_mode_value(value, current_mode),
            "Enter widthxheight@refreshrate, for example 1920x1080@60.",
        )

    set_custom_display_mode(width, height, refresh)
    return set_sync_mode("custom")


def run_hub() -> int:
    def run_advanced_tools_menu() -> int:
        while True:
            print("")
            print(heading("More tools"))
            print(f"{accent('1.')} Start Sunshine")
            print(f"{accent('2.')} Stop Sunshine")
            print(f"{accent('3.')} Restart Sunshine")
            print(f"{accent('4.')} Auto FPS limit (MangoHud)")
            print(f"{accent('5.')} Show logs")
            print(f"{accent('6.')} Remove headless streaming")
            print(f"{muted('0.')} Back")
            tool_choice = get_menu_choice(
                f"{accent('Choose a tool: ')}",
                ["0", "1", "2", "3", "4", "5", "6"],
            )
            if tool_choice == "0":
                return 0
            if tool_choice == "1":
                result = start()
                if result != 0:
                    return result
            elif tool_choice == "2":
                result = stop_display()
                if result != 0:
                    return result
            elif tool_choice == "3":
                result = restart()
                if result != 0:
                    return result
            elif tool_choice == "4":
                enabling = not as_display_snapshot(display_snapshot()).dynamic_mangohud_fps_limit
                prompt = (
                    "Cap FPS to match the stream?"
                    if enabling
                    else "Stop capping FPS to match the stream?"
                )
                if get_yes_no_input(prompt, default=True):
                    result = set_fps_limit(enabling)
                    if result != 0:
                        return result
            elif tool_choice == "5":
                result = display_logs(80)
                if result != 0:
                    return result
            elif tool_choice == "6":
                confirmed = get_yes_no_input(
                    "This removes all headless streaming files and restores Sunshine to normal. Continue?",
                    default=False,
                )
                if confirmed:
                    return reset()

    while True:
        print("")
        print_hub_overview()
        print("")
        print(heading("Actions"))
        print(f"{accent('1.')} Set up headless streaming")
        print(f"{accent('2.')} Show full status")
        print(f"{accent('3.')} Configure display sync mode")
        print(f"{accent('4.')} Choose which GPU to use")
        print(f"{accent('5.')} Choose renderer (GLES2 / Vulkan)")
        print(f"{accent('6.')} More tools")
        print(f"{accent('7.')} Choose capture backend (wlr / portal)")
        print(f"{muted('0.')} Exit")
        choice = get_menu_choice(f"{accent('Choose an action: ')}", ["0", "1", "2", "3", "4", "5", "6", "7"])
        if choice == "0":
            return 0
        if choice == "1":
            result = enable()
            if result != 0:
                return result
        elif choice == "2":
            print("")
            print_dashboard()
        elif choice == "3":
            print("")
            print("Display sync mode")
            print("Both client modes take the resolution from Moonlight; only the refresh rate source differs.")
            print("1. Match Moonlight's requested resolution and FPS (integer, e.g. 60/90/120)")
            print("2. Match the client's exact refresh rate (fractional, e.g. 59.94/119.88)")
            print('3. Use a custom resolution and refresh rate')
            print("0. Cancel")
            mode_choice = get_menu_choice("Choose a mode: ", ["0", "1", "2", "3"])
            if mode_choice == "1":
                result = set_sync_mode("client")
                if result != 0:
                    return result
            elif mode_choice == "2":
                result = set_sync_mode("exact")
                if result != 0:
                    return result
            elif mode_choice == "3":
                result = configure_custom_mode(interactive=True)
                if result != 0:
                    return result
        elif choice == "4":
            result = configure_gpu(
                load_state_fn=load_state,
                refresh_managed_files_fn=refresh_managed_files,
                restart_fn=restart_display,
            )
            if result != 0:
                return result
        elif choice == "5":
            result = configure_renderer_mode()
            if result != 0:
                return result
        elif choice == "6":
            result = run_advanced_tools_menu()
            if result != 0:
                return result
        elif choice == "7":
            result = configure_capture_method()
            if result != 0:
                return result
