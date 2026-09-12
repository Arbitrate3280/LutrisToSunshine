#!/bin/bash
set -euo pipefail

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

# Fall back to the client's requested FPS: an unresolvable exact rate
# must not leave the display stuck on the fallback mode.
exact_stream_fps="$("@RESOLVE_STREAM_FPS_SCRIPT@" exact fallback "$since_time")"
if [ -z "$exact_stream_fps" ]; then
    exit 0
fi

if [ -f /.flatpak-info ]; then
    flatpak-spawn --host env SWAYSOCK="@SWAY_SOCKET@" swaymsg "output HEADLESS-1 mode ${width}x${height}@${exact_stream_fps}Hz" >/dev/null 2>&1 || true
else
    SWAYSOCK="@SWAY_SOCKET@" swaymsg "output HEADLESS-1 mode ${width}x${height}@${exact_stream_fps}Hz" >/dev/null 2>&1 || true
fi
