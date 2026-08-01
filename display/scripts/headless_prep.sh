#!/bin/bash
set -euo pipefail

encoded_command="${1:-}"

if [ -z "$encoded_command" ]; then
    echo "Missing encoded prep command." >&2
    exit 1
fi

if [ -f /.flatpak-info ]; then
    sunshine_env=()
    for var in SUNSHINE_CLIENT_FPS SUNSHINE_CLIENT_WIDTH SUNSHINE_CLIENT_HEIGHT SUNSHINE_CLIENT_HMAX SUNSHINE_CLIENT_VMAX; do
        if [ -n "${!var:-}" ]; then
            sunshine_env+=("--env=$var=${!var}")
        fi
    done
    exec flatpak-spawn --host "${sunshine_env[@]}" "@HEADLESS_PREP_SCRIPT@" "$@"
fi

if [ ! -S "@SWAY_SOCKET@" ]; then
    echo "Headless sway IPC socket is not ready." >&2
    exit 1
fi

if [ ! -s "@WAYLAND_DISPLAY_FILE@" ]; then
    echo "Headless sway display is not ready." >&2
    exit 1
fi

unset DISPLAY
unset DESKTOP_SESSION
unset SESSION_MANAGER
unset XDG_SESSION_DESKTOP
unset XDG_ACTIVATION_TOKEN
unset DESKTOP_STARTUP_ID
unset KDE_FULL_SESSION
unset KDE_SESSION_UID
unset KDE_SESSION_VERSION

decoded_command="$(printf '%s' "$encoded_command" | base64 --decode)"
display_value=":1"
wayland_value="$(cat "@WAYLAND_DISPLAY_FILE@")"
runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
path_value="${PATH:-/usr/local/bin:/usr/bin:/bin}"
lang_value="${LANG:-C.UTF-8}"
home_value="${HOME:-/home/$(id -un)}"
user_value="${USER:-$(id -un)}"
logname_value="${LOGNAME:-$user_value}"
shell_value="${SHELL:-/bin/sh}"
dbus_value="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$runtime_dir/bus}"
pulse_server_value="${PULSE_SERVER:-}"
pulse_clientconfig_value="${PULSE_CLIENTCONFIG:-}"
portal_lock_file="@PORTAL_LOCK_FILE@"
launch_log_file="@LAST_LAUNCH_LOG_FILE@"
portal_timeout="@FLATPAK_PORTAL_SWITCH_TIMEOUT@"
spawn_timeout="@FLATPAK_PORTAL_SPAWN_TIMEOUT@"

mkdir -p "$(dirname "$launch_log_file")"
touch "$launch_log_file"
chmod 600 "$launch_log_file" >/dev/null 2>&1 || true

log_debug() {
    printf '[%s] prep %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$launch_log_file"
}

is_flatpak_command() {
    python3 - "$1" <<'PY'
import shlex
import sys

FLATPAK_SPAWN_HOST_PREFIX = ["flatpak-spawn", "--host"]

command = sys.argv[1]
try:
    tokens = shlex.split(command)
except ValueError:
    raise SystemExit(1)

index = 0
if tokens[: len(FLATPAK_SPAWN_HOST_PREFIX)] == FLATPAK_SPAWN_HOST_PREFIX:
    index = len(FLATPAK_SPAWN_HOST_PREFIX)

raise SystemExit(0 if tokens[index : index + 2] == ["flatpak", "run"] else 1)
PY
}
@AUDIO_INJECT_ENV@

run_headless_command() {
    local command_to_run="$1"
    log_debug "running prep command: $command_to_run"
    
    @GPU_DETECTION_BLOCK@

    local -a launch_command
    launch_command=(/usr/bin/env -i
        "HOME=$home_value"
        "USER=$user_value"
        "LOGNAME=$logname_value"
        "SHELL=$shell_value"
        "PATH=$path_value"
        "LANG=$lang_value"
        "XDG_RUNTIME_DIR=$runtime_dir"
        "DBUS_SESSION_BUS_ADDRESS=$dbus_value"
        "DISPLAY=$display_value"
        "WAYLAND_DISPLAY=$wayland_value"
        "SWAYSOCK=@SWAY_SOCKET@"
        "XDG_SESSION_TYPE=wayland"
        "XDG_CURRENT_DESKTOP=sway"
        "XDG_SESSION_DESKTOP=sway"
        "DESKTOP_SESSION=sway"
        "LTS_VDISPLAY=1"
        "PULSE_SINK=@AUDIO_SINK@"
        "PULSE_PROP=@AUDIO_STREAM_PULSE_PROP@"
        "PIPEWIRE_PROPS=@AUDIO_STREAM_PIPEWIRE_PROPS@"
        "PULSE_SERVER=$pulse_server_value"
        "PULSE_CLIENTCONFIG=$pulse_clientconfig_value"
    )
    @GPU_LAUNCH_ENV_VARS_BLOCK@
    launch_command+=(/bin/sh -lc "$command_to_run")
    "${launch_command[@]}" >>"$launch_log_file" 2>&1
}

