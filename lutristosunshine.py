import sys
import os
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

from config.constants import DEFAULT_IMAGE, SOURCE_COLORS, RESET_COLOR
from sunshine.sunshine import get_covers_path, set_installation_type, set_server_name
from utils.utils import handle_interrupt, get_games_found_message
from utils.input import get_menu_choice, get_yes_no_input, get_user_selection
from utils.terminal import accent, badge, heading, muted, state_text
from sunshine.sunshine import detect_sunshine_installation, detect_apollo_installation, add_game_to_sunshine, get_existing_apps, get_auth_session, get_auth_token, get_running_servers, is_server_running, reconcile_virtual_display_apps, get_virtual_display_blocked_apps
from utils.steamgriddb import manage_api_key, download_image_from_steamgriddb
from launchers.heroic import list_heroic_games, get_heroic_command, HEROIC_PATHS
from launchers.lutris import list_lutris_games, get_lutris_command, is_lutris_running
from launchers.bottles import detect_bottles_installation, list_bottles_games
from launchers.steam import detect_steam_installation, list_steam_games, get_steam_command
from launchers.faugus import detect_faugus_installation, list_faugus_games
from launchers.ryubing import detect_ryubing_installation, list_ryubing_games
from launchers.retroarch import detect_retroarch_installation, list_retroarch_games
from launchers.eden import detect_eden_installation, list_eden_games
from virtualdisplay.manager import (
    configure_exclusive_input_devices,
    dynamic_mangohud_fps_limit_enabled,
    is_enabled as virtual_display_is_enabled,
    refresh_managed_files,
    remove_virtual_display,
    set_dynamic_mangohud_fps_limit,
    setup_virtual_display,
    start_virtual_display,
    stop_virtual_display,
    virtual_display_doctor_report,
    virtual_display_snapshot,
    virtual_display_logs,
)
from virtualdisplay.rumble import test_bridge_rumble


def _status_level(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"active", "configured", "ready", "bridged", "detected", "ok"}:
        return "success"
    if normalized in {"starting", "idle", "waiting", "not configured", "inactive", "none"}:
        return "warning"
    if normalized in {"missing", "failed", "error", "unavailable"}:
        return "error"
    return "info"


def _format_status_value(value: str) -> str:
    return state_text(value, _status_level(value))


def _format_kv(label: str, value: str) -> str:
    return f"{accent(label)} {value}"


def parse_args(argv=None):
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
    subparsers = parser.add_subparsers(dest="command")

    virtualdisplay_parser = subparsers.add_parser(
        "virtualdisplay",
        help="Guided management for the isolated headless virtual display stack.",
    )
    virtualdisplay_parser.set_defaults(command="virtualdisplay")
    virtualdisplay_subparsers = virtualdisplay_parser.add_subparsers(dest="virtualdisplay_action")
    virtualdisplay_subparsers.add_parser("enable", help="Set up, start, and sync the virtual display stack.")
    virtualdisplay_subparsers.add_parser("doctor", help="Inspect the virtual display setup and suggest fixes.")
    virtualdisplay_subparsers.add_parser("controllers", help="Choose which host controllers are reserved for the virtual display.")
    virtualdisplay_subparsers.add_parser("status", help="Show the current virtual display status.")
    mangohud_parser = virtualdisplay_subparsers.add_parser(
        "mangohud-fps-limit",
        help="Enable or disable the dynamic MangoHud FPS limit for virtual-display launches.",
    )
    mangohud_subparsers = mangohud_parser.add_subparsers(dest="mangohud_fps_limit_action")
    mangohud_subparsers.add_parser("enable", help="Enable the dynamic MangoHud FPS limit.")
    mangohud_subparsers.add_parser("disable", help="Disable the dynamic MangoHud FPS limit.")
    virtualdisplay_subparsers.add_parser("stop", help="Stop the managed headless Sway and Sunshine services.")
    rumble_parser = virtualdisplay_subparsers.add_parser(
        "rumble",
        help="Send a test rumble signal through the bridged virtual controller path.",
    )
    rumble_parser.add_argument(
        "--controller",
        default="",
        help="Controller number from the prompt, selection id, or label substring.",
    )
    rumble_parser.add_argument(
        "--mode",
        choices=["auto", "evdev", "hidraw-ds4"],
        default="auto",
        help="Which virtual-device path to use for the rumble probe.",
    )
    rumble_parser.add_argument(
        "--duration",
        type=float,
        default=1.0,
        help="Length of each rumble pulse in seconds.",
    )
    rumble_parser.add_argument(
        "--repeat",
        type=int,
        default=2,
        help="How many pulses to send.",
    )
    rumble_parser.add_argument(
        "--pause",
        type=float,
        default=0.4,
        help="Pause between pulses in seconds.",
    )
    rumble_parser.add_argument(
        "--strong",
        type=int,
        default=0xC000,
        help="Strong motor magnitude from 0 to 65535.",
    )
    rumble_parser.add_argument(
        "--weak",
        type=int,
        default=0x8000,
        help="Weak motor magnitude from 0 to 65535.",
    )
    virtualdisplay_subparsers.add_parser(
        "reset",
        help="Turn off virtual display, restore Sunshine app launches to normal mode, and remove the managed setup.",
    )
    logs_parser = virtualdisplay_subparsers.add_parser("logs", help="Show recent logs for the managed services.")
    logs_parser.add_argument(
        "--lines",
        type=int,
        default=80,
        help="Number of log lines to show.",
    )

    return parser.parse_args(argv)


