import os
import argparse
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Tuple

from config.constants import DEFAULT_IMAGE, LAUNCHER_NAMES, SOURCE_COLORS, RESET_COLOR
from sunshine.sunshine import (
    get_api_connection,
    get_api_url,
    get_covers_path,
    get_server_display_name,
    save_api_connection,
    set_api_connection,
    set_installation_type,
    set_server_name,
)
from utils.utils import (
    handle_interrupt,
    get_games_found_message,
    dedupe_selected_games_by_name,
    normalize_game_name_for_dedup,
)
from utils.input import get_menu_choice, get_user_input, get_yes_no_input, get_user_selection, get_required_input, CUSTOM_COMMAND_SELECTION
from utils.terminal import accent, badge, heading, muted, state_text
from sunshine.sunshine import detect_sunshine_installation, detect_apollo_installation, add_game_to_sunshine, add_custom_command_to_sunshine, ensure_authenticated, get_existing_apps, get_running_servers, is_server_running, reconcile_display_apps, get_display_blocked_apps
from utils.steamgriddb import manage_api_key, download_image_from_steamgriddb
from config.registry import LAUNCHER_REGISTRY
from launchers.lutris import is_lutris_running
from display.manager import (
    configure_gpu,
    configure_renderer_mode,
    is_enabled as display_is_enabled,
    refresh_managed_files,
    remove_display,
    set_custom_display_mode,
    set_dynamic_mangohud_fps_limit,
    set_refresh_rate_sync_mode,
    set_renderer_mode,
    setup_display,
    start_display,
    restart_display,
    stop_display,
    display_doctor_report,
    display_snapshot,
    display_logs,
)


def _format_kv(label: str, value: str) -> str:
    return f"{accent(label)} {value}"


def _custom_display_mode_value(mode: dict) -> str:
    width = int(mode.get("width", 0) or 0)
    height = int(mode.get("height", 0) or 0)
    refresh = float(mode.get("refresh", 0) or 0)
    nearest = round(refresh)
    refresh_text = str(int(nearest)) if abs(refresh - nearest) < 0.01 else f"{refresh:.2f}"
    return f"{width}x{height}@{refresh_text}"


def _parse_custom_display_mode_value(value: str, current_mode: dict) -> Tuple[int, int, float]:
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