snapshot_host_env() {
    : > "$host_env_file"
    systemctl --user show-environment > "$systemd_env_dump"
    while IFS= read -r key; do
        grep -m1 "^${key}=" "$systemd_env_dump" >> "$host_env_file" || true
    done <<'EOF'
@PORTAL_ENV_KEYS_BLOCK@
EOF
}

import_portal_env() {
    local -a names
    names=("$@")
    systemctl --user import-environment "${names[@]}" >/dev/null
    if command -v dbus-update-activation-environment >/dev/null 2>&1; then
        dbus-update-activation-environment --systemd "${names[@]}" >/dev/null 2>&1 || true
    fi
}

restart_flatpak_portal() {
    systemctl --user restart @FLATPAK_PORTAL_UNIT@ >/dev/null
}

apply_headless_portal_env() {
    export DISPLAY="$display_value"
    export WAYLAND_DISPLAY="$wayland_value"
    export SWAYSOCK="@SWAY_SOCKET@"
    export DESKTOP_SESSION="sway"
    export XDG_CURRENT_DESKTOP="sway"
    export XDG_SESSION_DESKTOP="sway"
    export XDG_SESSION_TYPE="wayland"
@CLEAR_ACTIVATION_ENV_SCRIPT@

    import_portal_env @PORTAL_ENV_KEYS@
    restart_flatpak_portal
}

restore_host_portal_env() {
    local -a restore_names
    local -a absent_names
    local key
    local line
    local value

    restore_names=()
    absent_names=()

    while IFS= read -r key; do
        line="$(grep -m1 "^${key}=" "$host_env_file" || true)"
        if [ -n "$line" ]; then
            value="${line#*=}"
            export "${key}=${value}"
        else
            export "${key}="
            absent_names+=("$key")
        fi
        restore_names+=("$key")
    done <<'EOF'
@PORTAL_ENV_KEYS_BLOCK@
EOF

    import_portal_env "${restore_names[@]}"
    if [ "${#absent_names[@]}" -gt 0 ]; then
        systemctl --user unset-environment "${absent_names[@]}" >/dev/null 2>&1 || true
    fi
    restart_flatpak_portal
}

start_portal_monitor() {
    monitor_log="$(mktemp "@PROFILE_ROOT@/portal-monitor-XXXXXX.log")"
    stdbuf -oL -eL gdbus monitor --session --dest org.freedesktop.portal.Flatpak --object-path /org/freedesktop/portal/Flatpak > "$monitor_log" 2>/dev/null &
    monitor_pid="$!"
    sleep 0.2
}

wait_for_spawn() {
    local checks
    checks=$((spawn_timeout * 10))
    for _ in $(seq 1 "$checks"); do
        if grep -q "SpawnStarted" "$monitor_log" 2>/dev/null; then
            return 0
        fi
        sleep 0.1
    done
    return 1
}

cleanup_monitor() {
    if [ -n "${monitor_pid:-}" ]; then
        kill "$monitor_pid" >/dev/null 2>&1 || true
        wait "$monitor_pid" >/dev/null 2>&1 || true
        monitor_pid=""
    fi
    if [ -n "${monitor_log:-}" ]; then
        rm -f "$monitor_log"
        monitor_log=""
    fi
}

host_env_file=""
systemd_env_dump=""
monitor_log=""
monitor_pid=""
portal_switched=0

cleanup() {
    local exit_code=$?
    if [ "${portal_switched:-0}" -eq 1 ]; then
        restore_host_portal_env >/dev/null 2>&1 || true
    fi
    cleanup_monitor
    rm -f "$host_env_file" "$systemd_env_dump"
    exit "$exit_code"
}

trap cleanup EXIT INT TERM HUP
decoded_command="$(inject_flatpak_audio_env "$decoded_command")"

if is_flatpak_command "$decoded_command"; then
    exec 9>"$portal_lock_file"
    if ! flock -w "$portal_timeout" 9; then
        echo "Another Flatpak virtual-display launch is already switching the portal environment." >&2
        exit 1
    fi

    host_env_file="$(mktemp "@PROFILE_ROOT@/portal-env-XXXXXX")"
    systemd_env_dump="$(mktemp "@PROFILE_ROOT@/systemd-env-XXXXXX")"
    log_debug "starting transient Flatpak portal handoff for prep command"

    snapshot_host_env
    start_portal_monitor
    apply_headless_portal_env
    portal_switched=1
fi

run_headless_command "$decoded_command"

if [ "${portal_switched:-0}" -eq 1 ]; then
    if ! wait_for_spawn; then
        log_debug "portal handoff timed out waiting for SpawnStarted on prep command"
    else
        log_debug "portal handoff SpawnStarted signal observed for prep command"
    fi
fi
