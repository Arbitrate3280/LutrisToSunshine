"""Tests for scoped flatpak audio-env injection.

Regression coverage for the bug where the stream marker (`lutristosunshine.stream`)
leaked into the global systemd/DBus activation environment via the Flatpak portal
handoff, permanently tagging host apps (browsers, speech-dispatcher, ...) as games
and stranding their audio on the managed sink.

Tests run the actual rendered heredoc Python code (with @FLAGS@/@VALUE_OPTS@
substituted from module constants) via subprocess, matching how the shell
function executes at runtime.
"""

import shlex
import subprocess
import sys
import unittest
from unittest.mock import patch

from display import manager


def _rendered_inject_heredoc_py() -> str:
    """Generate a rendered script and extract the inject heredoc Python source.

    Renders both prep and launch templates, extracts the inject heredoc
    Python from each, and asserts they are byte-identical.
    """
    state = manager._default_state()
    templates = manager._script_templates(state)
    py_codes = {}
    for path, content in templates.items():
        name = str(path)
        if "headless-prep" in name or "launch-app" in name:
            func_start = content.index("inject_flatpak_audio_env()")
            marker = "<<'PY'\n"
            start = content.index(marker, func_start) + len(marker)
            end = content.index("\nPY\n", start)
            py_codes[name] = content[start:end]
    if len(py_codes) < 2:
        raise RuntimeError(f"expected 2 scripts, got {len(py_codes)}: {list(py_codes)}")
    names = list(py_codes.keys())
    assert py_codes[names[0]] == py_codes[names[1]], (
        f"heredoc mismatch: {names[0]} vs {names[1]}"
    )
    return py_codes[names[0]]


def _run_inject(cmd: str, sink: str, py_code: str) -> str:
    """Run the rendered heredoc Python code via subprocess."""
    r = subprocess.run(
        [sys.executable, "-c", py_code, cmd, sink,
         manager.AUDIO_STREAM_PULSE_PROP, manager.AUDIO_STREAM_PIPEWIRE_PROPS],
        capture_output=True, text=True, timeout=5,
    )
    return r.stdout.strip()



class InjectFlatpakAudioEnvTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(manager._svc, "sunshine_unit", return_value="sunshine"), \
             patch.object(manager._svc, "sunshine_binary", return_value="/usr/bin/sunshine"):
            cls._py_code = _rendered_inject_heredoc_py()

    def setUp(self):
        self.sink = "lts-sunshine-stereo"

    def _has(self, tokens, key, value):
        return f"--env={key}={value}" in tokens

    def test_preserves_flatpak_spawn_host_prefix(self):
        cmd = "flatpak-spawn --host flatpak run com.example.Game"
        tokens = shlex.split(_run_inject(cmd, self.sink, self._py_code))
        self.assertEqual(tokens[:3], ["flatpak-spawn", "--host", "flatpak"])
        self.assertEqual(tokens[3], "run")
        self.assertEqual(tokens[-1], "com.example.Game")

    def test_preserves_existing_flatpak_options(self):
        cmd = "flatpak run --branch=stable com.foo.Bar"
        tokens = shlex.split(_run_inject(cmd, self.sink, self._py_code))
        self.assertIn("--branch=stable", tokens)
        self.assertEqual(tokens[-1], "com.foo.Bar")

    def test_preserves_command_option_appid_and_args(self):
        cmd = 'flatpak run --command=launcher com.x.Y --arg1 "value with space"'
        tokens = shlex.split(_run_inject(cmd, self.sink, self._py_code))
        self.assertIn("--command=launcher", tokens)
        self.assertIn("com.x.Y", tokens)
        self.assertEqual(tokens[-3:], ["com.x.Y", "--arg1", "value with space"])

    def test_injects_all_three_audio_env_vars(self):
        cmd = "flatpak run com.test.App"
        tokens = shlex.split(_run_inject(cmd, self.sink, self._py_code))
        self.assertTrue(self._has(tokens, "PULSE_SINK", self.sink), tokens)
        self.assertTrue(
            self._has(tokens, "PULSE_PROP", manager.AUDIO_STREAM_PULSE_PROP), tokens
        )
        self.assertTrue(
            self._has(tokens, "PIPEWIRE_PROPS", manager.AUDIO_STREAM_PIPEWIRE_PROPS),
            tokens,
        )

    def test_marker_value_is_game(self):
        cmd = "flatpak run com.test.App"
        tokens = shlex.split(_run_inject(cmd, self.sink, self._py_code))
        joined = " ".join(tokens)
        self.assertIn('lutristosunshine.stream = "game"', joined)

    def test_non_flatpak_passthrough(self):
        for cmd in ["steam-native-game", "/usr/bin/foo --bar", "", "flatpak-spawn --host echo hi"]:
            self.assertEqual(_run_inject(cmd, self.sink, self._py_code), cmd)

    def test_idempotent(self):
        cmd = "flatpak-spawn --host flatpak run com.example.Game"
        once = _run_inject(cmd, self.sink, self._py_code)
        twice = _run_inject(once, self.sink, self._py_code)
        self.assertEqual(once, twice)

    def test_user_pulse_sink_override_is_replaced(self):
        # A user-set --env=PULSE_SINK=custom must be stripped and replaced
        # with the managed sink; the marker must be injected too.
        cmd = "flatpak run --env=PULSE_SINK=custom com.game"
        tokens = shlex.split(_run_inject(cmd, self.sink, self._py_code))
        self.assertTrue(self._has(tokens, "PULSE_SINK", self.sink))
        self.assertTrue(self._has(tokens, "PULSE_PROP", manager.AUDIO_STREAM_PULSE_PROP))
        self.assertTrue(self._has(tokens, "PIPEWIRE_PROPS", manager.AUDIO_STREAM_PIPEWIRE_PROPS))
        # The user's custom value must be gone.
        self.assertNotIn("--env=PULSE_SINK=custom", tokens)

    def test_user_override_space_form_stripped(self):
        # --env PULSE_SINK=custom (space form, two tokens) is also stripped.
        cmd = "flatpak run --env PULSE_SINK=custom --env PULSE_SERVER=keep com.game"
        tokens = shlex.split(_run_inject(cmd, self.sink, self._py_code))
        self.assertTrue(self._has(tokens, "PULSE_SINK", self.sink))
        self.assertNotIn("PULSE_SINK=custom", tokens)
        self.assertIn("--env", tokens)
        self.assertIn("PULSE_SERVER=keep", tokens)

    def test_app_args_after_app_id_preserved(self):
        # --env after the app_id is an app argument, not a Flatpak option.
        cmd = "flatpak run com.game --env PULSE_SINK=apparg"
        tokens = shlex.split(_run_inject(cmd, self.sink, self._py_code))
        self.assertTrue(self._has(tokens, "PULSE_SINK", self.sink))
        # The app's --env after app_id must survive.
        self.assertIn("PULSE_SINK=apparg", tokens)

    def test_unparseable_passthrough(self):
        # Unbalanced quote -> shlex fails -> unchanged.
        cmd = 'flatpak run com.x.Y "unterminated'
        self.assertEqual(_run_inject(cmd, self.sink, self._py_code), cmd)


class PortalEnvKeysLeakGuardTests(unittest.TestCase):
    """Ensure the audio vars never return to the global activation handoff."""

    def test_audio_vars_not_in_portal_env_keys(self):
        for key in ("PULSE_SINK", "PULSE_PROP", "PIPEWIRE_PROPS"):
            self.assertNotIn(key, manager.FLATPAK_PORTAL_ENV_KEYS)


if __name__ == "__main__":
    unittest.main()
