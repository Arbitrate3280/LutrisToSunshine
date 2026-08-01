#!/bin/bash
set -euo pipefail

if [ -f /.flatpak-info ]; then
    sunshine_env=()
    for var in SUNSHINE_CLIENT_FPS SUNSHINE_CLIENT_WIDTH SUNSHINE_CLIENT_HEIGHT SUNSHINE_CLIENT_HMAX SUNSHINE_CLIENT_VMAX; do
        if [ -n "${!var:-}" ]; then
            sunshine_env+=("--env=$var=${!var}")
        fi
    done
    exec flatpak-spawn --host "${sunshine_env[@]}" "@RESOLVE_STREAM_FPS_SCRIPT@" "$@"
fi

requested_fps="${SUNSHINE_CLIENT_FPS:-}"
mode_override="${1:-}"
fallback_mode="${2:-fallback}"
since_time="${3:-}"
mode="@REFRESH_RATE_SYNC_MODE@"
custom_fps="@CUSTOM_REFRESH@"

if [ -n "$mode_override" ]; then
    mode="$mode_override"
fi

if [ "$mode" = "custom" ]; then
    printf '%s\n' "$custom_fps"
    exit 0
fi

if [ -z "$requested_fps" ]; then
    exit 0
fi

if [ "$mode" != "exact" ]; then
    printf '%s\n' "$requested_fps"
    exit 0
fi

if ! command -v journalctl >/dev/null 2>&1; then
    printf '%s\n' "$requested_fps"
    exit 0
fi

attempts=100
while [ "$attempts" -gt 0 ]; do
    journal_output_file="$(mktemp)"
    journalctl --user -u "@SUNSHINE_UNIT@" -n 200 --no-pager -o cat >"$journal_output_file" 2>/dev/null || true
    resolved_fps="$(python3 - "$requested_fps" "$journal_output_file" "$since_time" <<'PY'
from datetime import datetime
import re
import sys

journal_output_path = sys.argv[2]
since_time = sys.argv[3].strip()
since_epoch = None
if since_time:
    try:
        since_epoch = float(since_time)
    except ValueError:
        since_epoch = None

timestamp_pattern = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\]:")

def parse_epoch(line: str):
    match = timestamp_pattern.match(line)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S.%f").timestamp()
    except ValueError:
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            return None

with open(journal_output_path, "r", encoding="utf-8", errors="replace") as handle:
    raw_lines = [line.strip() for line in handle.read().splitlines() if line.strip()]

lines = []
for line in raw_lines:
    if since_epoch is None:
        lines.append(line)
        continue
    line_epoch = parse_epoch(line)
    if line_epoch is not None and line_epoch >= since_epoch:
        lines.append(line)

connect_index = -1
for index, line in enumerate(lines):
    if "CLIENT CONNECTED" in line:
        connect_index = index

pattern = re.compile(r"Requested frame rate \[(\d+)/(\d+)(?:, approx\. [^\]]+)?\]")
search_lines = lines[connect_index + 1 :] if connect_index >= 0 else lines
for line in reversed(search_lines):
    match = pattern.search(line)
    if not match:
        continue
    numerator = int(match.group(1))
    denominator = int(match.group(2))
    if denominator <= 0:
        break
    fps = numerator / denominator
    nearest = round(fps)
    if abs(fps - nearest) < 0.005:
        print(str(int(nearest)))
    else:
        print(f"{fps:.2f}")
    raise SystemExit(0)

print("")
PY
)"
    rm -f "$journal_output_file"
    if [ -n "$resolved_fps" ]; then
        printf '%s\n' "$resolved_fps"
        exit 0
    fi
    attempts=$((attempts - 1))
    sleep 0.1
done

if [ "$fallback_mode" = "none" ]; then
    exit 0
fi

printf '%s\n' "$requested_fps"
