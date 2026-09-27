#!/bin/bash
set -euo pipefail

if [ ! -S "@SWAY_SOCKET@" ]; then
    exit 0
fi

# Prep-command output is written to the app's output file, which is not where
# anyone looks; keep a copy in a tool-owned log as well.
lts_log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" \
        >> "@PROFILE_ROOT@/client-sync.log" 2>/dev/null || true
    printf 'lts-display: %s\n' "$*" >&2
}

swaymsg_cmd() {
    if [ -f /.flatpak-info ]; then
        flatpak-spawn --host env SWAYSOCK="@SWAY_SOCKET@" swaymsg "$@"
    else
        SWAYSOCK="@SWAY_SOCKET@" swaymsg "$@"
    fi
}

mode="@REFRESH_RATE_SYNC_MODE@"
target_width="${SUNSHINE_CLIENT_WIDTH:-}"
target_height="${SUNSHINE_CLIENT_HEIGHT:-}"
target_fps="${SUNSHINE_CLIENT_FPS:-}"

# Prep-command output lands in Sunshine's log: state what the client asked for
# (SUNSHINE_CLIENT_FPS is the documented "FPS requested by the client").
lts_log "client request ${SUNSHINE_CLIENT_WIDTH:-?}x${SUNSHINE_CLIENT_HEIGHT:-?}@${SUNSHINE_CLIENT_FPS:-unset} (sync mode: @REFRESH_RATE_SYNC_MODE@)"

if [ "$mode" = "custom" ]; then
    target_width="@CUSTOM_WIDTH@"
    target_height="@CUSTOM_HEIGHT@"
    target_fps="@CUSTOM_REFRESH@"
elif [ -z "$target_width" ] || [ -z "$target_height" ] || [ -z "$target_fps" ]; then
    exit 0
fi

sync_since="$(python3 - <<'PY'
import time
print(f"{time.time():.6f}")
PY
)"
if [ "$mode" = "exact" ]; then
    # Exact mode: let apply_exact_refresh resolve the real FPS from
    # Sunshine's journal and do a single, correct mode switch.
    setsid "@APPLY_EXACT_REFRESH_SCRIPT@" "${target_width}" "${target_height}" "$sync_since" >/dev/null 2>&1 &
else
# Prep-command output lands in Sunshine's log, so report what the client asked for:
# SUNSHINE_CLIENT_FPS comes from the launch request's mode=WxHxFPS, which can
# differ from the refresh rate the stream ends up using.
# shellcheck disable=SC2016
lts_log "applying ${target_width}x${target_height}@${target_fps}Hz (client request ${SUNSHINE_CLIENT_WIDTH:-?}x${SUNSHINE_CLIENT_HEIGHT:-?}@${SUNSHINE_CLIENT_FPS:-unset}, sync mode ${mode})"
    lts_mode_command="output @HEADLESS_OUTPUT@ mode ${target_width}x${target_height}@${target_fps}Hz"
    # Never exit non-zero (Sunshine aborts the launch on a failed prep command),
    # but never fail silently either: a swallowed error leaves the display on
    # whatever rate it had, which looks exactly like the client being ignored.
    if lts_mode_output="$(swaymsg_cmd "$lts_mode_command" 2>&1)"; then
        lts_log "set ${target_width}x${target_height}@${target_fps}Hz on @HEADLESS_OUTPUT@"
    else
        lts_log "could not apply '$lts_mode_command': $(printf '%s' "$lts_mode_output" | tr '\n' ' ' | cut -c1-200)"
    fi
    if [ "$mode" = "client" ]; then
        # The rate above comes from Moonlight's launch request (mode=WxHxFPS on
        # Sunshine's HTTP launch call), while the stream follows the refresh rate
        # announced later over RTSP. When the two disagree the display would keep
        # the wrong rate, so re-apply the real one in the background once Sunshine
        # logs it - the same mechanism exact mode uses, with integer rounding.
        setsid "@APPLY_EXACT_REFRESH_SCRIPT@" "${target_width}" "${target_height}" "$sync_since" client >/dev/null 2>&1 &
    fi
fi