def parse_args(argv=None):
    def api_port_arg(value: str) -> int:
        port = int(value)
        if not 1 <= port <= 65535:
            raise argparse.ArgumentTypeError("port must be between 1 and 65535")
        return port

    parser = argparse.ArgumentParser(
        description="Import launcher games into Sunshine and manage the optional headless virtual display stack.",
    )
    parser.add_argument(
        "--cover",
        action="store_true",
        help="Automatically download SteamGridDB covers for added games.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Automatically add all listed games (skips selection prompt).",
    )
    parser.add_argument(
        "--sunshine-host",
        default="",
        help="Override the Sunshine/Apollo web UI host used for auth and API calls.",
    )
    parser.add_argument(
        "--sunshine-port",
        type=api_port_arg,
        help="Override the Sunshine/Apollo web UI port used for auth and API calls. Usually 47990.",
    )
    subparsers = parser.add_subparsers(dest="command")

    display_parser = subparsers.add_parser(
        "display",
        help="Guided management for the isolated headless virtual display stack.",
    )
    display_parser.set_defaults(command="display")
    display_subparsers = display_parser.add_subparsers(dest="display_action")
    display_subparsers.add_parser("enable", help="Set up, start, and sync the virtual display stack.")
    display_subparsers.add_parser("start", help="Start the managed Sunshine service for the virtual display without reinstalling.")
    display_subparsers.add_parser("restart", help="Restart the managed Sunshine service for the virtual display without reinstalling.")
    display_subparsers.add_parser(
        "doctor",
        help="Legacy detailed checks view. Most users should use 'display status'.",
    )
    display_subparsers.add_parser("status", help="Show the current virtual display status.")
    mangohud_parser = display_subparsers.add_parser(
        "mangohud-fps-limit",
        help="Enable or disable the dynamic MangoHud FPS limit for virtual-display launches.",
    )
    mangohud_subparsers = mangohud_parser.add_subparsers(dest="mangohud_fps_limit_action")
    mangohud_subparsers.add_parser("enable", help="Enable the dynamic MangoHud FPS limit.")
    mangohud_subparsers.add_parser("disable", help="Disable the dynamic MangoHud FPS limit.")
    refresh_rate_parser = display_subparsers.add_parser(
        "refresh-rate-mode",
        help="Choose whether virtual-display refresh follows Moonlight's requested FPS, the client's refresh rate, or a custom fixed mode.",
    )
    refresh_rate_parser.add_argument(
        "mode",
        choices=["client", "exact", "custom"],
        help="Use 'client' to follow Moonlight's requested FPS, 'exact' to use the client's refresh rate, or 'custom' for a fixed display mode.",
    )
    refresh_rate_parser.add_argument(
        "--width",
        type=int,
        help="Custom fixed width in pixels. Used with mode=custom.",
    )
    refresh_rate_parser.add_argument(
        "--height",
        type=int,
        help="Custom fixed height in pixels. Used with mode=custom.",
    )
    refresh_rate_parser.add_argument(
        "--refresh",
        type=float,
        help="Custom fixed refresh rate in Hz. Used with mode=custom.",
    )
    display_subparsers.add_parser("stop", help="Stop the managed Sunshine service for the virtual display.")
    display_subparsers.add_parser(
        "reset",
        help="Remove the virtual-display setup, restore Sunshine app launches to normal mode, and uninstall the managed files and overrides.",
    )
    logs_parser = display_subparsers.add_parser("logs", help="Show recent logs for the managed services.")
    logs_parser.add_argument(
        "--lines",
        type=int,
        default=80,
        help="Number of log lines to show.",
    )
    display_subparsers.add_parser(
        "display-gpu",
        help="Configure which GPU wlroots uses for the virtual display.",
    )
    renderer_parser = display_subparsers.add_parser(
        "renderer-mode",
        help="Choose between the default wlroots renderer and the Vulkan renderer (WLR_RENDERER=vulkan).",
    )
    renderer_parser.add_argument(
        "mode",
        choices=["default", "vulkan"],
        help="'default' lets wlroots choose, 'vulkan' forces WLR_RENDERER=vulkan.",
    )

    return parser.parse_args(argv)


