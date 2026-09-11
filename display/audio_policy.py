"""Audio policy for the virtual display.

Owns the audio rules end to end: the WirePlumber Lua policy and its
component registration, the generated audio create/cleanup scripts
(including the persistent Sunshine ``audio_sink`` configuration), the Flatpak
per-launch audio-env injection function, and the activation-environment
drain that keeps the game marker out of host apps.  Lifecycle ordering
and state persistence stay in ``display.manager``; this module only
exposes the audio-policy interface.
"""

import json
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List

from display.utils import safe_string
from display.state import DisplayState
from display.utils import run_command
from display.constants import (
    WIREPLUMBER_POLICY_CONF_NAME,
    WIREPLUMBER_POLICY_SCRIPT_NAME,
)

# WirePlumber loads user scripts from XDG_DATA and merges XDG_CONFIG fragments.
AUDIO_STREAM_MARKER_KEY = "lutristosunshine.stream"
AUDIO_STREAM_MARKER_VALUE = "game"
AUDIO_STREAM_PULSE_PROP = f"{AUDIO_STREAM_MARKER_KEY}={AUDIO_STREAM_MARKER_VALUE}"
AUDIO_STREAM_PIPEWIRE_PROPS = f'{{ {AUDIO_STREAM_MARKER_KEY} = "{AUDIO_STREAM_MARKER_VALUE}" }}'
SUNSHINE_OWNED_SINK_NAMES = [
    "sink-sunshine-stereo",
    "sink-sunshine-surround51",
    "sink-sunshine-surround71",
]

# ponytail: shell-side flatpak option parser.  @FLAGS@ and @VALUE_OPTS@ are
# rendered at script-generation time from the same FLATPAK_FLAG_OPTIONS /
# FLATPAK_VALUE_OPTIONS constants in display.audio_policy.  If the parser's option sets change, this heredoc picks up the change
# automatically via the shared constants — but the two parsers' logic
# (option boundary, --env handling) must stay in sync.
_INJECT_FLATPAK_AUDIO_ENV_HEREDOC = """\
inject_flatpak_audio_env() {
    python3 - "$1" "@AUDIO_SINK@" "@PULSE_PROP@" '@PIPEWIRE_PROPS@' <<'PY'
import shlex
import sys

cmd = sys.argv[1]
sink = sys.argv[2]
prop = sys.argv[3]
pwprop = sys.argv[4]

AUDIO_PREFIXES = ("PULSE_SINK=", "PULSE_PROP=", "PIPEWIRE_PROPS=")
FLAGS = frozenset(@FLAGS@)
VALUE_OPTS = frozenset(@VALUE_OPTS@)
PREFIX_OPTS = frozenset(f"{opt}=" for opt in VALUE_OPTS)

try:
    tokens = shlex.split(cmd)
except ValueError:
    print(cmd)
    raise SystemExit(0)

prefix = ["flatpak-spawn", "--host"]
index = len(prefix) if tokens[: len(prefix)] == prefix else 0
if tokens[index : index + 2] != ["flatpak", "run"]:
    print(cmd)
    raise SystemExit(0)
index += 2

# Walk options using flatpak's own flag/value/prefix classification.
# Only strip audio --env tokens from the option region (before app_id);
# app_id and app_args pass through untouched.  For example, in
#   flatpak run --env PULSE_SINK=custom org.foo --env PULSE_SINK=bar
# the first --env PULSE_SINK=custom is a Flatpak option (stripped);
# --env PULSE_SINK=bar after org.foo is an app argument (kept).
filtered = list(tokens[:index])  # prefix + flatpak + run
i = index
while i < len(tokens):
    tok = tokens[i]
    if not tok.startswith("-"):
        break  # app_id
    if tok == "--":
        break  # separator -> app_id follows
    if tok in FLAGS:
        filtered.append(tok)
        i += 1
        continue
    if tok in VALUE_OPTS:
        if i + 1 < len(tokens):
            value = tokens[i + 1]
            if tok == "--env" and any(
                value.startswith(p) for p in AUDIO_PREFIXES
            ):
                i += 2  # skip both --env and its audio value
            else:
                filtered.append(tok)
                filtered.append(value)
                i += 2
        else:
            filtered.append(tok)
            i += 1
        continue
    matched = next(
        (p for p in PREFIX_OPTS if tok.startswith(p)), None
    )
    if matched:
        if tok.startswith("--env=") and any(
            tok[6:].startswith(p) for p in AUDIO_PREFIXES
        ):
            pass  # skip audio prefix option
        else:
            filtered.append(tok)
        i += 1
        continue
    break  # unknown -> app_id boundary

# Inject the managed audio tuple and game marker right after
# "flatpak run", then append app_id and any remaining app_args.
inject = [
    "--env=PULSE_SINK=" + sink,
    "--env=PULSE_PROP=" + prop,
    "--env=PIPEWIRE_PROPS=" + pwprop,
]
print(shlex.join(filtered[:index] + inject + filtered[index:] + tokens[i:]))
PY
}
"""

