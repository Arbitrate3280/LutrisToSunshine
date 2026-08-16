"""Sunshine app-catalog reads, writes, and display reconciliation."""

from __future__ import annotations

import shlex
from typing import Callable, List, Mapping, Optional, Protocol, Tuple

import requests  # type: ignore[import-untyped]

from display.app_transform import normalize_app_payload, transform_app_for_display
from display.command_wrap import get_app_prep_commands, wrap_command
from display.state import is_enabled as display_enabled
from config.types import SunshineApp, SunshineAppReference, SunshinePrepCommand
from sunshine.connection import CONNECTION, SunshineConnection


class ApiAdder(Protocol):
    def __call__(
        self,
        game_name: str,
        command: str,
        image_path: str,
        *,
        prep_cmd: Optional[List[SunshinePrepCommand]] = None,
        detached: Optional[List[str]] = None,
    ) -> None: ...


def _parse_sunshine_app(value: object, index: int) -> Optional[SunshineApp]:
    """Keep only the typed Sunshine fields accepted by catalog operations."""
    if not isinstance(value, dict):
        return None
    name = value.get("name")
    if not isinstance(name, str) or not name:
        return None
    raw_index = value.get("index")
    app: SunshineApp = {
        "name": name,
        "index": raw_index if isinstance(raw_index, int) and not isinstance(raw_index, bool) else index,
    }
    string_fields = ("name", "cmd", "output", "image-path", "working-dir")
    for key in string_fields:
        raw = value.get(key)
        if isinstance(raw, str):
            app[key] = raw
    bool_fields = ("exclude-global-prep-cmd", "elevated", "auto-detach", "wait-all")
    for key in bool_fields:
        raw = value.get(key)
        if isinstance(raw, bool):
            app[key] = raw
    raw_timeout = value.get("exit-timeout")
    if isinstance(raw_timeout, int) and not isinstance(raw_timeout, bool):
        app["exit-timeout"] = raw_timeout
    raw_prep = value.get("prep-cmd")
    if isinstance(raw_prep, list):
        prep: List[SunshinePrepCommand] = []
        for item in raw_prep:
            if not isinstance(item, dict):
                continue
            command: SunshinePrepCommand = {}
            for key in ("do", "undo"):
                raw_command = item.get(key)
                if isinstance(raw_command, str):
                    command[key] = raw_command
            elevated = item.get("elevated")
            if isinstance(elevated, bool):
                command["elevated"] = elevated
            prep.append(command)
        app["prep-cmd"] = prep
    raw_detached = value.get("detached")
    if isinstance(raw_detached, list):
        app["detached"] = [item for item in raw_detached if isinstance(item, str)]
    return app


def add_game_to_sunshine_api(
    game_name: str,
    cmd: str,
    image_path: str,
    prep_cmd: Optional[List[SunshinePrepCommand]] = None,
    detached: Optional[List[str]] = None,
    *,
    connection: SunshineConnection = CONNECTION,
) -> None:
    payload: SunshineApp = {
        "name": game_name,
        "output": "",
        "cmd": cmd,
        "index": -1,
        "exclude-global-prep-cmd": False,
        "elevated": False,
        "auto-detach": True,
        "wait-all": True,
        "exit-timeout": 5,
        "prep-cmd": prep_cmd or [],
        "detached": detached or [],
        "image-path": image_path,
    }
    _, error = connection.api_request("POST", "/api/apps", json=payload)
    if error:
        print(f"Error adding {game_name} to {connection.get_server_display_name()} via API: {error}")
    else:
        print(f"Added {game_name} to {connection.get_server_display_name()}.")


def full_apps(
    allow_prompt: bool = True,
    *,
    connection: SunshineConnection = CONNECTION,
) -> Tuple[List[SunshineApp], Optional[str]]:
    session: Optional[requests.Session] = None
    token: Optional[str] = None
    if not allow_prompt:
        session = connection.get_auth_session(allow_prompt=False)
        token = connection.get_cached_auth_token()
        if session is None and token is None:
            return [], "No cached Sunshine authentication is available."
    data, error = connection.api_request(
        "GET",
        "/api/apps",
        session=session,
        token=token,
    )
    if error:
        return [], error
    apps: List[SunshineApp] = []
    raw_apps = data.get("apps", []) if isinstance(data, Mapping) else []
    for index, app in enumerate(raw_apps if isinstance(raw_apps, list) else []):
        parsed = _parse_sunshine_app(app, index)
        if parsed is not None:
            apps.append(parsed)
    return apps, None


def get_existing_apps(*, connection: SunshineConnection = CONNECTION) -> List[SunshineAppReference]:
    apps, error = full_apps(connection=connection)
    if error:
        print(f"Error retrieving existing apps from {connection.get_server_display_name()} API: {error}")
        return []
    return [{"name": app["name"]} for app in apps if isinstance(app.get("name"), str)]


def submit_command(
    game_name: str,
    command: str,
    image_path: str,
    *,
    connection: SunshineConnection = CONNECTION,
    display_enabled_fn: Callable[[], bool] = display_enabled,
    wrap_fn: Callable[[Optional[str], str], Optional[str]] = wrap_command,
    prep_fn: Callable[[bool], List[SunshinePrepCommand]] = get_app_prep_commands,
    api_add_fn: ApiAdder = add_game_to_sunshine_api,
) -> None:
    enabled = connection.server_name == "sunshine" and display_enabled_fn()
    if connection.installation_type == "flatpak":
        try:
            command = shlex.join(["flatpak-spawn", "--host", *shlex.split(command)])
        except ValueError:
            command = f"flatpak-spawn --host {command}"
    if enabled:
        command = wrap_fn(command, "cmd") or command
    prep_cmd = prep_fn(enabled) if enabled else []
    api_add_fn(game_name, command, image_path, prep_cmd=prep_cmd, detached=[])


def get_display_blocked_apps() -> Tuple[List[Tuple[str, str]], Optional[str]]:
    return [], None


def reconcile_display_apps(
    enable_display: bool,
    *,
    connection: SunshineConnection = CONNECTION,
    display_enabled_fn: Callable[[], bool] = display_enabled,
) -> Tuple[int, Optional[str]]:
    apps, error = full_apps(connection=connection)
    if error:
        return 0, error
    display_is_enabled = display_enabled_fn()
    updated_count = 0
    for app in apps:
        transformed = transform_app_for_display(app, enable_display, display_is_enabled)
        if transformed == normalize_app_payload(app):
            continue
        _, update_error = connection.api_request("POST", "/api/apps", json=transformed)
        if update_error:
            return updated_count, f"Error updating {app.get('name', 'Unknown App')}: {update_error}"
        updated_count += 1
    return updated_count, None