def handle_display_command(args) -> int:
    def get_blocked_apps_report():
        blocked_apps, error = get_display_blocked_apps()
        return blocked_apps, error

    def _hub_status_summary(snapshot: dict) -> str:
        level = {
            "READY": "success",
            "PARTIAL": "warning",
            "STOPPED": "info",
            "NOT SET UP": "warning",
        }.get(snapshot["status_summary"], "warning")
        return badge(snapshot["status_summary"], level)

    def _hub_display_sync_summary(snapshot: dict) -> str:
        return snapshot["display_sync_summary"]

    def _hub_attention_items(snapshot: dict, blocked_apps, blocked_error) -> list[str]:
        items = []
        if snapshot["dependencies_missing"]:
            items.append(f"Missing dependencies: {', '.join(snapshot['dependencies_missing'])}.")
        if snapshot["configured"] and not snapshot["sunshine_active"]:
            items.append("Managed Sunshine is not running.")
        if snapshot["configured"] and not snapshot["sway_active"]:
            items.append("Headless Sway is not ready.")
        if blocked_error:
            items.append(f"Flatpak launch audit unavailable: {blocked_error}")
        elif blocked_apps:
            items.append(
                f"{len(blocked_apps)} Sunshine app(s) cannot launch in virtual-display mode. Use 'status' for details."
            )
        return items

    def print_hub_overview() -> None:
        snapshot = display_snapshot()
        blocked_apps, blocked_error = get_blocked_apps_report()
        print(heading("Virtual display"))
        print(_format_kv("Status:", _hub_status_summary(snapshot)))
        print(_format_kv("Display sync:", _hub_display_sync_summary(snapshot)))
        print(_format_kv("Display GPU:", snapshot['gpu_status_label']))
        print(_format_kv("Display renderer:", snapshot['renderer_status_label']))
        attention_items = _hub_attention_items(snapshot, blocked_apps, blocked_error)
        if attention_items:
            print(_format_kv("Attention:", badge("CHECK", "warning")))
            for item in attention_items:
                print(f"- {item}")
        else:
            print(_format_kv("Attention:", f"{badge('OK', 'success')} nothing needs action right now"))
        print(_format_kv("Next step:", state_text(snapshot['next_step'], "accent")))

    def print_dashboard(include_blocked_apps: bool = True) -> None:
        snapshot = display_snapshot()
        print(heading("Virtual display"))
        status_parts = [
            f"setup={state_text('configured' if snapshot['configured'] else 'not configured', 'success' if snapshot['configured'] else 'warning')}",
            f"Sunshine={state_text('active' if snapshot['sunshine_active'] else 'inactive', 'success' if snapshot['sunshine_active'] else 'warning')}",
            f"Sway={state_text('active' if snapshot['sway_active'] else 'inactive', 'success' if snapshot['sway_active'] else 'warning')}",
        ]
        runtime_parts = [
            f"audio={state_text(snapshot['wireplumber_policy'], 'info')}",
            f"launch helper={state_text('active' if snapshot['portal_handoff_active'] else 'idle', 'success' if snapshot['portal_handoff_active'] else 'warning')}",
        ]
        print(_format_kv("Status:", ", ".join(status_parts)))
        print(_format_kv("Runtime:", ", ".join(runtime_parts)))
        print(_format_kv("Host session:", snapshot["host_session"]))
        print(
            _format_kv(
                "Input isolation:",
                f"{state_text(snapshot['input_isolation_mode'], snapshot['isolation_level'])} {muted('- ' + snapshot['isolation_summary'])}",
            )
        )
        mangohud_status = (
            f"{badge('ENABLED', 'success')} MangoHud games cap FPS to match the stream"
            if snapshot["dynamic_mangohud_fps_limit"]
            else f"{badge('DISABLED', 'info')} MangoHud games keep their normal FPS"
        )
        print(_format_kv("Auto FPS limit (MangoHud):", mangohud_status))
        print(
            _format_kv(
                "MangoHud env value:",
                snapshot["current_mangohud_config"] or state_text("not active", "warning"),
            )
        )
        print(
            _format_kv(
                "Refresh rate sync mode:",
                snapshot["refresh_rate_sync_mode_summary"],
            )
        )
        if snapshot["refresh_rate_sync_mode"] == "custom":
            print(_format_kv("Custom display target:", snapshot["custom_display_mode_summary"]))
        if snapshot["dependencies_missing"]:
            print(
                _format_kv(
                    "Dependencies:",
                    f"{badge('MISSING', 'error')} {', '.join(snapshot['dependencies_missing'])}",
                )
            )
        else:
            print(_format_kv("Dependencies:", f"{badge('OK', 'success')} all required commands available"))
        print(_format_kv("Headless display:", snapshot['wayland_display'] or state_text("not detected", "warning")))
        print(
            _format_kv(
                "Current headless mode:",
                snapshot["current_headless_mode"] or state_text("not detected", "warning"),
            )
        )
        print(_format_kv("Virtual display GPU:", snapshot['gpu_status_label']))
        print(_format_kv("Display renderer:", snapshot['renderer_status_label']))
        if include_blocked_apps:
            blocked_apps, error = get_blocked_apps_report()
            if error:
                print(_format_kv("Blocked apps:", f"{badge('WARN', 'warning')} {error}"))
            elif blocked_apps:
                print(_format_kv("Blocked apps:", badge("WARN", "warning")))
                for app_name, issue in blocked_apps:
                    print(f"- {app_name}: {issue}")
            else:
                print(_format_kv("Blocked apps:", f"{badge('OK', 'success')} none detected"))
        print(_format_kv("Next step:", state_text(snapshot['next_step'], "accent")))

    def print_doctor_report() -> None:
        report = display_doctor_report()
        status_labels = {
            "pass": "PASS",
            "warn": "WARN",
            "fail": "FAIL",
            "info": "INFO",
        }
        print(heading("Virtual display doctor"))
        for check in report["checks"]:
            level = {
                "pass": "success",
                "warn": "warning",
                "fail": "error",
                "info": "info",
            }.get(check["status"], "info")
            print(f"{badge(status_labels.get(check['status'], 'INFO'), level)} {check['label']}: {check['message']}")
        blocked_apps, error = get_blocked_apps_report()
        if error:
            print(f"{badge('WARN', 'warning')} Flatpak launch audit: {error}")
        elif blocked_apps:
            print(f"{badge('WARN', 'warning')} Flatpak launch audit: some Sunshine apps cannot be launched in virtual display mode.")
            for app_name, issue in blocked_apps:
                print(f"- {app_name}: {issue}")
        else:
            print(f"{badge('PASS', 'success')} Flatpak launch audit: no blocked apps detected.")
        print(_format_kv("Next step:", state_text(report['next_step'], "accent")))

    def reconcile_apps(enable_display: bool) -> int:
        if enable_display and display_is_enabled():
            refresh_managed_files()

        started_here = False
        if not is_server_running("sunshine"):
            start_status = start_display()
            if start_status != 0:
                return start_status
            started_here = True

        updated, error = reconcile_display_apps(enable_display=enable_display)
        if error:
            print(error)
            return 1

        verb = "Reconciled" if enable_display else "Restored"
        print(f"{verb} {updated} Sunshine app(s) {'for' if enable_display else 'from'} virtual display mode.")
        if enable_display:
            blocked_apps, blocked_error = get_blocked_apps_report()
            if blocked_error:
                print(f"{badge('WARN', 'warning')} unable to inspect Sunshine apps for blocked Flatpak launches. {blocked_error}")
            elif blocked_apps:
                print(_format_kv("Blocked Flatpak apps:", badge("WARN", "warning")))
                for app_name, issue in blocked_apps:
                    print(f"- {app_name}: {issue}")

        if started_here and not enable_display:
            return stop_display()
        return 0

    def run_enable() -> int:
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

    def run_reset() -> int:
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

    def run_start() -> int:
        return start_display()

    def run_restart() -> int:
        return restart_display()

    def update_mangohud_fps_limit(enabled: bool) -> int:
        previous, _ = set_dynamic_mangohud_fps_limit(enabled)
        if previous == enabled:
            state = "on" if enabled else "off"
            print(f"Auto FPS limit already {state}.")
        else:
            state = "on" if enabled else "off"
            print(f"Auto FPS limit turned {state}.")
        print("Only affects games that already use MangoHud.")
        return 0

    def update_refresh_rate_mode(mode: str) -> int:
        normalized_mode = mode if mode in {"exact", "custom"} else "client"
        previous, _ = set_refresh_rate_sync_mode(normalized_mode)
        summary = {
            "exact": "client's refresh rate",
            "custom": "custom fixed display mode",
        }.get(normalized_mode, "follow Moonlight requested FPS")
        if previous == normalized_mode:
            print(f"Refresh rate sync mode is already set to {summary} for virtual-display launches.")
        else:
            print(f"Refresh rate sync mode set to {summary} for virtual-display launches.")
        if normalized_mode == "custom":
            print(f"Custom display target: {display_snapshot()['custom_display_mode_summary']}")
        print("This controls both the virtual-display output mode and the dynamic MangoHud FPS limit source.")
        return 0

    def configure_custom_display_mode(interactive: bool) -> int:
        snapshot = display_snapshot()
        current_mode = snapshot["custom_display_mode"]
        width = args.width if getattr(args, "width", None) is not None else current_mode["width"]
        height = args.height if getattr(args, "height", None) is not None else current_mode["height"]
        refresh = args.refresh if getattr(args, "refresh", None) is not None else current_mode["refresh"]

        if interactive:
            print("")
            print("Custom fixed display mode")
            print(f"Current target: {snapshot['custom_display_mode_summary']}")
            width, height, refresh = get_user_input(
                f"Enter the desired resolution and refresh rate as WidthxHeight@RefreshRate (e.g. {_custom_display_mode_value(current_mode)}): ",
                lambda value: _parse_custom_display_mode_value(value, current_mode),
                "Enter widthxheight@refreshrate, for example 1920x1080@60.",
            )

        set_custom_display_mode(width, height, refresh)
        return update_refresh_rate_mode("custom")

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
                    result = run_start()
                    if result != 0:
                        return result
                elif tool_choice == "2":
                    result = stop_display()
                    if result != 0:
                        return result
                elif tool_choice == "3":
                    result = run_restart()
                    if result != 0:
                        return result
                elif tool_choice == "4":
                    enabling = not display_snapshot()["dynamic_mangohud_fps_limit"]
                    prompt = (
                        "Cap FPS to match the stream?"
                        if enabling
                        else "Stop capping FPS to match the stream?"
                    )
                    if get_yes_no_input(prompt, default=True):
                        result = update_mangohud_fps_limit(enabling)
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
                        return run_reset()

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
            print(f"{muted('0.')} Exit")
            choice = get_menu_choice(f"{accent('Choose an action: ')}", ["0", "1", "2", "3", "4", "5", "6"])
            if choice == "0":
                return 0
            if choice == "1":
                result = run_enable()
                if result != 0:
                    return result
            elif choice == "2":
                print("")
                print_dashboard()
            elif choice == "3":
                print("")
                print("Display sync mode")
                print("1. Follow Moonlight's requested resolution and FPS")
                print("2. Use the client's refresh rate")
                print('3. Use a custom resolution and refresh rate')
                print("0. Cancel")
                mode_choice = get_menu_choice("Choose a mode: ", ["0", "1", "2", "3"])
                if mode_choice == "1":
                    result = update_refresh_rate_mode("client")
                    if result != 0:
                        return result
                elif mode_choice == "2":
                    result = update_refresh_rate_mode("exact")
                    if result != 0:
                        return result
                elif mode_choice == "3":
                    result = configure_custom_display_mode(interactive=True)
                    if result != 0:
                        return result
            elif choice == "4":
                result = configure_gpu()
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

    action = args.display_action
    if action is None:
        return run_hub()
    if action == "enable":
        return run_enable()
    if action == "start":
        return run_start()
    if action == "restart":
        return run_restart()
    if action == "doctor":
        print_doctor_report()
        return 0
    if action == "status":
        print_dashboard()
        return 0 if display_snapshot()["configured"] else 1
    if action == "mangohud-fps-limit":
        if args.mangohud_fps_limit_action == "enable":
            return update_mangohud_fps_limit(True)
        if args.mangohud_fps_limit_action == "disable":
            return update_mangohud_fps_limit(False)
        print("Choose 'enable' or 'disable' for 'display mangohud-fps-limit'.")
        return 1
    if action == "refresh-rate-mode":
        if args.mode == "custom":
            supplied = [getattr(args, "width", None), getattr(args, "height", None), getattr(args, "refresh", None)]
            if any(value is not None for value in supplied) and not all(value is not None for value in supplied):
                print("Provide --width, --height, and --refresh together when choosing custom mode.")
                return 1
            if all(value is not None for value in supplied):
                if args.width <= 0 or args.height <= 0 or args.refresh <= 0:
                    print("Custom width, height, and refresh must all be greater than 0.")
                    return 1
                set_custom_display_mode(args.width, args.height, args.refresh)
        return update_refresh_rate_mode(args.mode)
    if action == "stop":
        return stop_display()
    if action == "reset":
        return run_reset()
    if action == "logs":
        return display_logs(args.lines)
    if action == "display-gpu":
        return configure_gpu()
    if action == "renderer-mode":
        set_renderer_mode(args.mode)
        return 0
    print(f"Unknown display action: {action}")
    return 1



