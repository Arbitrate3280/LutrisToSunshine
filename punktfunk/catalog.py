"""Punktfunk game-library reads and writes for custom (hand-added) entries.

The unified library (``GET /api/v1/library``) holds plugin-synced titles
plus custom ones; every entry carries a stable id such as ``steam:570`` or
``custom:<row>`` and a ``title``. Hand-added entries are created with
``POST /api/v1/library/custom`` using a ``command`` launch spec -- the host
runs that recipe itself, so the client only ever sends the title id.

Cover art is attached as local ``portrait``/``header`` paths. The host
serves any path inside its art roots (the user's home on Linux by default)
through its art proxy, rewriting it to ``/api/v1/library/art/...`` URLs.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from config.types import DetectHints, SunshineAppReference
from punktfunk.connection import CONNECTION, PunktfunkConnection
from utils.steamgriddb import read_cover_source_url
from utils.utils import normalize_detect_hints


def get_existing_apps(*, connection: PunktfunkConnection = CONNECTION) -> List[SunshineAppReference]:
    data, error = connection.api_request("GET", "/api/v1/library")
    if error:
        print(f"Error retrieving existing apps from {connection.get_server_display_name()} API: {error}")
        return []
    if not isinstance(data, list):
        return []
    return [
        {"name": entry["title"]}
        for entry in data
        if isinstance(entry, dict) and isinstance(entry.get("title"), str)
    ]


def resolve_art(image_path: str) -> Dict[str, str]:
    """Build the ``art`` payload for a cover, preferring its origin URL."""
    if not image_path or not os.path.isfile(image_path):
        return {}
    art_value = read_cover_source_url(image_path) or image_path
    return {"portrait": art_value, "header": art_value}


def submit_command(
    game_name: str,
    command: str,
    image_path: str = "",
    *,
    connection: PunktfunkConnection = CONNECTION,
    detect: Optional[DetectHints] = None,
) -> None:
    payload: Dict[str, Any] = {
        "title": game_name,
        "launch": {"kind": "command", "value": command},
    }
    art = resolve_art(image_path)
    if art:
        payload["art"] = art
    hints = normalize_detect_hints(detect)
    if hints:
        payload["detect"] = hints
    _, error = connection.api_request("POST", "/api/v1/library/custom", json=payload)
    if error:
        print(f"Error adding {game_name} to {connection.get_server_display_name()} via API: {error}")
        return
    print(f"Added {game_name} to {connection.get_server_display_name()}.")
