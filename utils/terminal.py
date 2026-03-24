import os
import sys
from typing import Mapping, Optional, TextIO


RESET = "\033[0m"
STYLE_CODES = {
    "heading": "1;36",
    "accent": "36",
    "success": "1;32",
    "warning": "1;33",
    "error": "1;31",
    "info": "1;34",
    "muted": "2;37",
}


def supports_color(stream: Optional[TextIO] = None, environ: Optional[Mapping[str, str]] = None) -> bool:
    stream = stream or sys.stdout
    environ = environ or os.environ
    if environ.get("NO_COLOR"):
        return False
    if environ.get("TERM", "").lower() == "dumb":
        return False
    isatty = getattr(stream, "isatty", None)
    if callable(isatty):
        return bool(isatty())
    return False


def colorize(text: str, style: str, *, enabled: Optional[bool] = None) -> str:
    if enabled is None:
        enabled = supports_color()
    if not enabled:
        return text
    code = STYLE_CODES.get(style, "")
    if not code:
        return text
    return f"\033[{code}m{text}{RESET}"


def heading(text: str, *, enabled: Optional[bool] = None) -> str:
    return colorize(text, "heading", enabled=enabled)


def muted(text: str, *, enabled: Optional[bool] = None) -> str:
    return colorize(text, "muted", enabled=enabled)


def accent(text: str, *, enabled: Optional[bool] = None) -> str:
    return colorize(text, "accent", enabled=enabled)


def state_text(text: str, level: str, *, enabled: Optional[bool] = None) -> str:
    return colorize(text, level, enabled=enabled)


def badge(label: str, level: str, *, enabled: Optional[bool] = None) -> str:
    return state_text(f"[{label}]", level, enabled=enabled)