def handle_virtualdisplay_command(args) -> int:
    def get_blocked_apps_report():
        blocked_apps, error = get_virtual_display_blocked_apps()
        return blocked_apps, error

    def print_dashboard(include_blocked_apps: bool = True) -> None:
        snapshot = virtual_display_snapshot()
        print(heading("Virtual display"))
        status_parts = [
            f"setup={_format_status_value('configured' if snapshot['configured'] else 'not configured')}",
            f"Sunshine={_format_status_value('active' if snapshot['sunshine_active'] else 'inactive')}",
            f"Sway={_format_status_value('active' if snapshot['sway_active'] else 'inactive')}",
        ]
        runtime_parts = [
            f"input bridge={_format_status_value(snapshot['bridge_state'])}",
            f"audio guard={_format_status_value(snapshot['audio_guard_state'])}",
            f"portal handoff={_format_status_value('active' if snapshot['portal_handoff_active'] else 'idle')}",
        ]
        print(_format_kv("Status:", ", ".join(status_parts)))
        print(_format_kv("Runtime:", ", ".join(runtime_parts)))
        mangohud_status = (
            f"{badge('ENABLED', 'success')} wrapped launches that already use MangoHud follow the client FPS"
            if snapshot["dynamic_mangohud_fps_limit"]
            else f"{badge('DISABLED', 'info')} wrapped launches keep their normal MangoHud FPS behavior"
        )
        print(_format_kv("Dynamic MangoHud FPS limit:", mangohud_status))
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
        if snapshot["controller_detection_error"]:
            print(
                _format_kv(
                    "Host controllers:",
                    f"{badge('UNAVAILABLE', 'error')} {snapshot['controller_detection_error']}",
                )
            )
        elif snapshot["controller_count"]:
            print(
                _format_kv(
                    "Host controllers:",
                    f"{badge('CONFIGURED', 'success')} {snapshot['controller_count']} selected",
                )
            )
            for controller in snapshot["controllers"]:
                suffix = f" {muted('- ' + controller['details'])}" if controller["details"] else ""
                print(f"- {controller['label']} {badge(controller['state'].upper(), _status_level(controller['state']))}{suffix}")
        else:
            print(
                _format_kv(
                    "Host controllers:",
                    f"{badge('AUTO', 'info')} none reserved; client inputs from Moonlight/Sunshine already work automatically",
                )
            )
        if include_blocked_apps:
            blocked_apps, error = get_blocked_apps_report()
            if error:
                print(_format_kv("Blocked Flatpak apps:", f"{badge('WARN', 'warning')} {error}"))
            elif blocked_apps:
                print(_format_kv("Blocked Flatpak apps:", badge("WARN", "warning")))
                for app_name, issue in blocked_apps:
                    print(f"- {app_name}: {issue}")
            else:
                print(_format_kv("Blocked Flatpak apps:", f"{badge('OK', 'success')} none detected"))
        print(_format_kv("Next step:", state_text(snapshot['next_step'], "accent")))

    def print_doctor_report() -> None:
        report = virtual_display_doctor_report()
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

    def reconcile_apps(enable_virtual_display: bool) -> int:
        if enable_virtual_display and virtual_display_is_enabled():
            refresh_managed_files()

        started_here = False
        if not is_server_running("sunshine"):
            start_status = start_virtual_display()
            if start_status != 0:
                return start_status
            started_here = True

        updated, error = reconcile_virtual_display_apps(enable_virtual_display=enable_virtual_display)
        if error:
            print(error)
            return 1

        verb = "Reconciled" if enable_virtual_display else "Restored"
        print(f"{verb} {updated} Sunshine app(s) {'for' if enable_virtual_display else 'from'} virtual display mode.")
        if enable_virtual_display:
            blocked_apps, blocked_error = get_blocked_apps_report()
            if blocked_error:
                print(f"{badge('WARN', 'warning')} unable to inspect Sunshine apps for blocked Flatpak launches. {blocked_error}")
            elif blocked_apps:
                print(_format_kv("Blocked Flatpak apps:", badge("WARN", "warning")))
                for app_name, issue in blocked_apps:
                    print(f"- {app_name}: {issue}")

        if started_here and not enable_virtual_display:
            stop_virtual_display()
        return 0

    def run_enable(interactive: bool) -> int:
        setup_status = setup_virtual_display()
        if setup_status != 0:
            return setup_status
        start_status = start_virtual_display()
        if start_status != 0:
            return start_status
        reconcile_status = reconcile_apps(True)
        if reconcile_status != 0:
            print(f"{badge('WARN', 'warning')} the virtual display stack is running, but Sunshine app sync did not complete.")
            print("Run 'python3 lutristosunshine.py virtualdisplay doctor' or '... virtualdisplay enable' again after fixing the issue.")
            return 0

        if interactive:
            print("")
            print("Client inputs from Moonlight/Sunshine already work automatically.")
            if get_yes_no_input("Reserve any physical host controllers for the virtual display now?", default=False):
                return configure_exclusive_input_devices()
        return 0

    def run_reset() -> int:
        reconcile_status = reconcile_apps(False)
        if reconcile_status != 0:
            return reconcile_status
        remove_status = remove_virtual_display()
        if remove_status == 0:
            print("Sunshine app launches were restored to normal mode and the managed virtual-display setup was removed.")
        return remove_status

    def update_mangohud_fps_limit(enabled: bool) -> int:
        previous = dynamic_mangohud_fps_limit_enabled()
        set_dynamic_mangohud_fps_limit(enabled)
        if previous == enabled:
            state = "enabled" if enabled else "disabled"
            print(f"Dynamic MangoHud FPS limit is already {state} for virtual-display launches.")
        else:
            state = "enabled" if enabled else "disabled"
            print(f"Dynamic MangoHud FPS limit {state} for virtual-display launches.")
        print("This only affects wrapped launches that already use MangoHud.")
        return 0

    def run_hub() -> int:
        while True:
            print("")
            print_dashboard()
            print("")
            print(heading("Actions"))
            print(f"{accent('1.')} Enable virtual display")
            print(f"{accent('2.')} Configure host controllers")
            print(f"{accent('3.')} Run doctor")
            print(f"{accent('4.')} Test controller rumble")
            print(f"{accent('5.')} Show logs")
            print(f"{accent('6.')} Stop virtual display")
            print(f"{accent('7.')} Turn off virtual display and restore Sunshine")
            print(f"{accent('8.')} Toggle dynamic MangoHud FPS limit")
            print(f"{muted('0.')} Exit")
            choice = get_menu_choice(f"{accent('Choose an action: ')}", ["0", "1", "2", "3", "4", "5", "6", "7", "8"])
            if choice == "0":
                return 0
            if choice == "1":
                result = run_enable(interactive=True)
                if result != 0:
                    return result
            elif choice == "2":
                print("")
                print("Client inputs from Moonlight/Sunshine do not need any extra setup.")
                print("Use this only if you want physical controllers connected to the host PC to be reserved for streamed games.")
                result = configure_exclusive_input_devices()
                if result != 0:
                    return result
            elif choice == "3":
                print("")
                print_doctor_report()
            elif choice == "4":
                result = test_bridge_rumble(selector="", mode="auto")
                if result != 0:
                    return result
            elif choice == "5":
                result = virtual_display_logs(80)
                if result != 0:
                    return result
            elif choice == "6":
                result = stop_virtual_display()
                if result != 0:
                    return result
            elif choice == "7":
                confirmed = get_yes_no_input(
                    "Turn off virtual display and restore Sunshine app launches to normal mode? This removes the managed headless setup.",
                    default=False,
                )
                if confirmed:
                    return run_reset()
            elif choice == "8":
                enabling = not dynamic_mangohud_fps_limit_enabled()
                prompt = (
                    "Enable dynamic MangoHud FPS limit for virtual-display launches?"
                    if enabling
                    else "Disable dynamic MangoHud FPS limit for virtual-display launches?"
                )
                if get_yes_no_input(prompt, default=True):
                    result = update_mangohud_fps_limit(enabling)
                    if result != 0:
                        return result

    action = args.virtualdisplay_action
    if action is None:
        return run_hub()
    if action == "enable":
        return run_enable(interactive=True)
    if action == "doctor":
        print_doctor_report()
        return 0
    if action == "controllers":
        print("Client inputs from Moonlight/Sunshine do not need any extra setup.")
        print("Only reserve host controllers here if you want them removed from the desktop and used only inside streamed games.")
        return configure_exclusive_input_devices()
    if action == "status":
        print_dashboard()
        return 0 if virtual_display_snapshot()["configured"] else 1
    if action == "mangohud-fps-limit":
        if args.mangohud_fps_limit_action == "enable":
            return update_mangohud_fps_limit(True)
        if args.mangohud_fps_limit_action == "disable":
            return update_mangohud_fps_limit(False)
        print("Choose 'enable' or 'disable' for 'virtualdisplay mangohud-fps-limit'.")
        return 1
    if action == "stop":
        return stop_virtual_display()
    if action == "rumble":
        return test_bridge_rumble(
            selector=args.controller,
            mode=args.mode,
            duration=args.duration,
            repeat=args.repeat,
            pause=args.pause,
            strong_magnitude=args.strong,
            weak_magnitude=args.weak,
        )
    if action == "reset":
        return run_reset()
    if action == "logs":
        return virtual_display_logs(args.lines)
    print(f"Unknown virtualdisplay action: {action}")
    return 1


