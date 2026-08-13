"""Tests for the concentrated audio-policy module.

Covers the pure renderer (managed_files), the Flatpak audio-env
renderer, the persistent Sunshine ``audio_sink`` lifecycle, and the
WirePlumber/legacy-hook cleanup.  Every test runs against temporary paths;
nothing touches the real user configuration.
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
            self.assertIn('default.configured.audio.sink', wp_script)
            self.assertIn('last_host_default', wp_script)
            self.assertIn('name == last_host_default', wp_script)
            self.assertIn('metadata:set (0, "default.configured.audio.sink"', wp_script)
            self.assertIn('name = "lts-audio/route-game-and-recorder"', wp_script)
            self.assertIn('audio_sink = "lts-sunshine-stereo"', wp_script)
            for name in ("sink-sunshine-stereo", "sink-sunshine-surround51", "sink-sunshine-surround71"):
                self.assertIn(f'["{name}"] = true', wp_script)
            self.assertIn(audio_policy.WIREPLUMBER_POLICY_SCRIPT_NAME, wp_conf)
            self.assertIn("script.lts-audio-policy = required", wp_conf)

            create = files[Path(state.paths.audio_create_script)]
            cleanup = files[Path(state.paths.audio_cleanup_script)]
            # Sunshine stays configured for the managed sink; cleanup only
            # removes the runtime module and never rewrites sunshine.conf.
            self.assertNotIn("state.get(\"sunshine_audio_sink\")", create)
            self.assertIn('audio_sink = {managed_sink}', create)
            self.assertIn("run_audio_command pactl list sinks short", create)
            self.assertIn("run_audio_command pactl load-module", create)
            self.assertIn("Audio sink creation failed", create)
            self.assertNotIn("|| true)", create)
            self.assertNotIn("restore_audio_state", cleanup)
            self.assertIn('sink_name="lts-sunshine-stereo"', cleanup)
            self.assertIn("run_audio_command pactl unload-module", cleanup)
            self.assertIn("pactl list modules short", cleanup)
            # Pure renderer: no files written, no commands run (the fixture
            # placeholders stay untouched).
            self.assertEqual(Path(state.paths.wireplumber_policy_script).read_text(), "managed\n")
            self.assertEqual(Path(state.paths.wireplumber_policy_conf).read_text(), "managed\n")
            self.assertEqual(Path(state.paths.audio_create_script).read_text(), "managed\n")

    def test_setup_adds_stream_scoped_audio_prep(self) -> None:
        state, conf_path = self._temp_state(
            "global_prep_cmd = " + json.dumps([
                {"do": "user-cmd", "undo": "user-undo"},
            ]) + "\n"
        )
        state.sunshine_unit_name = "app-dev.lizardbyte.app.Sunshine.service"
        calls = []
        with patch.object(audio_policy.subprocess, "run", side_effect=self._fake_run(calls)), \
             patch.object(audio_policy.shutil, "which", return_value=None):
            audio_policy.setup(state)
            audio_policy.setup(state)

        config = json.loads(audio_policy._read_key_value(Path(conf_path), "global_prep_cmd")["value"])
        self.assertEqual(config[0], {"do": "user-cmd", "undo": "user-undo"})
        managed = [entry for entry in config if "create-audio-sink.sh" in entry.get("do", "")]
        self.assertEqual(len(managed), 1)
        self.assertIn("flatpak-spawn --host", managed[0]["do"])
        self.assertIn("flatpak-spawn --host", managed[0]["undo"])
        self.assertIn("cleanup-audio-sink.sh", managed[0]["undo"])

    def test_native_sunshine_alias_does_not_get_flatpak_audio_prep(self) -> None:
        state, conf_path = self._temp_state()
        state.sunshine_unit_name = "app-dev.lizardbyte.app.Sunshine.service"
        state.sunshine_execstart = "/usr/bin/sunshine"
        calls = []
        with patch.object(audio_policy.subprocess, "run", side_effect=self._fake_run(calls)), \
             patch.object(audio_policy.shutil, "which", return_value=None):
            audio_policy.setup(state)

        config = json.loads(audio_policy._read_key_value(conf_path, "global_prep_cmd")["value"])
        self.assertEqual(config[-1]["do"], state.paths.audio_create_script)
        self.assertEqual(config[-1]["undo"], state.paths.audio_cleanup_script)

    def test_setup_drains_activation_env_and_reloads_wireplumber(self) -> None:
        state, conf_path = self._temp_state()
        calls = []
        with patch.object(audio_policy.subprocess, "run", side_effect=self._fake_run(calls)), \
             patch.object(audio_policy.shutil, "which", return_value="/usr/bin/dbus-update-activation-environment"):
            audio_policy.setup(state)

        self.assertIn("audio_sink = host-speakers\n", conf_path.read_text(encoding="utf-8"))
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

    def test_start_drains_activation_env_without_rewriting_config(self) -> None:
        state, conf_path = self._temp_state()
        calls = []
        with patch.object(audio_policy.subprocess, "run", side_effect=self._fake_run(calls)), \
             patch.object(audio_policy.shutil, "which", return_value="/usr/bin/dbus-update-activation-environment"):
            audio_policy.start(state)

        self.assertIn("audio_sink = host-speakers\n", conf_path.read_text(encoding="utf-8"))

    def test_stop_cleans_runtime_audio_without_rewriting_sunshine_config(self) -> None:
        state, conf_path = self._temp_state("audio_sink = lts-sunshine-stereo\n")
        calls = []
        with patch.object(audio_policy.subprocess, "run", side_effect=self._fake_run(calls)):
            audio_policy.stop(state)

        self.assertEqual(conf_path.read_text(encoding="utf-8"), "audio_sink = lts-sunshine-stereo\n")
        self.assertIn([state.paths.audio_cleanup_script], calls)

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