_CLEAR_AUDIO_ACTIVATION_ENV = """\
    # Replace stale audio values inherited from a prior session.
    if command -v dbus-update-activation-environment >/dev/null 2>&1; then
        dbus-update-activation-environment --systemd \
            PULSE_SINK= PULSE_PROP= PIPEWIRE_PROPS= >/dev/null 2>&1 || true
    fi
    systemctl --user unset-environment PULSE_SINK PULSE_PROP PIPEWIRE_PROPS >/dev/null 2>&1 || true"""


def render_flatpak_audio_env(
    audio_sink: str,
    flag_options: Iterable[str],
    value_options: Iterable[str],
) -> str:
    """Return the existing inject_flatpak_audio_env shell/Python function."""
    rendered = _INJECT_FLATPAK_AUDIO_ENV_HEREDOC.replace(
        "@FLAGS@", repr(sorted(flag_options))
    ).replace(
        "@VALUE_OPTS@", repr(sorted(value_options))
    )
    return (
        rendered.replace("@AUDIO_SINK@", audio_sink)
        .replace("@PULSE_PROP@", AUDIO_STREAM_PULSE_PROP)
        .replace("@PIPEWIRE_PROPS@", AUDIO_STREAM_PIPEWIRE_PROPS)
    )


def clear_activation_env_script() -> str:
    """Return the existing shell snippet that clears audio vars from activation env."""
    return _CLEAR_AUDIO_ACTIVATION_ENV


