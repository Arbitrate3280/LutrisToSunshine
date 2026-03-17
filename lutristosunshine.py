import sys
import os
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

from config.constants import DEFAULT_IMAGE, SOURCE_COLORS, RESET_COLOR
from sunshine.sunshine import get_covers_path, set_installation_type, set_server_name
from utils.utils import handle_interrupt, get_games_found_message
from utils.input import get_yes_no_input, get_user_selection
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
    is_enabled as virtual_display_is_enabled,
    refresh_managed_files,
    remove_virtual_display,
    setup_virtual_display,
    start_virtual_display,
    stop_virtual_display,
    virtual_display_logs,
    virtual_display_status,
)
from virtualdisplay.rumble import test_bridge_rumble

def parse_args():
    parser = argparse.ArgumentParser(description="Sync launcher games to Sunshine.")
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
        help="Manage the isolated headless virtual display stack.",
    )
    virtualdisplay_parser.set_defaults(command="virtualdisplay")
    virtualdisplay_subparsers = virtualdisplay_parser.add_subparsers(dest="virtualdisplay_action", required=True)
    virtualdisplay_subparsers.add_parser("setup", help="Install virtual display files and configuration.")
    virtualdisplay_subparsers.add_parser("status", help="Show the current virtual display status.")
    virtualdisplay_subparsers.add_parser("start", help="Start the managed headless Sway and Sunshine services.")
    virtualdisplay_subparsers.add_parser("stop", help="Stop the managed headless Sway and Sunshine services.")
    virtualdisplay_subparsers.add_parser("inputs", help="Choose which host controllers are routed exclusively to the virtual display.")
    rumble_parser = virtualdisplay_subparsers.add_parser(
        "test-rumble",
        help="Send rumble signals to a currently bridged controller from the virtual-device side.",
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
    virtualdisplay_subparsers.add_parser("sync-apps", help="Reconcile existing Sunshine apps with the current virtual display state.")
    virtualdisplay_subparsers.add_parser("disable", help="Disable virtual display mode and restore Sunshine app entries.")
    virtualdisplay_subparsers.add_parser("remove", help="Remove the managed virtual display setup.")
    logs_parser = virtualdisplay_subparsers.add_parser("logs", help="Show recent logs for the managed services.")
    logs_parser.add_argument(
        "--lines",
        type=int,
        default=80,
        help="Number of log lines to show.",
    )

    return parser.parse_args()


def handle_virtualdisplay_command(args) -> int:
    def report_blocked_apps() -> None:
        blocked_apps, error = get_virtual_display_blocked_apps()
        if error:
            print(f"Warning: unable to inspect Sunshine apps for blocked Flatpak launches. {error}")
            return
        if not blocked_apps:
            return

        print("Blocked Flatpak apps:")
        for app_name, issue in blocked_apps:
            print(f"- {app_name}: {issue}")

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
            report_blocked_apps()

        if started_here and not enable_virtual_display:
            stop_virtual_display()
        return 0

    action = args.virtualdisplay_action
    if action == "setup":
        setup_status = setup_virtual_display()
        if setup_status != 0:
            return setup_status
        return reconcile_apps(True)
    if action == "status":
        status = virtual_display_status()
        if status == 0:
            report_blocked_apps()
        return status
    if action == "start":
        return start_virtual_display()
    if action == "stop":
        return stop_virtual_display()
    if action == "inputs":
        return configure_exclusive_input_devices()
    if action == "test-rumble":
        return test_bridge_rumble(
            selector=args.controller,
            mode=args.mode,
            duration=args.duration,
            repeat=args.repeat,
            pause=args.pause,
            strong_magnitude=args.strong,
            weak_magnitude=args.weak,
        )
    if action == "sync-apps":
        if not virtual_display_is_enabled():
            print("Virtual display is not enabled.")
            return 1
        return reconcile_apps(virtual_display_is_enabled())
    if action == "disable":
        reconcile_status = reconcile_apps(False)
        if reconcile_status != 0:
            return reconcile_status
        return remove_virtual_display()
    if action == "remove":
        reconcile_status = reconcile_apps(False)
        if reconcile_status != 0:
            return reconcile_status
        return remove_virtual_display()
    if action == "logs":
        return virtual_display_logs(args.lines)
    print(f"Unknown virtualdisplay action: {action}")
    return 1


def main():
    args = parse_args()
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
