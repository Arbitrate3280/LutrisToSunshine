"""Sunshine app ↔ display transform logic.

All reconciliation of Sunshine app payloads with the virtual display
lifecycle lives here. sunshine/sunshine.py calls one interface:
transform_app_for_display.
"""
import shlex
from typing import List, Tuple

from config.types import SunshineApp, SunshinePrepCommand

from display.command_wrap import (
    HEADLESS_PREP_PREFIX,
    get_app_prep_commands,
    get_wrapped_command_exit_timeout,
    get_wrapped_command_origin,
    is_headless_prep_wrapped,
    is_wrapped_command,
    routes_through_managed_scripts,
    unwrap_command,
    unwrap_headless_prep_command,
    wrap_command,
    wrap_headless_prep_command,
)


def normalize_single_prep_command(command: str, enable_display: bool) -> str:
    if not command:
        return ""

    if enable_display:
        if is_headless_prep_wrapped(command):
            return command
        if command.startswith(HEADLESS_PREP_PREFIX):
            raw_command = command[len(HEADLESS_PREP_PREFIX):].lstrip()
            if not raw_command:
                return ""
            return wrap_headless_prep_command(raw_command) or command
        return command

    if is_headless_prep_wrapped(command):
        unwrapped_command = unwrap_headless_prep_command(command)
        if not unwrapped_command:
            return ""
        return f"{HEADLESS_PREP_PREFIX}{unwrapped_command}"

    return command


def normalize_prep_cmd(
    app: SunshineApp,
    enable_display: bool,
    display_enabled: bool,
) -> List[SunshinePrepCommand]:
    prep_cmd = app.get("prep-cmd") or []
    filtered: List[SunshinePrepCommand] = []
    for command in prep_cmd:
        if not isinstance(command, dict):
            continue
        do_cmd = command.get("do", "")
        undo_cmd = command.get("undo", "")
        # Tool-owned prep commands are re-added below in canonical form; drop
        # any earlier copy, however it was invoked (raw or host-escaped).
        if routes_through_managed_scripts(do_cmd) or routes_through_managed_scripts(undo_cmd):
            continue
        normalized: SunshinePrepCommand = dict(command)
        normalized["do"] = normalize_single_prep_command(do_cmd, enable_display)
        normalized["undo"] = normalize_single_prep_command(undo_cmd, enable_display)
        filtered.append(normalized)

    if enable_display:
        return get_app_prep_commands(display_enabled) + filtered
    return filtered


def normalize_app_payload(app: SunshineApp) -> SunshineApp:
    payload: SunshineApp = dict(app)
    payload["cmd"] = payload.get("cmd") or ""
    payload["output"] = payload.get("output") or ""
    payload["detached"] = payload.get("detached") or []
    payload["prep-cmd"] = payload.get("prep-cmd") or []
    payload["exclude-global-prep-cmd"] = payload.get("exclude-global-prep-cmd", False)
    payload["elevated"] = payload.get("elevated", False)
    payload["auto-detach"] = payload.get("auto-detach", True)
    payload["wait-all"] = payload.get("wait-all", True)
    payload["exit-timeout"] = payload.get("exit-timeout", 5)
    payload["image-path"] = payload.get("image-path") or ""
    return payload


def dedupe_commands(commands: List[str]) -> List[str]:
    unique_commands = []
    seen = set()
    for command in commands:
        if not command or command in seen:
            continue
        unique_commands.append(command)
        seen.add(command)
    return unique_commands


def unwrap_with_origin(command: str, field_origin: str) -> Tuple[str, str]:
    if not command:
        return "", field_origin
    if not is_wrapped_command(command):
        return command, field_origin

    origin = get_wrapped_command_origin(command) or field_origin
    # Legacy wrappers only encoded the command, so preserve the field placement.
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = []
    if len(parts) < 3:
        origin = field_origin

    unwrapped = unwrap_command(command) or ""
    return unwrapped, origin


def select_display_primary_command(app: SunshineApp) -> Tuple[str, str, List[str], int]:
    cmd, cmd_origin = unwrap_with_origin(app.get("cmd") or "", "cmd")
    if cmd:
        timeout = get_wrapped_command_exit_timeout(app.get("cmd") or "", app.get("exit-timeout", 5))
        remaining = []
        for command in app.get("detached") or []:
            unwrapped, _ = unwrap_with_origin(command, "detached")
            if unwrapped:
                remaining.append(unwrapped)
        return cmd, cmd_origin, remaining, timeout

    detached_commands: List[str] = []
    detached_timeout = app.get("exit-timeout", 5)
    for command in app.get("detached") or []:
        unwrapped, _ = unwrap_with_origin(command, "detached")
        if not unwrapped:
            continue
        detached_commands.append(unwrapped)
        if len(detached_commands) == 1:
            detached_timeout = get_wrapped_command_exit_timeout(command, app.get("exit-timeout", 5))

    if not detached_commands:
        return "", "cmd", [], app.get("exit-timeout", 5)
    return detached_commands[0], "detached", detached_commands[1:], detached_timeout


def enable_display_launch(app: SunshineApp) -> Tuple[str, List[str]]:
    primary_command, origin, detached_commands, exit_timeout = select_display_primary_command(app)
    wrapped_primary = wrap_command(primary_command, origin, exit_timeout) or ""
    return wrapped_primary, dedupe_commands(detached_commands)


def disable_display_launch(app: SunshineApp) -> Tuple[str, List[str]]:
    restored_cmd = ""
    restored_detached: List[str] = []

    cmd, origin = unwrap_with_origin(app.get("cmd") or "", "cmd")
    if cmd:
        if origin == "detached":
            restored_detached.append(cmd)
        else:
            restored_cmd = cmd

    for command in app.get("detached") or []:
        unwrapped, detached_origin = unwrap_with_origin(command, "detached")
        if not unwrapped:
            continue
        if detached_origin == "cmd" and not restored_cmd:
            restored_cmd = unwrapped
        else:
            restored_detached.append(unwrapped)

    return restored_cmd, dedupe_commands(restored_detached)


def transform_app_for_display(
    app: SunshineApp,
    enable_display: bool,
    display_enabled: bool,
) -> SunshineApp:
    updated = normalize_app_payload(app)
    if enable_display:
        updated["cmd"], updated["detached"] = enable_display_launch(updated)
    else:
        updated["cmd"], updated["detached"] = disable_display_launch(updated)
    updated["prep-cmd"] = normalize_prep_cmd(updated, enable_display, display_enabled)
    return updated
