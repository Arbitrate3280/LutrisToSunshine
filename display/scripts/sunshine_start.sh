#!/bin/bash
set -euo pipefail

display_file="@WAYLAND_DISPLAY_FILE@"
if [ ! -s "$display_file" ]; then
    echo "Headless sway display is not ready." >&2
    exit 1
fi

runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
dbus_value="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$runtime_dir/bus}"
wayland_value="$(cat "$display_file")"

unset DISPLAY
export XDG_RUNTIME_DIR="$runtime_dir"
export DBUS_SESSION_BUS_ADDRESS="$dbus_value"
export WAYLAND_DISPLAY="$wayland_value"
export SWAYSOCK="@SWAY_SOCKET@"
export XDG_SESSION_TYPE=wayland
export XDG_CURRENT_DESKTOP=sway
export XDG_SESSION_DESKTOP=sway

sunshine_cmd=@SUNSHINE_COMMAND@
if [[ "$sunshine_cmd" == *"flatpak run"* ]]; then
    sunshine_cmd="${sunshine_cmd/flatpak run/flatpak run --env=WAYLAND_DISPLAY=$wayland_value --env=SWAYSOCK=@SWAY_SOCKET@ --filesystem=$runtime_dir/}"
fi
exec /bin/sh -lc "$sunshine_cmd"
