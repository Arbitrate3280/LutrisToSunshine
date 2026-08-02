"""Tests for the concentrated audio-policy module.

Covers the pure renderer (managed_files), the Flatpak audio-env
renderer, the Sunshine ``audio_sink`` snapshot/restore lifecycle, and
the WirePlumber/legacy-hook cleanup.  Every test runs against temporary
paths; nothing touches the real user configuration.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from display import audio_policy
from display import manager
from display.state import DisplayPaths
from tests._display_test_helpers import temp_display_state


def _wp_script(state):
    files = audio_policy.managed_files(state)
    return files[Path(state.paths.wireplumber_policy_script)]


def _wp_conf(state):
    files = audio_policy.managed_files(state)
    return files[Path(state.paths.wireplumber_policy_conf)]


class AudioPolicyTests(unittest.TestCase):
    def _temp_state(self, config_text: str = "audio_sink = host-speakers\n"):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        conf_path = Path(tempdir.name) / "sunshine.conf"
        conf_path.write_text(config_text, encoding="utf-8")

        state = manager._default_state()
        state.enabled = True
        state.paths = DisplayPaths(**asdict(state.paths))
        state.paths.sunshine_conf = str(conf_path)
        return state, conf_path

    def _fake_run(self, calls):
        def fake_run(command, **kwargs):
            calls.append(list(command))
            return subprocess.CompletedProcess(command, 0, "", "")
        return fake_run

    def test_managed_files_render_policy_and_audio_scripts(self) -> None:
        with temp_display_state(manager) as (state, base):
            conf_path = base / "sunshine.conf"
            conf_path.write_text("audio_sink = host-speakers\n", encoding="utf-8")
            state.paths.sunshine_conf = str(conf_path)

            files = audio_policy.managed_files(state)
            self.assertEqual(
                set(files),
                {
                    Path(state.paths.audio_create_script),
                    Path(state.paths.audio_cleanup_script),
                    Path(state.paths.wireplumber_policy_script),
                    Path(state.paths.wireplumber_policy_conf),
                },
            )

            wp_script = files[Path(state.paths.wireplumber_policy_script)]
            wp_conf = files[Path(state.paths.wireplumber_policy_conf)]
            self.assertIn('name = "lts-audio/guard-default-sink"', wp_script)
            self.assertIn('name = "lts-audio/route-game-and-recorder"', wp_script)
            self.assertIn('audio_sink = "lts-sunshine-stereo"', wp_script)
            for name in ("sink-sunshine-stereo", "sink-sunshine-surround51", "sink-sunshine-surround71"):
                self.assertIn(f'["{name}"] = true', wp_script)
            self.assertIn(audio_policy.WIREPLUMBER_POLICY_SCRIPT_NAME, wp_conf)
            self.assertIn("script.lts-audio-policy = required", wp_conf)

            create = files[Path(state.paths.audio_create_script)]
            cleanup = files[Path(state.paths.audio_cleanup_script)]
            # Moved Sunshine config snapshot lives in the create script before
            # the pactl operations; restore lives in the cleanup script.
            self.assertIn("prepare_audio_state", create)
            self.assertIn('audio_sink = {managed_sink}', create)
            self.assertIn("run_audio_command pactl list sinks short", create)
            self.assertIn("run_audio_command pactl load-module", create)
            self.assertIn("restore_audio_state", cleanup)
            self.assertIn("run_audio_command pactl unload-module", cleanup)
            # Pure renderer: no files written, no commands run (the fixture
            # placeholders stay untouched).
            self.assertEqual(Path(state.paths.wireplumber_policy_script).read_text(), "managed\n")
            self.assertEqual(Path(state.paths.wireplumber_policy_conf).read_text(), "managed\n")
            self.assertEqual(Path(state.paths.audio_create_script).read_text(), "managed\n")

    def test_setup_remembers_sink_drains_activation_env_and_reloads_wireplumber(self) -> None:
        state, conf_path = self._temp_state()
        calls = []
        with patch.object(audio_policy.subprocess, "run", side_effect=self._fake_run(calls)), \
             patch.object(audio_policy.shutil, "which", return_value="/usr/bin/dbus-update-activation-environment"):
            audio_policy.setup(state)

        self.assertEqual(
            state.sunshine_audio_sink,
            {"present": True, "value": "host-speakers"},
        )
        self.assertEqual(conf_path.read_text(encoding="utf-8"), "audio_sink = host-speakers\n")
        self.assertIn(
            ["dbus-update-activation-environment", "--systemd", "PULSE_SINK=", "PULSE_PROP=", "PIPEWIRE_PROPS="],
            calls,
        )
        self.assertIn(
            ["systemctl", "--user", "unset-environment", "PULSE_SINK", "PULSE_PROP", "PIPEWIRE_PROPS"],
            calls,
        )
        self.assertIn(
            ["systemctl", "--user", "reload-or-restart", "wireplumber"],
            calls,
        )

    def test_start_remembers_sink_without_overwriting_config(self) -> None:
        state, conf_path = self._temp_state()
        calls = []
        with patch.object(audio_policy.subprocess, "run", side_effect=self._fake_run(calls)), \
             patch.object(audio_policy.shutil, "which", return_value="/usr/bin/dbus-update-activation-environment"):
            audio_policy.start(state)

        self.assertEqual(
            state.sunshine_audio_sink,
            {"present": True, "value": "host-speakers"},
        )
        self.assertEqual(conf_path.read_text(encoding="utf-8"), "audio_sink = host-speakers\n")

    def test_stop_restores_existing_sink(self) -> None:
        state, conf_path = self._temp_state("audio_sink = lts-sunshine-stereo\n")
        state.sunshine_audio_sink = {"present": True, "value": "host-speakers"}

        audio_policy.stop(state)

        self.assertEqual(conf_path.read_text(encoding="utf-8"), "audio_sink = host-speakers\n")

    def test_stop_removes_missing_original_sink(self) -> None:
        state, conf_path = self._temp_state("audio_sink = lts-sunshine-stereo\n")
        state.sunshine_audio_sink = {"present": False, "value": ""}

        audio_policy.stop(state)

        self.assertNotIn("audio_sink", conf_path.read_text(encoding="utf-8"))

    def test_remove_deletes_policy_files_and_legacy_audio_entries(self) -> None:
        with temp_display_state(manager) as (state, base):
            conf_path = base / "sunshine.conf"
            user_entry = {"do": "user-cmd", "undo": "user-undo"}
            lts_entry = {
                "do": "/opt/lutristosunshine/bin/lutristosunshine-stream-audio-start.sh",
                "undo": "/opt/lutristosunshine/bin/lutristosunshine-stream-audio-stop.sh",
            }
            conf_path.write_text(
                f"audio_sink = lts-sunshine-stereo\nglobal_prep_cmd = {json.dumps([user_entry, lts_entry])}\n",
                encoding="utf-8",
            )
            state.paths.sunshine_conf = str(conf_path)
            script_path = Path(state.paths.wireplumber_policy_script)
            conf_path_wp = Path(state.paths.wireplumber_policy_conf)
            script_path.write_text("managed\n", encoding="utf-8")
            conf_path_wp.write_text("managed\n", encoding="utf-8")

            calls = []
            with patch.object(audio_policy.subprocess, "run", side_effect=self._fake_run(calls)):
                audio_policy.remove(state)

            self.assertFalse(script_path.exists())
            self.assertFalse(conf_path_wp.exists())
            self.assertIn(
                ["systemctl", "--user", "reload-or-restart", "wireplumber"],
                calls,
            )
            conf_text = conf_path.read_text(encoding="utf-8")
            self.assertIn("global_prep_cmd", conf_text)
            self.assertIn("user-cmd", conf_text)
            self.assertNotIn("stream-audio-start", conf_text)
            self.assertNotIn("stream-audio-stop", conf_text)

    def test_flatpak_audio_env_rendering_preserves_existing_behavior(self) -> None:
        rendered = audio_policy.render_flatpak_audio_env(
            "lts-sunshine-stereo", manager.FLATPAK_FLAG_OPTIONS, manager.FLATPAK_VALUE_OPTIONS
        )
        func_start = rendered.index("inject_flatpak_audio_env()")
        marker = "<<'PY'\n"
        start = rendered.index(marker, func_start) + len(marker)
        end = rendered.index("\nPY\n", start)
        py_code = rendered[start:end]

        def run_inject(cmd, sink="lts-sunshine-stereo"):
            result = subprocess.run(
                [sys.executable, "-c", py_code, cmd, sink,
                 audio_policy.AUDIO_STREAM_PULSE_PROP, audio_policy.AUDIO_STREAM_PIPEWIRE_PROPS],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip()

        # Managed injection of all three audio vars (joined because shlex
        # splits the quoted PIPEWIRE_PROPS value into multiple tokens).
        joined = run_inject("flatpak run com.test.App")
        tokens = joined.split()
        self.assertIn("--env=PULSE_SINK=lts-sunshine-stereo", tokens)
        self.assertIn(f"--env=PULSE_PROP={audio_policy.AUDIO_STREAM_PULSE_PROP}", tokens)
        self.assertIn(
            f"PIPEWIRE_PROPS={audio_policy.AUDIO_STREAM_PIPEWIRE_PROPS}",
            joined,
        )

        # Malformed command passes through unchanged.
        malformed = 'flatpak run com.x.Y "unterminated'
        self.assertEqual(run_inject(malformed), malformed)

        # Idempotent: re-injecting the output changes nothing.
        once = run_inject("flatpak run com.example.Game")
        self.assertEqual(run_inject(once), once)


if __name__ == "__main__":
    unittest.main()