_WIREPLUMBER_POLICY_SCRIPT_TEMPLATE = """-- WirePlumber policy: LutrisToSunshine audio enforcement.
--
-- Sunshine forces the system default sink to its capture sink at stream start
-- (src/platform/linux/audio.cpp :: set_sink -> pa_context_set_default_sink) and
-- there is no Sunshine config to disable it; LTS instead routes the game per-app
-- (PULSE_SINK) and captures the managed sink's monitor, so the system default
-- must stay on host hardware. This script enforces the invariants declaratively,
-- in-process and event-driven (no polling, no subprocesses):
--   1. The default sink is never a managed sink (select-default-node hook).
--   2. A game stream (lutristosunshine.stream=game) targets the managed sink.
--   3. Sunshine's recorder targets the managed sink's monitor.

local log = Log.open_topic("s-lts-audio")

local audio_sink = "__AUDIO_SINK__"
local managed_sinks = {
__MANAGED_SINKS_TABLE__}
local last_host_default = nil

local function is_restorable_sink (props)
  local name = props ["node.name"]
  return name and not managed_sinks [name]
     and (props ["media.class"] or ""):match ("Audio/Sink")
end

local function is_fallback_sink (props)
  return is_restorable_sink (props) and props ["node.virtual"] ~= "true"
end

SimpleEventHook {
  name = "lts-audio/guard-default-sink",
  after = { "default-nodes/find-best-default-node",
            "default-nodes/find-selected-default-node",
            "default-nodes/find-stored-default-node" },
  before = { "default-nodes/apply-default-node" },
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "select-default-node" },
    },
  },
  execute = function (event)
    local props = event:get_properties ()
    if props ["default-node.type"] ~= "audio.sink" then return end

    local selected = event:get_data ("selected-node")
    if type (selected) ~= "string" and selected then
      local ok, v = pcall (function () return selected:parse () end)
      if ok then selected = v end
    end

    local om = event:get_source ():call ("get-object-manager", "node")
    if not om then return end

    -- Remember the actual configured host sink whenever WirePlumber selects
    -- one. Sunshine may replace it with the managed sink before the next
    -- selection event, so choosing by priority alone could restore the wrong
    -- hardware device on machines with multiple outputs.
    if selected and not managed_sinks [selected] then
      for node in om:iterate () do
        local p = node.properties
        if p ["node.name"] == selected and is_restorable_sink (p) then
          last_host_default = selected
          break
        end
      end
      return
    end
    if not selected or not managed_sinks [selected] then return end

    local best_name, best_prio = nil, -1
    for node in om:iterate () do
      local p = node.properties
      local name = p ["node.name"]
      if name == last_host_default and is_restorable_sink (p) then
        best_name = name
        break
      end
      if is_fallback_sink (p) then
        local prio = tonumber (p ["priority.session"] or "0") or 0
        if prio > best_prio then best_name, best_prio = name, prio end
      end
    end
    if best_name then
      last_host_default = best_name
      log:info ("override default sink " .. selected .. " -> " .. best_name)
      event:set_data ("selected-node", best_name)

      -- Keep the configured default-node stack aligned with the runtime
      -- override. Without this, default-nodes stores the managed sink and
      -- reintroduces it on the next graph change or login.
      local metadata_om = event:get_source ():call ("get-object-manager", "metadata")
      if metadata_om then
        local metadata = metadata_om:lookup {
          Constraint { "metadata.name", "=", "default" },
        }
        if metadata then
          metadata:set (0, "default.configured.audio.sink", "Spa:String:JSON",
                        Json.Object { ["name"] = best_name }:to_string ())
        end
      end
    end
  end,
}:register ()

-- Pin game streams to the managed sink and Sunshine's recorder to the managed
-- monitor, authoritatively: a tagged stream is always re-pinned even if
-- stream-restore or the Flatpak portal pre-set another target (the host default
-- on a client disconnect/reconnect), which would otherwise strand game audio on
-- the host. Untagged streams are left to WirePlumber's native linking.
local lutils = require ("linking-utils")

local function find_linkable (om, node_name)
  return om:lookup {
    Constraint { "node.name", "=", node_name, type = "pw-global" },
  }
end

SimpleEventHook {
  name = "lts-audio/route-game-and-recorder",
  before = { "linking/find-best-target" },
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "select-target" },
    },
  },
  execute = function (event)
    local source, om, si, si_props =
      lutils:unwrap_select_target_event (event)
    -- Authoritative for streams we manage: override any target that
    -- stream-restore or the Flatpak portal pre-set (e.g. the host default on a
    -- client disconnect/reconnect graph change). Streams we do not manage fall
    -- through the `if not want` guard below, so this never hijacks them.

    local marker = si_props ["lutristosunshine.stream"]
    local class = si_props ["media.class"] or ""
    local want
    if marker == "game" then
      want = audio_sink
    elseif (class:match ("Source") or class:match ("Record"))
       and (si_props ["application.name"] == "sunshine"
            or si_props ["media.name"] == "sunshine-record") then
      want = audio_sink .. ".monitor"
    end
    if not want then return end

    local t = find_linkable (om, want)
    if t then
      log:info ("pin " .. tostring (si_props ["node.name"]) .. " -> " .. want)
      event:set_data ("target", t)
    end
  end,
}:register ()

Log.info ("LTS audio policy registered")
"""


def _wireplumber_policy_script(audio_sink: str) -> str:
    managed = list(dict.fromkeys([audio_sink, *SUNSHINE_OWNED_SINK_NAMES]))
    table = "".join(f'  ["{name}"] = true,\n' for name in managed if name)
    return (
        _WIREPLUMBER_POLICY_SCRIPT_TEMPLATE
        .replace("__AUDIO_SINK__", audio_sink)
        .replace("__MANAGED_SINKS_TABLE__", table)
    )


