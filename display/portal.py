"""Portal capture policy for the virtual display.

Sunshine's portal backend (``capture = portal``) talks to
``org.freedesktop.portal.Desktop`` over ``DBUS_SESSION_BUS_ADDRESS`` and
receives the frames from PipeWire.  Pointing that at the *host* session bus
captures the host desktop and needs an approval click, so this module owns the
alternative: a private session bus, inside the headless session, running its
own ``xdg-desktop-portal`` plus the wlroots backend.  Sunshine then captures
``HEADLESS-1`` through the portal while the host session keeps its own portal
stack untouched.

The private bus is deliberately *only* for Sunshine's capture path: launched
games keep the host session bus (see ``display/scripts/headless_prep.sh``), so
Flatpak games keep working against the host portals.  Input isolation is not
affected -- Sunshine's virtual input devices still go through ``/dev/uinput``
regardless of the capture backend.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from display.constants import (
    CAPTURE_METHODS,
    HEADLESS_OUTPUT_NAME,
    PORTAL_BUS_SOCKET_NAME,
    PORTAL_DESKTOP_NAME,
    PORTAL_PORTALS_DIRNAME,
    PORTAL_WLR_BACKEND_NAME,
    XDG_RUNTIME_DIR,
)
from display.state import DisplayState
from display.sunshine_conf import read_key_value, remove_key, set_key_value
from display.utils import run_command, safe_string

CAPTURE_WLR = "wlr"
CAPTURE_PORTAL = "portal"
CAPTURE_METHOD_SUMMARIES = {
    CAPTURE_WLR: "wlroots screencopy (capture inside the headless compositor)",
    CAPTURE_PORTAL: "xdg-desktop-portal + PipeWire (private portal session)",
}

_PORTAL_BINARIES = ("xdg-desktop-portal", "xdg-desktop-portal-wlr")
# Distros install portal daemons into libexec, which is usually not on PATH.
_PORTAL_BINARY_DIRS = (
    "/usr/libexec",
    "/usr/lib",
    "/usr/lib64",
    "/usr/local/libexec",
    "/usr/local/lib",
)
# Sunshine keys this module owns while the virtual display is enabled.
_MANAGED_CONF_KEYS = ("capture", "output_name")
_PORTAL_CAPTURE_VALUE = CAPTURE_PORTAL
# Values this module writes itself; never remembered as a user preference.
_PINNED_CONF_VALUES = frozenset({CAPTURE_WLR, CAPTURE_PORTAL, HEADLESS_OUTPUT_NAME})


def portal_capture_enabled(state: DisplayState) -> bool:
    return safe_string(getattr(state, "capture_method", "")) == CAPTURE_PORTAL


def capture_method_label(state: DisplayState) -> str:
    if portal_capture_enabled(state):
        return "[PORTAL] Sunshine captures the headless output through a private portal session"
    return "[WLR] Sunshine captures the headless output directly (wlroots screencopy)"


def capture_method_summary(state: DisplayState) -> str:
    return CAPTURE_METHOD_SUMMARIES.get(
        safe_string(getattr(state, "capture_method", "")),
        CAPTURE_METHOD_SUMMARIES[CAPTURE_WLR],
    )


def bus_address(state: DisplayState) -> str:
    """Return the private session bus address Sunshine should connect to."""
    address_file = safe_string(state.paths.portal_bus_address_file)
    if address_file:
        try:
            content = Path(address_file).read_text(encoding="utf-8").strip()
        except OSError:
            content = ""
        if content:
            return content
    return f"unix:path={XDG_RUNTIME_DIR / PORTAL_BUS_SOCKET_NAME}"


def host_bus_address() -> str:
    """Return the host session bus address to hand to launched games.

    Captured at render time so the generated scripts never inherit the private
    bus from Sunshine's environment.
    """
    explicit = safe_string(os.environ.get("DBUS_SESSION_BUS_ADDRESS"))
    if explicit:
        return explicit
    return f"unix:path={host_runtime_dir()}/bus"


def host_runtime_dir() -> str:
    """Return the real runtime dir, which launched games must keep using.

    Portal mode gives the Sunshine process a private ``XDG_RUNTIME_DIR`` (see
    :func:`runtime_bus_socket`); games and prep commands must not inherit it.
    """
    return safe_string(os.environ.get("XDG_RUNTIME_DIR")) or f"/run/user/{os.getuid()}"


def runtime_bus_socket(address: str) -> Optional[str]:
    """Return the socket path of a ``unix:path=`` bus address."""
    prefix = "unix:path="
    if not address.startswith(prefix):
        return None
    return address[len(prefix):]


def portal_binary(name: str, which_fn: Callable[[str], Optional[str]] = shutil.which) -> Optional[str]:
    """Resolve a portal daemon, which usually lives outside ``PATH``."""
    found = which_fn(name)
    if found:
        return found
    for directory in _PORTAL_BINARY_DIRS:
        candidate = Path(directory) / name
        if candidate.is_file():
            return str(candidate)
    return None


def missing_dependencies(which_fn: Callable[[str], Optional[str]] = shutil.which) -> List[str]:
    """Return portal-only dependencies that are not installed."""
    missing: List[str] = []
    for name in ("dbus-daemon", "gdbus"):
        if which_fn(name) is None:
            missing.append(name)
    for name in _PORTAL_BINARIES:
        if portal_binary(name, which_fn) is None:
            missing.append(name)
    return missing


def routing_conf_content() -> str:
    """Content of ``xdg-desktop-portal``'s routing config for this session.

    Only ScreenCast/Screenshot are pinned to the wlroots backend; the global
    ``default`` is left to the system so unrelated interfaces keep working if
    something in the headless session asks for them.
    """
    return (
        "# Managed by LutrisToSunshine display.\n"
        "[preferred]\n"
        f"org.freedesktop.impl.portal.ScreenCast={CAPTURE_WLR}\n"
        f"org.freedesktop.impl.portal.Screenshot={CAPTURE_WLR}\n"
    )


def wlr_config_content() -> str:
    """Content of the ``xdg-desktop-portal-wlr`` config.

    ``chooser_type=none`` plus an explicit ``output_name`` is what makes the
    screencast headless: the backend serves the configured output without any
    selection UI.  No ``max_fps`` is set on purpose -- the backend then follows
    the output's refresh rate, which is already managed by the display-sync
    settings.
    """
    return (
        "# Managed by LutrisToSunshine display.\n"
        "[screencast]\n"
        f"output_name={HEADLESS_OUTPUT_NAME}\n"
        "chooser_type=none\n"
    )


def wlr_portal_content() -> str:
    """Content of the private ``wlr.portal`` implementation file.

    The virtual display points ``XDG_DESKTOP_PORTAL_DIR`` at a directory that
    holds only this file, so ``xdg-desktop-portal`` can never resolve a request
    to another implementation.  That matters beyond taste: D-Bus-activated
    implementations inherit the *bus daemon's* environment, so a KDE/GTK
    backend activated here could reach the host session and screencast the host
    desktop instead of the headless output.
    """
    interfaces = ";".join(
        (
            "org.freedesktop.impl.portal.Screenshot",
            "org.freedesktop.impl.portal.ScreenCast",
        )
    )
    return (
        "# Managed by LutrisToSunshine display.\n"
        "[portal]\n"
        f"DBusName={PORTAL_WLR_BACKEND_NAME}\n"
        f"Interfaces={interfaces};\n"
        "UseIn=wlroots;sway;Wayfire;river;phosh;Hyprland;\n"
    )


def managed_files(state: DisplayState) -> Dict[Path, str]:
    """Return the portal config files, or nothing when portal mode is off."""
    if not portal_capture_enabled(state):
        return {}
    return {
        Path(state.paths.portal_routing_conf): routing_conf_content(),
        Path(state.paths.portal_wlr_config): wlr_config_content(),
        Path(state.paths.portal_wlr_portal): wlr_portal_content(),
    }


def remove_files(state: DisplayState) -> None:
    """Unlink every managed portal artifact and prune the config home.

    Called whenever portal capture is off, so switching back to ``wlr`` does
    not leave a stale portal stack on disk.
    """
    for path in (
        state.paths.portal_bus_script,
        state.paths.portal_start_script,
        state.paths.portal_routing_conf,
        state.paths.portal_wlr_config,
        state.paths.portal_wlr_portal,
    ):
        if not path:
            continue
        try:
            Path(path).unlink()
        except OSError:
            pass
    config_home = safe_string(state.paths.portal_config_home)
    if not config_home:
        return
    for directory in (
        Path(config_home) / "xdg-desktop-portal",
        Path(config_home) / "xdg-desktop-portal-wlr",
        Path(config_home) / PORTAL_PORTALS_DIRNAME,
        Path(config_home),
    ):
        try:
            directory.rmdir()
        except OSError:
            pass


def managed_capture_settings(state: DisplayState) -> Dict[str, str]:
    """Sunshine settings the managed virtual display needs for this backend.

    Sunshine picks its capture method automatically and prefers KMS over both
    the wlroots protocol and the portal, so the managed display has to pin the
    method it was set up for.  A leftover value from the other backend (or from
    the user changing it in Sunshine's own UI) otherwise sends the capture to
    the host desktop, which still streams, just not the virtual display.
    """
    capture = CAPTURE_PORTAL if portal_capture_enabled(state) else CAPTURE_WLR
    return {"capture": capture, "output_name": HEADLESS_OUTPUT_NAME}


def capture_config_drift(
    state: DisplayState, conf_path: Optional[Path] = None
) -> List[Tuple[str, str, str]]:
    """Return ``(key, expected, actual)`` for managed keys that do not match."""
    if conf_path is None:
        sunshine_conf = safe_string(state.paths.sunshine_conf)
        if not sunshine_conf:
            return []
        conf_path = Path(sunshine_conf)
    drift: List[Tuple[str, str, str]] = []
    for key, expected in managed_capture_settings(state).items():
        actual = safe_string(read_key_value(conf_path, key).get("value"))
        if actual != expected:
            drift.append((key, expected, actual))
    return drift


def _remembered_override(conf_path: Path) -> Dict[str, Any]:
    """Values the keys held before this module pinned them, if worth restoring."""
    remembered: Dict[str, Any] = {}
    for key in _MANAGED_CONF_KEYS:
        current = read_key_value(conf_path, key)
        if safe_string(current.get("value")) in _PINNED_CONF_VALUES:
            continue
        remembered[key] = current
    return remembered


def sync_capture_config(state: DisplayState) -> DisplayState:
    """Pin Sunshine to the managed display's capture backend and output.

    The values the user had before are remembered in
    ``state.sunshine_capture_override`` so ``clear_capture_config`` can put them
    back when the virtual display is removed or switched off.
    """
    sunshine_conf = safe_string(state.paths.sunshine_conf)
    if not sunshine_conf:
        return state
    conf_path = Path(sunshine_conf)
    expected = managed_capture_settings(state)
    drift = capture_config_drift(state, conf_path)

    if state.sunshine_capture_override is None:
        state.sunshine_capture_override = _remembered_override(conf_path)
    for key, value in expected.items():
        set_key_value(conf_path, key, value)

    if drift:
        changed = ", ".join(f"{key} = {actual or '(unset)'}" for key, _, actual in drift)
        pinned = ", ".join(f"{key} = {value}" for key, value in expected.items())
        print(f"Sunshine's capture config ({changed}) did not match the virtual display; pinned {pinned}.")
    return state


def clear_capture_config(state: DisplayState) -> DisplayState:
    """Remove the pinned keys and put back whatever the user had before."""
    sunshine_conf = safe_string(state.paths.sunshine_conf)
    if not sunshine_conf:
        return state
    conf_path = Path(sunshine_conf)
    previous = state.sunshine_capture_override if isinstance(state.sunshine_capture_override, dict) else {}
    pinned_values = set(_PINNED_CONF_VALUES)

    for key in _MANAGED_CONF_KEYS:
        recorded = previous.get(key)
        if isinstance(recorded, dict) and safe_string(recorded.get("value")):
            # The user had their own value before the display was set up.
            set_key_value(conf_path, key, safe_string(recorded.get("value")))
            continue
        current = read_key_value(conf_path, key)
        if current.get("present") and safe_string(current.get("value")) in pinned_values:
            remove_key(conf_path, key)

    state.sunshine_capture_override = None
    return state


def _address_socket_path(address: str) -> Optional[Path]:
    socket = runtime_bus_socket(address)
    return Path(socket) if socket else None


def portal_status(
    state: DisplayState,
    *,
    runner: Callable[..., object] = run_command,
) -> Dict[str, object]:
    """Report whether the private bus and both portal names are up."""
    status: Dict[str, object] = {
        "bus_active": False,
        "names_active": False,
        "error": "",
    }
    address = bus_address(state)
    socket_path = _address_socket_path(address)
    if socket_path is None or not socket_path.exists():
        status["error"] = f"Portal session bus socket is missing ({address})."
        return status
    status["bus_active"] = True

    for name in (PORTAL_DESKTOP_NAME, PORTAL_WLR_BACKEND_NAME):
        result = runner(
            [
                "gdbus",
                "call",
                "--address",
                address,
                "--dest",
                "org.freedesktop.DBus",
                "--object-path",
                "/org/freedesktop/DBus",
                "--method",
                "org.freedesktop.DBus.NameHasOwner",
                name,
            ],
            check=False,
        )
        stdout = safe_string(getattr(result, "stdout", ""))
        if getattr(result, "returncode", 1) != 0 or "true" not in stdout:
            status["error"] = f"{name} is not owned on the private session bus."
            return status
    status["names_active"] = True
    return status


__all__ = [
    "CAPTURE_METHODS",
    "CAPTURE_PORTAL",
    "CAPTURE_WLR",
    "bus_address",
    "capture_method_label",
    "capture_method_summary",
    "clear_capture_config",
    "host_bus_address",
    "host_runtime_dir",
    "managed_files",
    "missing_dependencies",
    "portal_binary",
    "portal_capture_enabled",
    "portal_status",
    "remove_files",
    "routing_conf_content",
    "runtime_bus_socket",
    "sync_capture_config",
    "wlr_config_content",
    "wlr_portal_content",
]
