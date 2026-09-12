"""Pure display-mode normalization functions (no side effects)."""
from typing import Any, TypedDict

from display.constants import FALLBACK_FPS, FALLBACK_HEIGHT, FALLBACK_WIDTH, REFRESH_RATE_SYNC_MODES
from display.utils import safe_string


class DisplayMode(TypedDict):
    width: int
    height: int
    refresh: float


def normalized_refresh_rate_sync_mode(value: Any) -> str:
    normalized = safe_string(value).lower()
    if normalized in REFRESH_RATE_SYNC_MODES:
        return normalized
    return "client"


def refresh_rate_sync_mode_summary(mode: str) -> str:
    if mode == "exact":
        return "client's exact refresh rate (fractional, e.g. 59.94/119.88)"
    if mode == "custom":
        return "custom fixed display mode"
    return "Moonlight's requested FPS (integer, e.g. 60/90/120)"


def normalized_custom_display_mode(value: Any) -> DisplayMode:
    default_mode = {
        "width": FALLBACK_WIDTH,
        "height": FALLBACK_HEIGHT,
        "refresh": float(FALLBACK_FPS),
    }
    if not isinstance(value, dict):
        return default_mode

    try:
        width = int(value.get("width", default_mode["width"]))
    except (TypeError, ValueError):
        width = default_mode["width"]
    try:
        height = int(value.get("height", default_mode["height"]))
    except (TypeError, ValueError):
        height = default_mode["height"]
    try:
        refresh = float(value.get("refresh", default_mode["refresh"]))
    except (TypeError, ValueError):
        refresh = default_mode["refresh"]

    if width <= 0:
        width = default_mode["width"]
    if height <= 0:
        height = default_mode["height"]
    if refresh <= 0:
        refresh = default_mode["refresh"]

    return {
        "width": width,
        "height": height,
        "refresh": refresh,
    }


def format_refresh_rate_hz(refresh_value: Any) -> str:
    try:
        refresh = float(refresh_value)
    except (TypeError, ValueError):
        return ""
    if refresh <= 0:
        return ""
    if refresh > 1000:
        refresh /= 1000.0
    nearest = round(refresh)
    if abs(refresh - nearest) < 0.01:
        return str(int(nearest))
    return f"{refresh:.2f}"


def custom_display_mode_string(value: Any) -> str:
    mode = normalized_custom_display_mode(value)
    refresh_hz = format_refresh_rate_hz(mode["refresh"])
    if not refresh_hz:
        return ""
    return f"{mode['width']}x{mode['height']} @ {refresh_hz} Hz"
