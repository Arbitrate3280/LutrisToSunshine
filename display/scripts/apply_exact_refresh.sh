#!/bin/bash
set -euo pipefail

# Prep-command output is written to the app's output file, which is not where
# anyone looks; keep a copy in a tool-owned log as well.
lts_log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" \
        >> "@PROFILE_ROOT@/client-sync.log" 2>/dev/null || true
    printf 'lts-display: %s\n' "$*" >&2
}

if [ "${#}" -lt 2 ]; then
    exit 0
fi

if [ -f /.flatpak-info ]; then
    sunshine_env=()
    for var in SUNSHINE_CLIENT_FPS SUNSHINE_CLIENT_WIDTH SUNSHINE_CLIENT_HEIGHT SUNSHINE_CLIENT_HMAX SUNSHINE_CLIENT_VMAX; do
        if [ -n "${!var:-}" ]; then
            sunshine_env+=("--env=$var=${!var}")
        fi
    done
    exec flatpak-spawn --host "${sunshine_env[@]}" "@APPLY_EXACT_REFRESH_SCRIPT@" "$@"
fi

width="${1}"
height="${2}"
since_time="${3:-}"
# exact: use the client's refresh rate as announced (fractional rates like 59.94).
# client: the same source, rounded to the integer rate Moonlight shows the user.
rate_mode="${4:-exact}"

# Fall back to the client's requested FPS: an unresolvable exact rate
# must not leave the display stuck on the fallback mode.
exact_stream_fps="$("@RESOLVE_STREAM_FPS_SCRIPT@" exact fallback "$since_time")"
if [ -z "$exact_stream_fps" ]; then
    exit 0
fi

if [ "$rate_mode" = "client" ]; then
    # client mode reports the integer rate Moonlight shows (60/90/120), so a
    # negotiated 59.94 becomes 60 and 119.88 becomes 120.
    exact_stream_fps="$(python3 - "$exact_stream_fps" <<'PY'
import sys

try:
    value = float(sys.argv[1])
except ValueError:
    print(sys.argv[1])
    raise SystemExit(0)
if value <= 0:
    print(sys.argv[1])
    raise SystemExit(0)
print(str(int(round(value))))
PY
)"
fi

# Prep-command output lands in Sunshine's log: report the rate the stream really uses.
lts_log "stream rate resolved to ${exact_stream_fps}Hz (${rate_mode} sync, client asked ${SUNSHINE_CLIENT_FPS:-unset})"

mode_command="output @HEADLESS_OUTPUT@ mode ${width}x${height}@${exact_stream_fps}Hz"
if [ -f /.flatpak-info ]; then
    apply_output="$(flatpak-spawn --host env SWAYSOCK="@SWAY_SOCKET@" swaymsg "$mode_command" 2>&1)" && apply_status=0 || apply_status=$?
else
    apply_output="$(SWAYSOCK="@SWAY_SOCKET@" swaymsg "$mode_command" 2>&1)" && apply_status=0 || apply_status=$?
fi
if [ "${apply_status:-0}" -eq 0 ]; then
    lts_log "set ${width}x${height}@${exact_stream_fps}Hz on @HEADLESS_OUTPUT@ (${rate_mode} sync)"
else
    lts_log "could not apply '$mode_command': $(printf '%s' "$apply_output" | tr '\n' ' ' | cut -c1-200)"
fi
