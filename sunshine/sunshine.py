"""Compatibility façade for the Sunshine connection and app catalog."""

from __future__ import annotations

from typing import List, Mapping, Optional, Tuple

import requests  # type: ignore[import-untyped]

from display.command_wrap import get_app_prep_commands, wrap_command
from display.state import is_enabled as display_enabled
from config.types import SunshineAppReference, SunshinePrepCommand
from sunshine.catalog import (
    add_game_to_sunshine_api as _catalog_add_game_to_sunshine_api,
    get_display_blocked_apps as _catalog_get_display_blocked_apps,
    get_existing_apps as _catalog_get_existing_apps,
    reconcile_display_apps as _catalog_reconcile_display_apps,
    submit_command as _catalog_submit_command,
)
from sunshine.connection import CONNECTION, SunshineConnection

def set_installation_type(type_: str) -> None:
    CONNECTION.set_installation_type(type_)


def set_server_name(name: str) -> None:
    CONNECTION.set_server_name(name)


def set_api_connection(host: Optional[str] = None, port: Optional[object] = None) -> None:
    CONNECTION.set_api_connection(host, port)


def get_api_connection(server_name: Optional[str] = None) -> Tuple[str, int]:
    return CONNECTION.get_api_connection(server_name)


def get_api_url(server_name: Optional[str] = None) -> str:
    return CONNECTION.get_api_url(server_name)


def save_api_connection(host: Optional[str], port: Optional[object], server_name: Optional[str] = None) -> None:
    CONNECTION.save_api_connection(host, port, server_name)


def get_covers_path() -> str:
    return CONNECTION.get_covers_path()


def get_api_key_path() -> str:
    return CONNECTION.get_api_key_path()


def get_credentials_path() -> str:
    return CONNECTION.get_credentials_path()


def detect_apollo_installation() -> bool:
    return CONNECTION.detect_apollo_installation()


def get_running_servers() -> List[str]:
    return CONNECTION.get_running_servers()


def is_server_running(name: Optional[str] = None) -> bool:
    return CONNECTION.is_server_running(name)


def is_sunshine_running() -> bool:
    return is_server_running()


def get_server_display_name() -> str:
    return CONNECTION.get_server_display_name()


def get_sunshine_credentials() -> Tuple[str, str]:
    return CONNECTION.get_sunshine_credentials()


def get_auth_session(allow_prompt: bool = True) -> Optional[requests.Session]:
    return CONNECTION.get_auth_session(allow_prompt)


def ensure_authenticated(allow_prompt: bool = True) -> bool:
    return CONNECTION.ensure_authenticated(allow_prompt)


def get_auth_token() -> Optional[str]:
    return CONNECTION.get_auth_token()


def sunshine_api_request(
    method: str,
    endpoint: str,
    *,
    session: Optional[requests.Session] = None,
    token: Optional[str] = None,
    headers: Optional[Mapping[str, str]] = None,
    json: Optional[Mapping[str, object]] = None,
) -> Tuple[Optional[Mapping[str, object]], Optional[str]]:
    return CONNECTION.api_request(
        method,
        endpoint,
        session=session,
        token=token,
        headers=headers,
        json=json,
    )


def add_game_to_sunshine_api(
    game_name: str,
    cmd: str,
    image_path: str,
    prep_cmd: Optional[List[SunshinePrepCommand]] = None,
    detached: Optional[List[str]] = None,
) -> None:
    _catalog_add_game_to_sunshine_api(
        game_name,
        cmd,
        image_path,
        prep_cmd=prep_cmd,
        detached=detached,
        connection=CONNECTION,
    )


def submit_command(game_name: str, cmd: str, image_path: str) -> None:
    _catalog_submit_command(
        game_name,
        cmd,
        image_path,
        connection=CONNECTION,
        display_enabled_fn=display_enabled,
        wrap_fn=wrap_command,
        prep_fn=get_app_prep_commands,
        api_add_fn=add_game_to_sunshine_api,
    )


def add_custom_command_to_sunshine(game_name: str, command: str, image_path: str) -> None:
    submit_command(game_name, command, image_path)


def get_existing_apps() -> List[SunshineAppReference]:
    return _catalog_get_existing_apps(connection=CONNECTION)


def get_display_blocked_apps() -> Tuple[List[Tuple[str, str]], Optional[str]]:
    return _catalog_get_display_blocked_apps()


def reconcile_display_apps(enable_display: bool) -> Tuple[int, Optional[str]]:
    return _catalog_reconcile_display_apps(
        enable_display,
        connection=CONNECTION,
        display_enabled_fn=display_enabled,
    )
