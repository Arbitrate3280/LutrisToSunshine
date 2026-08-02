"""Flatpak run-command parsing and display-compatibility analysis."""
import shlex
from typing import Any, Dict, List, Optional

from display.constants import (
    FLATPAK_FLAG_OPTIONS,
    FLATPAK_SPAWN_HOST_PREFIX,
    FLATPAK_VALUE_OPTIONS,
    FLATPAK_VALUE_PREFIXES,
)
from display.utils import run_command


def parse_flatpak_run_command(command: str) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return None, f"Unable to parse command: {exc}"

    if not tokens:
        return None, "Missing command."

    outer_prefix: List[str] = []
    index = 0
    if tokens[: len(FLATPAK_SPAWN_HOST_PREFIX)] == FLATPAK_SPAWN_HOST_PREFIX:
        outer_prefix = list(FLATPAK_SPAWN_HOST_PREFIX)
        index = len(FLATPAK_SPAWN_HOST_PREFIX)

    if tokens[index : index + 2] != ["flatpak", "run"]:
        return None, None
    index += 2

    flatpak_options: List[str] = []
    command_name: Optional[str] = None

    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return None, "Unsupported flatpak separator '--'."
        if not token.startswith("-"):
            app_id = token
            return {
                "outer_prefix": outer_prefix,
                "flatpak_options": flatpak_options,
                "command_name": command_name,
                "app_id": app_id,
                "app_args": tokens[index + 1 :],
            }, None
        if token in FLATPAK_FLAG_OPTIONS:
            flatpak_options.append(token)
            index += 1
            continue
        if token in FLATPAK_VALUE_OPTIONS:
            if index + 1 >= len(tokens):
                return None, f"Missing value for flatpak option: {token}"
            value = tokens[index + 1]
            if token == "--command":
                command_name = value
            else:
                flatpak_options.extend([token, value])
            index += 2
            continue

        matched_prefix = next(
            (prefix for prefix in FLATPAK_VALUE_PREFIXES if token.startswith(prefix)),
            None,
        )
        if matched_prefix:
            if matched_prefix == "--command=":
                command_name = token.split("=", 1)[1]
            else:
                flatpak_options.append(token)
            index += 1
            continue

        return None, f"Unsupported flatpak option: {token}"

    return None, "Missing Flatpak application ID."


def resolve_flatpak_default_command(parsed_command: Dict[str, Any]) -> Optional[str]:
    lookup_command = [*parsed_command["outer_prefix"], "flatpak", "info", "--show-metadata", parsed_command["app_id"]]
    result = run_command(lookup_command, check=False)
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        if line.startswith("command="):
            command_name = line.split("=", 1)[1].strip()
            if command_name:
                return command_name
    return None


def analyze_flatpak_command_for_display(command: Optional[str]) -> Optional[str]:
    if not command:
        return None
    parsed_command, error = parse_flatpak_run_command(command)
    if parsed_command is None:
        return error
    return None
