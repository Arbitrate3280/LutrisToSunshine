import os
import argparse
import sys
from typing import Dict, Optional, Tuple

from config.constants import DEFAULT_IMAGE, LAUNCHER_NAMES, SOURCE_COLORS, RESET_COLOR
from sunshine import catalog
from sunshine.connection import CONNECTION
from utils.utils import (
    handle_interrupt,
    get_games_found_message,
    normalize_game_name_for_dedup,
)
from utils.input import get_menu_choice, get_user_input, get_yes_no_input, get_user_selection, get_required_input, CUSTOM_COMMAND_SELECTION
from utils.terminal import accent, heading, muted
from config.settings import load_settings, update_setting
from sunshine.installation import resolve_installation, save_install_choice
from utils.steamgriddb import manage_api_key, download_image_from_steamgriddb
from launchers.lutris import is_lutris_running
from launchers import intake

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

    subparsers.add_parser(
        "ignore",
        help="Toggle which detected game sources are scanned for games.",
    ).set_defaults(command="ignore")

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
        help="Choose whether the virtual display uses Moonlight's requested integer FPS, the client's exact fractional refresh rate, or a custom fixed mode.",
    )
    refresh_rate_parser.add_argument(
        "mode",
        choices=["client", "exact", "custom"],
        help="Use 'client' for Moonlight's requested integer FPS (60/90/120), 'exact' for the client's exact fractional refresh rate (59.94/119.88), or 'custom' for a fixed display mode.",
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
    capture_parser = display_subparsers.add_parser(
        "capture-method",
        help=(
            "Choose how Sunshine captures the virtual display: 'wlr' (direct wlroots screencopy) "
            "or 'portal' (private xdg-desktop-portal + PipeWire session)."
        ),
    )
    capture_parser.add_argument(
        "method",
        choices=["wlr", "portal"],
        help="Capture backend Sunshine uses for the virtual display.",
    )

    return parser.parse_args(argv)


def handle_display_command(args) -> int:
    from display import hub

    action = args.display_action
    if action is None:
        return hub.run_hub()
    if action == "enable":
        return hub.enable()
    if action == "start":
        return hub.start()
    if action == "restart":
        return hub.restart()
    if action == "doctor":
        hub.print_doctor_report()
        return 0
    if action == "status":
        return hub.status()
    if action == "mangohud-fps-limit":
        if args.mangohud_fps_limit_action == "enable":
            return hub.set_fps_limit(True)
        if args.mangohud_fps_limit_action == "disable":
            return hub.set_fps_limit(False)
        print("Choose 'enable' or 'disable' for 'display mangohud-fps-limit'.")
        return 1
    if action == "refresh-rate-mode":
        if args.mode == "custom":
            supplied = [getattr(args, "width", None), getattr(args, "height", None), getattr(args, "refresh", None)]
            if any(v is not None for v in supplied) and not all(v is not None for v in supplied):
                print("Provide --width, --height, and --refresh together when choosing custom mode.")
                return 1
            if all(v is not None for v in supplied):
                if args.width <= 0 or args.height <= 0 or args.refresh <= 0:
                    print("Custom width, height, and refresh must all be greater than 0.")
                    return 1
                hub.set_custom_display_mode(args.width, args.height, args.refresh)
        return hub.set_sync_mode(args.mode)
    if action == "stop":
        from display.manager import stop_display
        return stop_display()
    if action == "reset":
        return hub.reset()
    if action == "logs":
        from display.manager import display_logs
        return display_logs(args.lines)
    if action == "display-gpu":
        from display.gpu import configure_gpu
        from display.manager import refresh_managed_files, restart_display
        return configure_gpu(
            refresh_managed_files_fn=refresh_managed_files,
            restart_fn=restart_display,
        )
    if action == "renderer-mode":
        from display.manager import set_renderer_mode
        set_renderer_mode(args.mode)
        return 0
    if action == "capture-method":
        from display import portal
        from display.manager import set_capture_method
        previous, _ = set_capture_method(args.method)
        if args.method == portal.CAPTURE_PORTAL:
            print("Sunshine capture pinned to 'portal' (private xdg-desktop-portal session).")
        else:
            print("Sunshine capture restored to 'wlr'; the portal files were removed.")
        if previous != args.method:
            print("Restart the virtual display for the change to take effect: 'display restart'.")
        return 0
    print(f"Unknown display action: {action}")
    return 1



def add_custom_command_flow() -> None:
    """Prompt for a name + command and add it to the active server."""
    print("")
    print(heading("Add custom command"))
    name = get_required_input("Entry name: ", "Name cannot be empty.")
    command = get_required_input("Command to run: ", "Command cannot be empty.")

    existing = {normalize_game_name_for_dedup(app["name"]) for app in catalog.get_existing_apps()}
    if normalize_game_name_for_dedup(name) in existing:
        print(f"Warning: an entry named '{name}' already exists in {CONNECTION.get_server_display_name()}.")

    image_path = DEFAULT_IMAGE
    if get_yes_no_input(f"Download a cover from SteamGridDB for '{name}'? (y/n): "):
        api_key = manage_api_key()
        if api_key:
            try:
                image_path = download_image_from_steamgriddb(name, api_key)
            except Exception as e:
                print(f"Error downloading image for {name}: {e}")
                image_path = DEFAULT_IMAGE

    catalog.submit_command(name, command, image_path)


def prompt_install_choice(detected_types) -> Optional[str]:
    """Prompt the user to pick one of multiple detected Sunshine installs."""
    print("Multiple Sunshine installs detected. Which one should be used?")
    for index, install_type in enumerate(detected_types, start=1):
        print(f"{accent(index)}. {install_type}")
    choice = get_menu_choice(
        f"{accent('Choose an installation: ')}",
        [str(index) for index in range(1, len(detected_types) + 1)],
    )
    return detected_types[int(choice) - 1]


def ignore_sources_flow() -> int:
    """Interactive toggle menu for ignored game sources; saves on every toggle."""
    detected = intake.detect_launchers()
    available = [name for name in LAUNCHER_NAMES if detected.get(name)]
    if not available:
        print("No game sources detected.")
        return 0

    def toggle_validator(value: str) -> str:
        stripped = value.strip()
        if stripped == "":
            return ""
        if stripped.isdigit() and 1 <= int(stripped) <= len(available):
            return stripped
        raise ValueError()

    while True:
        ignored = load_settings().get("ignored_sources", [])
        ignored = [e for e in ignored if isinstance(e, str)] if isinstance(ignored, list) else []
        ignored_folded = {name.casefold() for name in ignored}

        print("")
        print(heading("Game sources"))
        for index, name in enumerate(available, start=1):
            marker = f" {muted('(ignored)')}" if name.casefold() in ignored_folded else ""
            print(f"{accent(f'{index}.')} {name}{marker}")

        choice = get_user_input(
            f"{accent('Number to toggle, Enter to exit: ')}",
            toggle_validator,
            "Invalid selection.",
        )
        if choice == "":
            return 0
        name = available[int(choice) - 1]
        if name.casefold() in ignored_folded:
            update_setting("ignored_sources", [e for e in ignored if e.casefold() != name.casefold()])
        else:
            update_setting("ignored_sources", [*ignored, name])


def main(argv=None):
    def prompt_server_connection() -> Tuple[str, int]:
        current_host, current_port = CONNECTION.get_api_connection()
        print(f"{CONNECTION.get_server_display_name()} web UI address: {CONNECTION.get_api_url()}")
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
            current_url = CONNECTION.get_api_url()
            prompt = (
                f"Authentication failed using {CONNECTION.get_server_display_name()} at {current_url}. "
                "Configure a different web UI host or port and try again?"
            )
            if not get_yes_no_input(prompt, default=True):
                return False

            host, port = prompt_server_connection()
            CONNECTION.set_api_connection(host=host, port=port)

            if CONNECTION.ensure_authenticated(allow_prompt=True):
                CONNECTION.save_api_connection(host, port, server_name=server_name)
                print(f"Saved {CONNECTION.get_server_display_name()} web UI address: {CONNECTION.get_api_url()}")
                return True

    args = parse_args(argv)
    if args.command == "display":
        raise SystemExit(handle_display_command(args))
    if args.command == "ignore":
        raise SystemExit(ignore_sources_flow())
    try:
        facts = resolve_installation()
        sunshine_install_type = facts.install_type
        if facts.installed and sunshine_install_type is None:
            if sys.stdin.isatty():
                sunshine_install_type = prompt_install_choice(facts.detected_types)
                save_install_choice(sunshine_install_type)
            else:
                types = ", ".join(facts.detected_types)
                print(
                    f"Multiple Sunshine installs detected: {types}. "
                    "Run 'python3 lutristosunshine.py' once interactively to pick which one to use."
                )
                return 1

        apollo_installed = CONNECTION.detect_apollo_installation()
        if not facts.installed and not apollo_installed:
            print("Error: No Sunshine or Apollo installation detected.")
            return

        running_servers = CONNECTION.get_running_servers()
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
            if not facts.installed or sunshine_install_type is None:
                print("Error: Sunshine is not installed.")
                return
            CONNECTION.set_installation_type(sunshine_install_type)
        else:
            if not apollo_installed:
                print("Error: Apollo is not installed.")
                return
            CONNECTION.set_installation_type("native")

        CONNECTION.set_server_name(server_name)
        if args.sunshine_host or args.sunshine_port is not None:
            CONNECTION.set_api_connection(
                host=args.sunshine_host or None,
                port=args.sunshine_port,
            )
        if not CONNECTION.is_server_running(server_name):
            print(f"Error: {server_name.title()} is not running. Please start it and try again.")
            return

        COVERS_PATH = CONNECTION.get_covers_path()
        os.makedirs(COVERS_PATH, exist_ok=True)

        authenticated = CONNECTION.ensure_authenticated(allow_prompt=True)
        if not authenticated:
            authenticated = configure_connection_and_retry_auth(server_name)

        if not authenticated:
            print("Error: Could not obtain valid authentication. Exiting.")
            return

        if args.sunshine_host or args.sunshine_port is not None:
            CONNECTION.save_api_connection(
                args.sunshine_host or None,
                args.sunshine_port,
                server_name=server_name,
            )

        detected_launchers = intake.detect_launchers()
        detected_launchers, skipped_sources = intake.apply_ignores(
            detected_launchers,
            load_settings().get("ignored_sources", []),
        )
        if skipped_sources:
            print(muted(f"Ignored: {', '.join(skipped_sources)} (manage with 'lutristosunshine ignore')"))

        if not any(detected_launchers.values()):
            if skipped_sources:
                print("All detected sources are ignored. Run 'lutristosunshine ignore' to include some.")
            else:
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

        all_games = intake.collect_games(detected_launchers)

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

        existing_apps = catalog.get_existing_apps()
        existing_game_names_normalized = {
            normalize_game_name_for_dedup(app["name"]) for app in existing_apps
        }

        all_games.sort(key=lambda game: game.game_name)

        _game_name_cache: Dict[str, str] = {
            g.game_name: normalize_game_name_for_dedup(g.game_name)
            for g in all_games
        }

        for idx, game in enumerate(all_games):
            game_name = game.game_name
            status = (
                f"(already in {CONNECTION.get_server_display_name()})"
                if _game_name_cache[game_name] in existing_game_names_normalized
                else ""
            )
            if sum(detected_launchers.values()) > 1:
                source_color = SOURCE_COLORS.get(game.display_source, "")
                source_info = f"{source_color}({game.display_source}){RESET_COLOR}"
                print(f"{idx + 1}. {game_name} {source_info} {status}")
            else:
                print(f"{idx + 1}. {game_name} {status}")

        if args.all:
            selected_indices = list(range(len(all_games)))
        else:
            selection = get_user_selection([(game.game_id, game.game_name) for game in all_games])
            if selection == CUSTOM_COMMAND_SELECTION:
                add_custom_command_flow()
                return
            selected_indices = selection

        selection_result = intake.select_games(
            all_games,
            selected_indices,
            existing_game_names_normalized,
        )
        selected_games = selection_result.games

        for skipped_game, retained_game in selection_result.skipped_duplicates:
            skipped_name = skipped_game.game_name
            skipped_source = skipped_game.display_source
            retained_source = retained_game.display_source
            print(
                f"Skipping duplicate '{skipped_name}' from {skipped_source}; "
                f"using {retained_source}."
            )

        for invalid_game, reason in selection_result.invalid_games:
            print(
                f"Error: {reason} for '{invalid_game.game_name}'. Please associate the game with a core in RetroArch before adding it to {CONNECTION.get_server_display_name()}."
            )

        if not selected_games:
            print("No games ready to add. Please resolve the reported issues and try again.")
            return

        download_images = args.cover or get_yes_no_input("Do you want to download images from SteamGridDB? (y/n): ")
        api_key = manage_api_key() if download_images else None

        games_added = intake.submit_games(
            selected_games,
            download_images=download_images,
            api_key=api_key,
            default_image=DEFAULT_IMAGE,
            download_image_fn=download_image_from_steamgriddb,
            submit_game_fn=catalog.submit_command,
        )

        if games_added:
            print(f"Games added to {CONNECTION.get_server_display_name()} successfully.")
        else:
            print(f"No new games were added to {CONNECTION.get_server_display_name()}.")

    except (KeyboardInterrupt, EOFError):
        handle_interrupt()

if __name__ == "__main__":
    raise SystemExit(main())