def _wireplumber_policy_conf() -> str:
    return (
        "# LutrisToSunshine: register the host audio policy in WirePlumber.\n"
        "wireplumber.components = [\n"
        "  {\n"
        f"    name = {WIREPLUMBER_POLICY_SCRIPT_NAME}, type = script/lua,\n"
        "    provides = script.lts-audio-policy\n"
        "  }\n"
        "]\n"
        "wireplumber.profiles = {\n"
        "  main = {\n"
        "    script.lts-audio-policy = required\n"
        "  }\n"
        "}\n"
    )


def _reload_wireplumber() -> None:
    run_command(["systemctl", "--user", "reload-or-restart", "wireplumber"])


def _audio_create_script(state: DisplayState, audio_sink: str) -> str:
    """Render the audio-create script; keep Sunshine pointed at the managed sink."""
    paths = state.paths
    return f"""#!/bin/bash
set -euo pipefail

sunshine_conf="{paths.sunshine_conf}"
sink_name="{audio_sink}"
module_file="{paths.audio_module_file}"
runtime_dir="${{XDG_RUNTIME_DIR:-/run/user/$(id -u)}}"
dbus_value="${{DBUS_SESSION_BUS_ADDRESS:-unix:path=$runtime_dir/bus}}"
pulse_server_value="${{PULSE_SERVER:-}}"
pulse_clientconfig_value="${{PULSE_CLIENTCONFIG:-}}"

run_audio_command() {{
    local command=(/usr/bin/env
        "XDG_RUNTIME_DIR=$runtime_dir"
        "DBUS_SESSION_BUS_ADDRESS=$dbus_value"
    )
    if [ -n "$pulse_server_value" ]; then
        command+=("PULSE_SERVER=$pulse_server_value")
    fi
    if [ -n "$pulse_clientconfig_value" ]; then
        command+=("PULSE_CLIENTCONFIG=$pulse_clientconfig_value")
    fi
    command+=("$@")
    "${{command[@]}}"
}}

ensure_sunshine_audio_sink() {{
    python3 - "$sunshine_conf" "$sink_name" <<'PY'
import sys
from pathlib import Path

conf_path = Path(sys.argv[1])
managed_sink = sys.argv[2]
lines = conf_path.read_text(encoding="utf-8").splitlines() if conf_path.exists() else []
updated_lines = []
replaced = False
for line in lines:
    stripped = line.strip()
    if stripped and not stripped.startswith("#") and "=" in stripped:
        key = stripped.split("=", 1)[0].strip()
        if key == "audio_sink":
            updated_lines.append(f"audio_sink = {{managed_sink}}")
            replaced = True
            continue
    updated_lines.append(line)
if not replaced:
    if updated_lines and updated_lines[-1] != "":
        updated_lines.append("")
    updated_lines.append(f"audio_sink = {{managed_sink}}")
conf_path.parent.mkdir(parents=True, exist_ok=True)
conf_path.write_text("\\n".join(updated_lines) + "\\n", encoding="utf-8")
PY
}}

ensure_sunshine_audio_sink

if run_audio_command pactl list sinks short 2>/dev/null | grep -q "$sink_name"; then
    exit 0
fi

if ! module_id="$(run_audio_command pactl load-module module-null-sink "sink_name=$sink_name" "sink_properties=device.description=$sink_name" 2>/dev/null)"; then
    echo "Audio sink creation failed: $sink_name" >&2
    exit 1
fi
if [ -z "$module_id" ]; then
    echo "Audio sink creation returned no module id: $sink_name" >&2
    exit 1
fi
echo "$module_id" > "$module_file"
"""