def main(argv=None):
    args = parse_args(argv)
    if args.command == "virtualdisplay":
        raise SystemExit(handle_virtualdisplay_command(args))
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
        if not is_server_running(server_name):
            print(f"Error: {server_name.title()} is not running. Please start it and try again.")
            return

        COVERS_PATH = get_covers_path()
        os.makedirs(COVERS_PATH, exist_ok=True)

        # Reuse saved cookies/token first; only prompt if nothing cached is valid.
        session = get_auth_session(allow_prompt=False)
        if not session:
            token = get_auth_token()
            if not token:
                print("Error: Could not obtain valid authentication. Exiting.")
                return

        lutris_command = get_lutris_command()
        heroic_command, _ = get_heroic_command()
        bottles_installed = detect_bottles_installation()
        steam_installed, _ = detect_steam_installation()
        steam_command = get_steam_command() if steam_installed else ""
        faugus_installed = detect_faugus_installation()
        ryubing_installed = detect_ryubing_installation()
        retroarch_installed = detect_retroarch_installation()
        eden_installed = detect_eden_installation()

        if not lutris_command and not heroic_command and not bottles_installed and not steam_command and not faugus_installed and not ryubing_installed and not retroarch_installed and not eden_installed:
            print("No Lutris, Heroic, Bottles, Steam, Faugus, Ryubing, RetroArch, or Eden installation detected.")
            return

        if lutris_command and is_lutris_running():
            print("Error: Lutris is currently running. Please close Lutris and try again.")
            return

        with ThreadPoolExecutor() as executor:
            futures = {}
            if lutris_command:
                futures['Lutris'] = executor.submit(list_lutris_games)
            if heroic_command:
                futures['Heroic'] = executor.submit(list_heroic_games)
            if bottles_installed:
                futures['Bottles'] = executor.submit(list_bottles_games)
            if steam_command:
                futures['Steam'] = executor.submit(list_steam_games)
            if faugus_installed:
                futures['Faugus'] = executor.submit(list_faugus_games)
            if ryubing_installed:
                futures['Ryubing'] = executor.submit(list_ryubing_games)
            if retroarch_installed:
                futures['RetroArch'] = executor.submit(list_retroarch_games)
            if eden_installed:
                futures['Eden'] = executor.submit(list_eden_games)

            all_games = []
            for source, future in futures.items():
                result = future.result()
                if source == 'Lutris':
                    all_games.extend([(game_id, game_name, "Lutris", "Lutris") for game_id, game_name in result])
                elif source == 'Heroic':
                    all_games.extend([(game_id, game_name, "Heroic", runner) for game_id, game_name, _, runner in result])
                elif source == 'Bottles':
                    all_games.extend(result)  # Bottles results are already in the correct format
                elif source == 'Steam':
                    all_games.extend([(game_id, game_name, "Steam", "Steam") for game_id, game_name in result])
                elif source == 'Faugus':
                    all_games.extend(result)
                elif source == 'Ryubing':
                    all_games.extend([(game_id, game_name, "Ryubing", "Ryubing") for game_id, game_name in result])
                elif source == 'RetroArch':
                    all_games.extend([
                        (
                            game_path,
                            game_name,
                            "RetroArch",
                            core_info,
                        )
                        for game_path, game_name, core_info in result
                    ])
                elif source == 'Eden':
                    all_games.extend([(game_id, game_name, "Eden", "Eden") for game_id, game_name in result])

        if not all_games:
            print("No games found in any detected launcher.")
            return

        games_found_message = get_games_found_message(lutris_command, heroic_command, bottles_installed, steam_command, faugus_installed, ryubing_installed, retroarch_installed, eden_installed)
        print(games_found_message)

        existing_apps = get_existing_apps()
        existing_game_names = {app["name"] for app in existing_apps}

        all_games.sort(key=lambda x: x[1])

        for idx, (_, game_name, display_source, source) in enumerate(all_games):
            status = "(already in Sunshine)" if game_name in existing_game_names else ""
            if len(futures) > 1: 
                source_color = SOURCE_COLORS.get(display_source, "")
                source_info = f"{source_color}({display_source}){RESET_COLOR}"
                print(f"{idx + 1}. {game_name} {source_info} {status}")
            else:
                print(f"{idx + 1}. {game_name} {status}")

        if args.all:
            selected_indices = list(range(len(all_games)))
        else:
            selected_indices = get_user_selection([(game_id, game_name) for game_id, game_name, _, _ in all_games])

        selected_games = [all_games[i] for i in selected_indices if all_games[i][1] not in existing_game_names]

        if not selected_games:
            print("No new games to add to Sunshine configuration.")
            return

        valid_selected_games = []
        for game_id, game_name, display_source, source in selected_games:
            if display_source == "RetroArch":
                core_info = source if isinstance(source, dict) else {}
                core_path = (core_info.get("core_path", "") or "").strip()
                core_name = (core_info.get("core_name", "") or "").strip()
                if core_path.upper() == "DETECT" or core_name.upper() == "DETECT" or not core_path:
                    print(f"Error: RetroArch core not set for '{game_name}'. Please associate the game with a core in RetroArch before adding it to Sunshine.")
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
            print("Games added to Sunshine successfully.")
        else:
            print("No new games were added to Sunshine.")

    except (KeyboardInterrupt, EOFError):
        handle_interrupt()

if __name__ == "__main__":
    main()
