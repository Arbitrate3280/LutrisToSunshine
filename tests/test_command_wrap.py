"""Direct tests for display/command_wrap wrapping/unwrapping logic."""

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from display import command_wrap


SCRIPT = command_wrap.get_launch_app_script()
PREP_SCRIPT = command_wrap.get_headless_prep_script()


class WrapCommandTests(unittest.TestCase):
    def test_roundtrip(self):
        self.assertEqual(command_wrap.unwrap_command(command_wrap.wrap_command("echo hi")), "echo hi")

    def test_roundtrip_preserves_origin_and_timeout(self):
        wrapped = command_wrap.wrap_command("flatpak run app", origin="steam", exit_timeout=12)
        self.assertEqual(command_wrap.get_wrapped_command_origin(wrapped), "steam")
        self.assertEqual(command_wrap.get_wrapped_command_exit_timeout(wrapped), 12)

    def test_wrap_is_idempotent(self):
        once = command_wrap.wrap_command("x")
        self.assertEqual(command_wrap.wrap_command(once), once)

    def test_none_and_empty_passthrough(self):
        self.assertIsNone(command_wrap.wrap_command(None))
        self.assertEqual(command_wrap.wrap_command(""), "")
        self.assertIsNone(command_wrap.unwrap_command(None))
        self.assertEqual(command_wrap.unwrap_command(""), "")

    def test_unwrapped_passthrough(self):
        self.assertEqual(command_wrap.unwrap_command("plain command"), "plain command")
        self.assertFalse(command_wrap.is_wrapped_command("plain command"))

    def test_exit_timeout_default_for_unwrapped(self):
        self.assertEqual(command_wrap.get_wrapped_command_exit_timeout("plain command"), 5)
        self.assertEqual(command_wrap.get_wrapped_command_exit_timeout("plain command", default=9), 9)

    def test_malformed_base64_returns_original(self):
        bad = f"{SCRIPT} cmd 5 !!!notbase64!!!"
        self.assertEqual(command_wrap.unwrap_command(bad), bad)

    def test_legacy_two_part_format_decodes(self):
        encoded = base64.b64encode(b"legacy cmd").decode("ascii")
        wrapped = f"{SCRIPT} {encoded}"
        self.assertEqual(command_wrap.unwrap_command(wrapped), "legacy cmd")
        self.assertEqual(command_wrap.get_wrapped_command_origin(wrapped), "cmd")


class HeadlessPrepWrapTests(unittest.TestCase):
    def test_roundtrip(self):
        self.assertEqual(
            command_wrap.unwrap_headless_prep_command(
                command_wrap.wrap_headless_prep_command("prep --flag")
            ),
            "prep --flag",
        )

    def test_wrap_is_idempotent(self):
        once = command_wrap.wrap_headless_prep_command("prep")
        self.assertEqual(command_wrap.wrap_headless_prep_command(once), once)

    def test_unwrapped_passthrough(self):
        self.assertEqual(command_wrap.unwrap_headless_prep_command("plain"), "plain")
        self.assertFalse(command_wrap.is_headless_prep_wrapped("plain"))


class GetAppPrepCommandsTests(unittest.TestCase):
    def test_disabled_without_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(command_wrap, "DISPLAY_STATE_PATH", Path(tmp) / "missing.json"):
                with mock.patch.object(command_wrap, "LEGACY_STATE_PATH", Path(tmp) / "legacy-missing.json"):
                    self.assertEqual(command_wrap.get_app_prep_commands(), [])

    def test_legacy_state_file_enables(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "virtualdisplay.json"
            legacy.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            with mock.patch.object(command_wrap, "DISPLAY_STATE_PATH", Path(tmp) / "missing.json"):
                with mock.patch.object(command_wrap, "LEGACY_STATE_PATH", legacy):
                    self.assertNotEqual(command_wrap.get_app_prep_commands(), [])

    def test_corrupt_primary_does_not_fall_through_to_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = Path(tmp) / "display.json"
            primary.write_text("not json", encoding="utf-8")
            legacy = Path(tmp) / "virtualdisplay.json"
            legacy.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            with mock.patch.object(command_wrap, "DISPLAY_STATE_PATH", primary):
                with mock.patch.object(command_wrap, "LEGACY_STATE_PATH", legacy):
                    self.assertEqual(command_wrap.get_app_prep_commands(), [])

    def test_non_dict_state_file_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = Path(tmp) / "display.json"
            primary.write_text("[]", encoding="utf-8")
            with mock.patch.object(command_wrap, "DISPLAY_STATE_PATH", primary):
                self.assertEqual(command_wrap.get_app_prep_commands(), [])

    def test_enabled_with_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "display.json"
            state.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            with mock.patch.object(command_wrap, "DISPLAY_STATE_PATH", state):
                self.assertEqual(
                    command_wrap.get_app_prep_commands(),
                    [{"do": str(command_wrap.BIN_ROOT / "lutristosunshine-set-resolution.sh"),
                      "undo": str(command_wrap.BIN_ROOT / "lutristosunshine-reset-resolution.sh")}],
                )


if __name__ == "__main__":
    unittest.main()