def _audio_cleanup_script(state: DisplayState) -> str:
    """Render the audio-cleanup script; remove only the runtime sink module."""
    paths = state.paths
    return f"""#!/bin/bash
set -euo pipefail

sink_name="{safe_string(state.audio_sink)}"
module_file="{paths.audio_module_file}"
runtime_dir="${{XDG_RUNTIME_DIR:-/run/user/$(id -u)}}"
dbus_value="${{DBUS_SESSION_BUS_ADDRESS:-unix:path=$runtime_dir/bus}}"
pulse_server_value="${{PULSE_SERVER:-}}"
pulse_clientconfig_value="${{PULSE_CLIENTCONFIG:-}}"

run_audio_command() {{
    local command=(/usr/bin/env
        "XDG_RUNTIME_DIR=$runtime_dir"
        "DBUS_SESSION_BUS_ADDRESS=$dbus_value"
    )
    if [ -n "$pulse_server_value" ]; then
        command+=("PULSE_SERVER=$pulse_server_value")
    fi
    if [ -n "$pulse_clientconfig_value" ]; then
        command+=("PULSE_CLIENTCONFIG=$pulse_clientconfig_value")
    fi
    command+=("$@")
    "${{command[@]}}"
}}

module_ids=()
if [ -f "$module_file" ]; then
    module_id="$(cat "$module_file")"
    if [ -n "$module_id" ]; then
        module_ids+=("$module_id")
    fi
fi
while read -r module_id; do
    if [ -n "$module_id" ]; then
        module_ids+=("$module_id")
    fi
done < <(
    run_audio_command pactl list modules short 2>/dev/null \
        | awk -v sink_name="$sink_name" \
            '$2 == "module-null-sink" && index($0, "sink_name=" sink_name) {{ print $1 }}' \
        || true
)

printf '%s\\n' "${{module_ids[@]}}" | sort -u | while read -r module_id; do
    if [ -n "$module_id" ]; then
        run_audio_command pactl unload-module "$module_id" >/dev/null 2>&1 || true
    fi
done
rm -f "$module_file"
"""


def managed_files(state: DisplayState) -> Dict[Path, str]:
    """Return all audio-owned generated files without writing or running commands."""
    paths = state.paths
    audio_sink = safe_string(state.audio_sink)
    return {
        Path(paths.audio_create_script): _audio_create_script(state, audio_sink),
        Path(paths.audio_cleanup_script): _audio_cleanup_script(state),
        Path(paths.wireplumber_policy_script): _wireplumber_policy_script(audio_sink),
        Path(paths.wireplumber_policy_conf): _wireplumber_policy_conf(),
    }


def _read_key_value(path: Path, key: str) -> Dict[str, Any]:
    if not path.exists():
        return {"present": False, "value": ""}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        current_key, value = stripped.split("=", 1)
        if current_key.strip() == key:
            return {"present": True, "value": value.strip()}
    return {"present": False, "value": ""}


def _set_key_value(path: Path, key: str, value: str) -> None:
    lines: List[str] = []
    found = False
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    updated_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            current_key = stripped.split("=", 1)[0].strip()
            if current_key == key:
                updated_lines.append(f"{key} = {value}")
                found = True
                continue
        updated_lines.append(line)

    if not found:
        if updated_lines and updated_lines[-1] != "":
            updated_lines.append("")
        updated_lines.append(f"{key} = {value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")


def _remove_key(path: Path, key: str) -> None:
    if not path.exists():
        return
    updated_lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            current_key = stripped.split("=", 1)[0].strip()
            if current_key == key:
                continue
        updated_lines.append(line)
    path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")


def _read_global_prep_cmd_list(sunshine_conf: Path) -> List[Dict[str, str]]:
    raw = _read_key_value(sunshine_conf, "global_prep_cmd")
    if not raw.get("present"):
        return []
    try:
        parsed = json.loads(raw.get("value", ""))
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [entry for entry in parsed if isinstance(entry, dict)]


_LEGACY_PREP_CMD_MARKERS = (
    "lutristosunshine-stream-audio-start",
    "lutristosunshine-stream-audio-stop",
)
_AUDIO_PREP_CMD_MARKER = "lutristosunshine-create-audio-sink.sh"


def _strip_legacy_global_prep_cmd(state: DisplayState) -> None:
    """Remove the obsolete LTS stream-audio hooks from Sunshine's global_prep_cmd.

    The WirePlumber policy replaces the old do/undo scripts; strip any stale
    entries left from a previous install so Sunshine does not run dead commands.
    """
    sunshine_conf = Path(state.paths.sunshine_conf)
    entries = _read_global_prep_cmd_list(sunshine_conf)
    kept = [
        entry for entry in entries
        if not any(
            marker in str(entry.get("do", "")) or marker in str(entry.get("undo", ""))
            for marker in _LEGACY_PREP_CMD_MARKERS
        )
    ]
    if len(kept) == len(entries):
        return
    if kept:
        _set_key_value(sunshine_conf, "global_prep_cmd", json.dumps(kept))
    else:
        _remove_key(sunshine_conf, "global_prep_cmd")


