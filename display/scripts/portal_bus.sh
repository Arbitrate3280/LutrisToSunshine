#!/bin/bash
# Managed by LutrisToSunshine display.
#
# Starts the private session bus the virtual display's portal stack runs on.
#
# The environment given to dbus-daemon is inherited by every service it
# activates, so it is deliberately the *headless session* environment: an
# activated portal implementation must never see the host's Wayland display or
# the host's session bus, or it would happily screencast the host desktop.
# The standard session configuration is kept so D-Bus activation still works
# (services such as flatpak-portal are resolvable on this bus too).
set -euo pipefail

runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
address_file="@PORTAL_BUS_ADDRESS_FILE@"
pid_file="@PORTAL_BUS_PID_FILE@"
config_home="@PORTAL_CONFIG_HOME@"
socket_path="$runtime_dir/@PORTAL_BUS_SOCKET_NAME@"
bus_address="unix:path=$socket_path"
home_value="${HOME:-/home/$(id -un)}"
user_value="${USER:-$(id -un)}"
logname_value="${LOGNAME:-$user_value}"
shell_value="${SHELL:-/bin/sh}"
path_value="${PATH:-/usr/local/bin:/usr/bin:/bin}"
lang_value="${LANG:-C.UTF-8}"

mkdir -p "$(dirname "$address_file")"

if [ -s "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    printf '%s' "$bus_address" > "$address_file"
    exit 0
fi

rm -f "$socket_path" "$pid_file"
if ! /usr/bin/env -i \
    "HOME=$home_value" \
    "USER=$user_value" \
    "LOGNAME=$logname_value" \
    "SHELL=$shell_value" \
    "PATH=$path_value" \
    "LANG=$lang_value" \
    "XDG_RUNTIME_DIR=$runtime_dir" \
    "XDG_CONFIG_HOME=$config_home" \
    "DBUS_SESSION_BUS_ADDRESS=$bus_address" \
    "XDG_CURRENT_DESKTOP=sway" \
    "XDG_SESSION_DESKTOP=sway" \
    dbus-daemon --session --fork --address="$bus_address" --print-pid=1 > "$pid_file"; then
    echo "Unable to start the virtual display portal session bus." >&2
    exit 1
fi

printf '%s' "$bus_address" > "$address_file"
