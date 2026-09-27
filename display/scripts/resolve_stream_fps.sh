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

attempts=100
while [ "$attempts" -gt 0 ]; do
    stream_output_file="$(mktemp)"
    if [ -r "@SUNSHINE_LOG_FILE@" ]; then
        # Sunshine writes its log next to sunshine.conf. The systemd
        # journal cannot be trusted: Flatpak runs the app inside a
        # transient scope, so `--user -u <unit>` never sees these lines.
        cat "@SUNSHINE_LOG_FILE@" >"$stream_output_file" 2>/dev/null || true
    elif command -v journalctl >/dev/null 2>&1; then
        journalctl --user -u "@SUNSHINE_UNIT@" -n 200 --no-pager -o cat >"$stream_output_file" 2>/dev/null || true
    else
        printf '%s\n' "$requested_fps"
        exit 0
    fi
    resolved_fps="$(python3 - "$requested_fps" "$stream_output_file" "$since_time" <<'PY'
from datetime import datetime
import re
import sys

stream_output_path = sys.argv[2]
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

with open(stream_output_path, "r", encoding="utf-8", errors="replace") as handle:
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

# Sunshine logs the rate the client asked for in a backend-specific shape:
#   wlroots capture: "[wlgrab] Requested frame rate [60000/1001, approx. 59.94 fps]"
#   portal capture:  "[pipewire] Requested frame rate: 120/1, approx. 120.00 fps"
#   portal capture, variable rate:
#                    "[pipewire] Requested variable frame rate (Sunshine pacing required: 8.3333ms)"
# The portal forms never matched, so portal capture always fell back to the
# launch request's FPS (SUNSHINE_CLIENT_FPS), which is a different value.
WLGRAB_PATTERN = re.compile(
    r"Requested frame rate \[(?P<value>\d+(?:\.\d+)?)(?:"
    r"/(?P<denominator>\d+)(?:, approx\. [^\]]+)?|fps)\]"
)
PIPEWIRE_PATTERN = re.compile(
    r"Requested frame rate: (?P<value>\d+(?:\.\d+)?)"
    r"(?:/(?P<denominator>\d+))?"
)
PACING_PATTERN = re.compile(
    r"Requested variable frame rate \(Sunshine pacing required: "
    r"(?P<milliseconds>\d+(?:\.\d+)?)ms\)"
)


def rate_from_line(line: str):
    match = WLGRAB_PATTERN.search(line)
    if match:
        return rational_rate(match.group("value"), match.group("denominator"))
    match = PIPEWIRE_PATTERN.search(line)
    if match:
        return rational_rate(match.group("value"), match.group("denominator"))
    match = PACING_PATTERN.search(line)
    if match:
        milliseconds = float(match.group("milliseconds"))
        if milliseconds <= 0:
            return None
        return 1000.0 / milliseconds
    return None


def rational_rate(value_text: str, denominator_text):
    value = float(value_text)
    if denominator_text:
        denominator = int(denominator_text)
        if denominator <= 0:
            return None
        return value / denominator
    return value


# A prep command starts before Sunshine emits CLIENT CONNECTED. When the
# caller supplies since_time, the timestamp filter already isolates this
# stream, so do not discard the requested-rate line that precedes that event.
search_lines = (
    lines[connect_index + 1 :]
    if since_epoch is None and connect_index >= 0
    else lines
)
for line in reversed(search_lines):
    fps = rate_from_line(line)
    if fps is None:
        continue
    nearest = round(fps)
    if abs(fps - nearest) < 0.005:
        print(str(int(nearest)))
    else:
        print(f"{fps:.2f}")
    raise SystemExit(0)

print("")
PY
)"
    rm -f "$stream_output_file"
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
