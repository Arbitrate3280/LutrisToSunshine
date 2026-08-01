#!/bin/bash
set -euo pipefail

runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
display_file="@WAYLAND_DISPLAY_FILE@"
before_file="$(mktemp)"
after_file="$(mktemp)"
path_value="${PATH:-/usr/local/bin:/usr/bin:/bin}"
lang_value="${LANG:-C.UTF-8}"
home_value="${HOME:-/home/$(id -un)}"
user_value="${USER:-$(id -un)}"
logname_value="${LOGNAME:-$user_value}"
shell_value="${SHELL:-/bin/sh}"
dbus_value="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$runtime_dir/bus}"

@GPU_DETECTION_BLOCK@

cleanup() {
    rm -f "$before_file" "$after_file"
}
trap cleanup EXIT

rm -f "$display_file" "@SWAY_SOCKET@"
ls "$runtime_dir"/wayland-* 2>/dev/null | grep -v '\.lock$' | sort > "$before_file" || true

unset DISPLAY
unset WAYLAND_DISPLAY
unset DESKTOP_SESSION
unset SESSION_MANAGER
unset XDG_SESSION_DESKTOP
unset XDG_ACTIVATION_TOKEN
unset DESKTOP_STARTUP_ID
unset KDE_FULL_SESSION
unset KDE_SESSION_UID
unset KDE_SESSION_VERSION

    sway_cmd=(
        /usr/bin/env -i
        "HOME=$home_value"
        "USER=$user_value"
        "LOGNAME=$logname_value"
        "SHELL=$shell_value"
        "PATH=$path_value"
        "LANG=$lang_value"
        "XDG_RUNTIME_DIR=$runtime_dir"
        "DBUS_SESSION_BUS_ADDRESS=$dbus_value"
        "XDG_SESSION_TYPE=wayland"
        "XDG_CURRENT_DESKTOP=sway"
        "XDG_SESSION_DESKTOP=sway"
        "SWAYSOCK=@SWAY_SOCKET@"
        "WLR_BACKENDS=headless,libinput"
        "LIBSEAT_BACKEND=noop"
    )
@GPU_ENV_VARS_BLOCK@@RENDERER_BLOCK@
    sway_cmd+=(/usr/bin/sway --config "@SWAY_CONFIG@")
    "${sway_cmd[@]}" &
sway_pid=$!

for _ in $(seq 1 100); do
    if ! kill -0 "$sway_pid" 2>/dev/null; then
        break
    fi
    ls "$runtime_dir"/wayland-* 2>/dev/null | grep -v '\.lock$' | sort > "$after_file" || true
    new_socket="$(comm -13 "$before_file" "$after_file" | head -n1)"
    if [ -n "$new_socket" ]; then
        basename "$new_socket" > "$display_file"
        break
    fi
    sleep 0.1
done

if [ ! -s "$display_file" ]; then
    kill "$sway_pid" 2>/dev/null || true
    wait "$sway_pid" 2>/dev/null || true
    echo "Unable to determine the headless sway Wayland socket." >&2
    exit 1
fi

wait "$sway_pid"
