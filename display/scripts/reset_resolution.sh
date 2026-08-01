#!/bin/bash
set -euo pipefail

if [ ! -S "@SWAY_SOCKET@" ]; then
    exit 0
fi

if [ -f /.flatpak-info ]; then
    flatpak-spawn --host env SWAYSOCK="@SWAY_SOCKET@" swaymsg "output HEADLESS-1 mode @FALLBACK_MODE@" >/dev/null 2>&1 || true
else
    SWAYSOCK="@SWAY_SOCKET@" swaymsg "output HEADLESS-1 mode @FALLBACK_MODE@" >/dev/null 2>&1 || true
fi
