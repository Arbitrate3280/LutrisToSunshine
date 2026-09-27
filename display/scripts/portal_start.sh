#!/bin/bash
# Managed by LutrisToSunshine display.
#
# Supervises the portal stack that serves Sunshine's capture for the headless
# session: xdg-desktop-portal (the frontend Sunshine talks to) plus the
# wlroots backend that actually reads HEADLESS-1.  Both run on the private
# session bus only, so the host session's own portal stack stays untouched.
set -euo pipefail

display_file="@WAYLAND_DISPLAY_FILE@"
address_file="@PORTAL_BUS_ADDRESS_FILE@"
ready_file="@PORTAL_READY_FILE@"
config_home="@PORTAL_CONFIG_HOME@"
portals_dir="@PORTAL_PORTALS_DIR@"
log_file="@PORTAL_LOG_FILE@"
portal_name="@PORTAL_DESKTOP_NAME@"
backend_name="@PORTAL_WLR_BACKEND_NAME@"
runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

resolve_portal_binary() {
    local name="$1"
    local candidate
    if command -v "$name" >/dev/null 2>&1; then
        command -v "$name"
        return 0
    fi
    for candidate in "/usr/libexec/$name" "/usr/lib/$name" "/usr/lib64/$name" \
        "/usr/local/libexec/$name" "/usr/local/lib/$name"; do
        if [ -x "$candidate" ]; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    return 1
}

for _ in $(seq 1 100); do
    if [ -s "$display_file" ] && [ -s "$address_file" ]; then
        break
    fi
    sleep 0.1
done

if [ ! -s "$display_file" ]; then
    echo "Headless sway display is not ready." >&2
    exit 1
fi
if [ ! -s "$address_file" ]; then
    echo "Virtual display portal session bus is not ready." >&2
    exit 1
fi

portal_binary="$(resolve_portal_binary xdg-desktop-portal)" || {
    echo "xdg-desktop-portal is not installed." >&2
    exit 1
}
backend_binary="$(resolve_portal_binary xdg-desktop-portal-wlr)" || {
    echo "xdg-desktop-portal-wlr is not installed." >&2
    exit 1
}

wayland_value="$(cat "$display_file")"
# The portal daemons must load the managed routing/backend config instead of
# the user's own ~/.config, must resolve the backend against this session, and
# must see only the wlroots implementation (see wlr.portal_content).
export XDG_RUNTIME_DIR="$runtime_dir"
export DBUS_SESSION_BUS_ADDRESS="$(cat "$address_file")"
export XDG_CONFIG_HOME="$config_home"
export XDG_DESKTOP_PORTAL_DIR="$portals_dir"
export WAYLAND_DISPLAY="$wayland_value"
export SWAYSOCK="@SWAY_SOCKET@"
export XDG_SESSION_TYPE=wayland
export XDG_CURRENT_DESKTOP=sway
export XDG_SESSION_DESKTOP=sway
export DESKTOP_SESSION=sway
unset DISPLAY

mkdir -p "$(dirname "$log_file")"
rm -f "$ready_file"
: >> "$log_file"

portal_pid=""
backend_pid=""

stop_portal_daemons() {
    local pid
    for pid in "$backend_pid" "$portal_pid"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" >/dev/null 2>&1 || true
            wait "$pid" >/dev/null 2>&1 || true
        fi
    done
    backend_pid=""
    portal_pid=""
}

cleanup() {
    local exit_code=$?
    stop_portal_daemons
    rm -f "$ready_file"
    exit "$exit_code"
}
trap cleanup EXIT INT TERM HUP

name_owned() {
    local output
    output="$(gdbus call --address "$DBUS_SESSION_BUS_ADDRESS" \
        --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
        --method org.freedesktop.DBus.NameHasOwner "$1" 2>/dev/null || true)"
    [[ "$output" == *true* ]]
}

wait_for_name() {
    local name="$1"
    local pid="$2"
    for _ in $(seq 1 150); do
        if name_owned "$name"; then
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            return 1
        fi
        sleep 0.1
    done
    return 1
}

screencast_available() {
    local output
    output="$(gdbus call --address "$DBUS_SESSION_BUS_ADDRESS" \
        --dest "$portal_name" --object-path /org/freedesktop/portal/desktop \
        --method org.freedesktop.DBus.Properties.Get \
        org.freedesktop.portal.ScreenCast AvailableSourceTypes 2>/dev/null || true)"
    [[ "$output" == *uint32* ]]
}

# Bring up one generation of the stack: backend first, then the frontend.
#
# The backend goes first because xdg-desktop-portal resolves implementations when
# it starts; if the backend is not on the bus yet it asks D-Bus to activate one,
# which spawns a second instance from the system service file (with this bus's
# environment, so no compositor) and then marks ScreenCast unusable.
start_portal_generation() {
    "$backend_binary" >>"$log_file" 2>&1 &
    backend_pid=$!
    if ! wait_for_name "$backend_name" "$backend_pid"; then
        return 1
    fi

    "$portal_binary" >>"$log_file" 2>&1 &
    portal_pid=$!

    for _ in $(seq 1 150); do
        if name_owned "$portal_name" && screencast_available; then
            printf 'ready\n' > "$ready_file"
            return 0
        fi
        if ! kill -0 "$portal_pid" 2>/dev/null || ! kill -0 "$backend_pid" 2>/dev/null; then
            return 1
        fi
        sleep 0.1
    done
    return 1
}

# Sunshine creates and tears down a capture session per encoder it tests while
# starting, and a wlroots backend that dies on one of those sessions would
# otherwise stay dead for the rest of the session (the frontend caches the
# missing implementation), failing every later client.  Restart the pair instead.
# Keep supervising for as long as the display runs: a stack that gave up used to
# stay dead until the unit was restarted, which turned into a failed stream.
generation=0
backoff=1
while true; do
    generation=$((generation + 1))
    if start_portal_generation; then
        if [ "$generation" -gt 1 ]; then
            echo "Virtual display portal stack is ready again (attempt $generation)." >&2
        fi
        backoff=1
        # wait -n returns as soon as either daemon exits.
        wait -n "$portal_pid" "$backend_pid" || true
        stop_portal_daemons
        rm -f "$ready_file"
    else
        echo "Virtual display portal stack did not become ready (attempt $generation); see $log_file." >&2
        stop_portal_daemons
        backoff=$((backoff * 2))
        if [ "$backoff" -gt 30 ]; then
            backoff=30
        fi
    fi
    sleep "$backoff"
done
