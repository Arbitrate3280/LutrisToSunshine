#!/bin/bash
set -euo pipefail

if [ ! -S "@SWAY_SOCKET@" ]; then
    exit 0
fi

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
    swaymsg_cmd "output HEADLESS-1 mode ${target_width}x${target_height}@${target_fps}Hz" >/dev/null 2>&1 || true
fi