def _sunshine_uses_flatpak(state: DisplayState) -> bool:
    """Use the recorded executable, not the ambiguous systemd alias, as the signal."""
    executable = state.sunshine_execstart.lower()
    if executable:
        return "flatpak" in executable or "dev.lizardbyte.app.sunshine" in executable
    return state.sunshine_unit_name.startswith("app-")


def _audio_prep_command(state: DisplayState) -> str:
    command = shlex.quote(state.paths.audio_create_script)
    if _sunshine_uses_flatpak(state):
        return f"flatpak-spawn --host {command}"
    return command


def _audio_cleanup_command(state: DisplayState) -> str:
    command = shlex.quote(state.paths.audio_cleanup_script)
    if _sunshine_uses_flatpak(state):
        return f"flatpak-spawn --host {command}"
    return command


def _sync_audio_prep_cmd(state: DisplayState, enabled: bool) -> None:
    """Create the sink before each stream and remove it after that stream."""
    sunshine_conf = Path(state.paths.sunshine_conf)
    entries = _read_global_prep_cmd_list(sunshine_conf)
    kept = [
        entry for entry in entries
        if _AUDIO_PREP_CMD_MARKER not in str(entry.get("do", ""))
        and _AUDIO_PREP_CMD_MARKER not in str(entry.get("undo", ""))
    ]
    if enabled:
        kept.append({
            "do": _audio_prep_command(state),
            "undo": _audio_cleanup_command(state),
        })
    if kept:
        _set_key_value(sunshine_conf, "global_prep_cmd", json.dumps(kept))
    else:
        _remove_key(sunshine_conf, "global_prep_cmd")


def _drain_stale_audio_activation_env() -> None:
    """Clear stale audio vars from the systemd/DBus activation environment.

    The Flatpak portal handoff imported ``PULSE_SINK``, ``PULSE_PROP``,
    and ``PIPEWIRE_PROPS`` into the global activation environment where
    they persist for the login session, permanently tagging host apps as
    game streams.  Sets the DBus and systemd values to empty; then
    ``systemctl --user unset-environment`` removes them from systemd.
    """
    try:
        if shutil.which("dbus-update-activation-environment"):
            run_command(
                [
                    "dbus-update-activation-environment",
                    "--systemd",
                    "PULSE_SINK=",
                    "PULSE_PROP=",
                    "PIPEWIRE_PROPS=",
                ]
            )
    except OSError:
        pass
    try:
        run_command(
            [
                "systemctl",
                "--user",
                "unset-environment",
                "PULSE_SINK",
                "PULSE_PROP",
                "PIPEWIRE_PROPS",
            ]
        )
    except OSError:
        pass


def setup(state: DisplayState) -> None:
    """Install the policy and clear legacy global audio environment state."""
    _strip_legacy_global_prep_cmd(state)
    _sync_audio_prep_cmd(state, enabled=True)
    _reload_wireplumber()
    _drain_stale_audio_activation_env()


def start(state: DisplayState) -> None:
    """Ensure stream-scoped audio prep is installed before Sunshine starts."""
    _sync_audio_prep_cmd(state, enabled=True)
    _drain_stale_audio_activation_env()


def stop(state: DisplayState) -> None:
    """Remove the runtime sink while leaving Sunshine configured for it."""
    cleanup_script = Path(state.paths.audio_cleanup_script)
    if not cleanup_script.is_file():
        return
    try:
        run_command([str(cleanup_script)])
    except OSError:
        pass


def remove(state: DisplayState) -> None:
    """Remove policy files, reload WirePlumber, and remove legacy audio prep hooks."""
    for path in (state.paths.wireplumber_policy_script, state.paths.wireplumber_policy_conf):
        try:
            Path(path).unlink()
        except OSError:
            pass
    _reload_wireplumber()
    _strip_legacy_global_prep_cmd(state)
    _sync_audio_prep_cmd(state, enabled=False)