def add_custom_command_flow() -> None:
    """Prompt for a name + command and add it to the active server."""
    print("")
    print(heading("Add custom command"))
    name = get_required_input("Entry name: ", "Name cannot be empty.")
    command = get_required_input("Command to run: ", "Command cannot be empty.")

    existing = {normalize_game_name_for_dedup(app["name"]) for app in get_existing_apps()}
    if normalize_game_name_for_dedup(name) in existing:
        print(f"Warning: an entry named '{name}' already exists in {get_server_display_name()}.")

    image_path = DEFAULT_IMAGE
    if get_yes_no_input(f"Download a cover from SteamGridDB for '{name}'? (y/n): "):
        api_key = manage_api_key()
        if api_key:
            try:
                image_path = download_image_from_steamgriddb(name, api_key)
            except Exception as e:
                print(f"Error downloading image for {name}: {e}")
                image_path = DEFAULT_IMAGE

    add_custom_command_to_sunshine(name, command, image_path)


def main(argv=None):
    def prompt_server_connection() -> Tuple[str, int]:
        current_host, current_port = get_api_connection()
        print(f"{get_server_display_name()} web UI address: {get_api_url()}")
        print("Use the HTTPS web UI port here. The default is 47990, not the game streaming port.")

        host = input(f"Host [{current_host}]: ").strip() or current_host
        port = get_user_input(
            f"Port [{current_port}]: ",
            lambda value: current_port if value.strip() == "" else _validate_port_input(value),
            "Invalid port. Enter a number from 1 to 65535.",
        )
        return host, port

    def _validate_port_input(value: str) -> int:
        port = int(value.strip())
        if not 1 <= port <= 65535:
            raise ValueError()
        return port

    def configure_connection_and_retry_auth(server_name: str) -> bool:
        if server_name != "sunshine":
            return False

        while True:
            current_url = get_api_url()
            prompt = (
                f"Authentication failed using {get_server_display_name()} at {current_url}. "
                "Configure a different web UI host or port and try again?"
            )
            if not get_yes_no_input(prompt, default=True):
                return False

            host, port = prompt_server_connection()
            set_api_connection(host=host, port=port)

            if ensure_authenticated(allow_prompt=True):
                save_api_connection(host, port, server_name=server_name)
                print(f"Saved {get_server_display_name()} web UI address: {get_api_url()}")
                return True

    args = parse_args(argv)
    if args.command == "display":
        raise SystemExit(handle_display_command(args))
    try:
        sunshine_installed, sunshine_install_type = detect_sunshine_installation()
        apollo_installed = detect_apollo_installation()
        if not sunshine_installed and not apollo_installed:
            print("Error: No Sunshine or Apollo installation detected.")
            return

        running_servers = get_running_servers()
        if not running_servers:
            print("Error: Sunshine or Apollo is not running. Please start it and try again.")
            return

        if "sunshine" in running_servers and "apollo" in running_servers:
            while True:
                choice = input("Both Sunshine and Apollo are running. Use (1) Sunshine or (2) Apollo? ").strip().lower()
                if choice in ("1", "sunshine", "s"):
                    server_name = "sunshine"
                    break
                if choice in ("2", "apollo", "a"):
                    server_name = "apollo"
                    break
                print("Please enter 1 for Sunshine or 2 for Apollo.")
        else:
            server_name = running_servers[0]

        if server_name == "sunshine":
            if not sunshine_installed:
                print("Error: Sunshine is not installed.")
                return
            set_installation_type(sunshine_install_type)
        else:
            if not apollo_installed:
                print("Error: Apollo is not installed.")
                return
            set_installation_type("native")

        set_server_name(server_name)
        if args.sunshine_host or args.sunshine_port is not None:
            set_api_connection(
                host=args.sunshine_host or None,
                port=args.sunshine_port,
            )
        if not is_server_running(server_name):
            print(f"Error: {server_name.title()} is not running. Please start it and try again.")
            return

        COVERS_PATH = get_covers_path()
        os.makedirs(COVERS_PATH, exist_ok=True)

        authenticated = ensure_authenticated(allow_prompt=True)
        if not authenticated:
            authenticated = configure_connection_and_retry_auth(server_name)

        if not authenticated:
            print("Error: Could not obtain valid authentication. Exiting.")
            return

        if args.sunshine_host or args.sunshine_port is not None:
            save_api_connection(
                args.sunshine_host or None,
                args.sunshine_port,
                server_name=server_name,
            )

        detected_launchers = {
            name: entry["detect"]()
            for name, entry in LAUNCHER_REGISTRY.items()
        }

        if not any(detected_launchers.values()):
            names = ", ".join(LAUNCHER_NAMES[:-1]) + " or " + LAUNCHER_NAMES[-1]
            print(f"No {names} installation detected.")
            if args.all:
                return
            print("")
            print(f"{accent('1.')} Add custom command")
            print(f"{muted('0.')} Exit")
            choice = get_menu_choice(f"{accent('Choose an option: ')}", ["0", "1"])
            if choice == "1":
                add_custom_command_flow()
            return

        if detected_launchers["Lutris"] and is_lutris_running():
            print("Error: Lutris is currently running. Please close Lutris and try again.")
            return

        with ThreadPoolExecutor() as executor:
            futures = {
                name: executor.submit(LAUNCHER_REGISTRY[name]["list"])
                for name in LAUNCHER_NAMES
                if detected_launchers[name]
            }

            all_games = []
            for source_name, future in futures.items():
                result = future.result()
                all_games.extend(LAUNCHER_REGISTRY[source_name]["normalize"](result))

        if not all_games:
            print("No games found in any detected launcher.")
            if args.all:
                return
            print("")
            print(f"{accent('1.')} Add custom command")
            print(f"{muted('0.')} Exit")
            choice = get_menu_choice(f"{accent('Choose an option: ')}", ["0", "1"])
            if choice == "1":
                add_custom_command_flow()
            return

        games_found_message = get_games_found_message(detected_launchers)
        print(games_found_message)

        existing_apps = get_existing_apps()
        existing_game_names_normalized = {
            normalize_game_name_for_dedup(app["name"]) for app in existing_apps
        }

        all_games.sort(key=lambda x: x[1])

        _game_name_cache: Dict[str, str] = {
            g.game_name: normalize_game_name_for_dedup(g.game_name)
            for g in all_games
        }

        for idx, (_, game_name, display_source, _) in enumerate(all_games):
            status = (
                f"(already in {get_server_display_name()})"
                if _game_name_cache[game_name] in existing_game_names_normalized
                else ""
            )
            if len(futures) > 1:
                source_color = SOURCE_COLORS.get(display_source, "")
                source_info = f"{source_color}({display_source}){RESET_COLOR}"
                print(f"{idx + 1}. {game_name} {source_info} {status}")
            else:
                print(f"{idx + 1}. {game_name} {status}")

        if args.all:
            selected_indices = list(range(len(all_games)))
        else:
            selection = get_user_selection([(game_id, game_name) for game_id, game_name, _, _ in all_games])
            if selection == CUSTOM_COMMAND_SELECTION:
                add_custom_command_flow()
                return
            selected_indices = selection

        selected_games = [
            all_games[i]
            for i in selected_indices
            if _game_name_cache[all_games[i].game_name] not in existing_game_names_normalized
        ]

        selected_games, skipped_duplicates = dedupe_selected_games_by_name(selected_games)

        for skipped_game, retained_game in skipped_duplicates:
            _, skipped_name, skipped_source, _ = skipped_game
            _, _, retained_source, _ = retained_game
            print(
                f"Skipping duplicate '{skipped_name}' from {skipped_source}; "
                f"using {retained_source}."
            )

        if not selected_games:
            print(f"No new games to add to {get_server_display_name()} configuration.")
            return

        valid_selected_games = []
        for game_id, game_name, display_source, source in selected_games:
            if display_source == "RetroArch":
                core_info = source if isinstance(source, dict) else {}
                core_path = (core_info.get("core_path", "") or "").strip()
                core_name = (core_info.get("core_name", "") or "").strip()
                if core_path.upper() == "DETECT" or core_name.upper() == "DETECT" or not core_path:
                    print(
                        f"Error: RetroArch core not set for '{game_name}'. Please associate the game with a core in RetroArch before adding it to {get_server_display_name()}."
                    )
                    continue
            valid_selected_games.append((game_id, game_name, display_source, source))

        if not valid_selected_games:
            print("No games ready to add. Please resolve the reported issues and try again.")
            return

        download_images = args.cover or get_yes_no_input("Do you want to download images from SteamGridDB? (y/n): ")
        api_key = manage_api_key() if download_images else None

        games_added = False
        with ThreadPoolExecutor() as executor:
            futures = {}
            for game_id, game_name, display_source, source in valid_selected_games:
                if download_images and api_key:
                    future = executor.submit(download_image_from_steamgriddb, game_name, api_key)
                    futures[future] = (game_id, game_name, source)
                else:
                    add_game_to_sunshine(game_id, game_name, DEFAULT_IMAGE, source)
                    games_added = True

            for future in as_completed(futures):
                game_id, game_name, source = futures[future]
                try:
                    image_path = future.result()
                except Exception as e:
                    print(f"Error downloading image for {game_name}: {e}")
                    image_path = DEFAULT_IMAGE

                add_game_to_sunshine(game_id, game_name, image_path, source)
                games_added = True

        if games_added:
            print(f"Games added to {get_server_display_name()} successfully.")
        else:
            print(f"No new games were added to {get_server_display_name()}.")

    except (KeyboardInterrupt, EOFError):
        handle_interrupt()

if __name__ == "__main__":
    main()
