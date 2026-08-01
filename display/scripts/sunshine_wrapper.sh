#!/bin/bash
set -euo pipefail

audio_create_script="@AUDIO_CREATE_SCRIPT@"
audio_cleanup_script="@AUDIO_CLEANUP_SCRIPT@"
kwin_input_isolation_script="@KWIN_INPUT_ISOLATION_SCRIPT@"
sway_start_script="@SWAY_START_SCRIPT@"
sunshine_start_script="@SUNSHINE_START_SCRIPT@"
display_file="@WAYLAND_DISPLAY_FILE@"
kwin_input_isolation_status_file="@KWIN_INPUT_ISOLATION_STATUS_FILE@"
sway_socket="@SWAY_SOCKET@"
runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
dbus_value="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$runtime_dir/bus}"
pulse_server_value="${PULSE_SERVER:-}"
pulse_clientconfig_value="${PULSE_CLIENTCONFIG:-}"
kwin_input_isolation_pid=""
sway_pid=""
sunshine_pid=""
sunshine_status=0

export XDG_RUNTIME_DIR="$runtime_dir"
export DBUS_SESSION_BUS_ADDRESS="$dbus_value"
if [ -n "$pulse_server_value" ]; then
    export PULSE_SERVER="$pulse_server_value"
else
    unset PULSE_SERVER
fi
if [ -n "$pulse_clientconfig_value" ]; then
    export PULSE_CLIENTCONFIG="$pulse_clientconfig_value"
else
    unset PULSE_CLIENTCONFIG
fi



stop_child() {
    local pid="${1:-}"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill -- "-$pid" >/dev/null 2>&1 || kill "$pid" >/dev/null 2>&1 || true
        wait "$pid" >/dev/null 2>&1 || true
    fi
}

cleanup() {
    local exit_code=$?
    stop_child "$sunshine_pid"
    stop_child "$kwin_input_isolation_pid"
    stop_child "$sway_pid"
    rm -f "$kwin_input_isolation_status_file" "$display_file"
    "$audio_cleanup_script" >/dev/null 2>&1 || true
    exit "$exit_code"
}

trap cleanup EXIT INT TERM HUP

"$audio_create_script"
setsid "$sway_start_script" &
sway_pid=$!

for _ in $(seq 1 100); do
    if [ -s "$display_file" ] && [ -S "$sway_socket" ]; then
        break
    fi
    if ! kill -0 "$sway_pid" 2>/dev/null; then
        break
    fi
    sleep 0.1
done

if [ ! -s "$display_file" ] || [ ! -S "$sway_socket" ]; then
    echo "Headless sway did not become ready." >&2
    exit 1
fi


setsid python3 "$kwin_input_isolation_script" &
kwin_input_isolation_pid=$!

setsid "$sunshine_start_script" &
sunshine_pid=$!
wait "$sunshine_pid"
sunshine_status=$?
exit "$sunshine_status"
