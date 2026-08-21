"""Tests for source ignores: intake filtering, the ignore menu, and
connection-settings persistence in the tool settings store."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import settings as tool_settings
from launchers import intake
from lutristosunshine import ignore_sources_flow
from sunshine import installation
from sunshine.connection import CONNECTION


def _detected(**overrides):
    detected = {name: False for name in (
        "Steam", "Lutris", "Heroic", "Bottles",
        "Faugus", "Ryubing", "RetroArch", "Eden",
    )}
    detected.update(overrides)
    return detected


class ApplyIgnoresTests(unittest.TestCase):
    def test_excluded_detected_source_is_filtered_and_reported(self):
        filtered, skipped = intake.apply_ignores(
            _detected(Steam=True, RetroArch=True),
            ["RetroArch"],
        )
        self.assertTrue(filtered["Steam"])
        self.assertFalse(filtered["RetroArch"])
        self.assertEqual(skipped, ["RetroArch"])

    def test_matching_is_case_insensitive(self):
        filtered, skipped = intake.apply_ignores(
            _detected(RetroArch=True),
            ["retroarch"],
        )
        self.assertFalse(filtered["RetroArch"])
        self.assertEqual(skipped, ["RetroArch"])

    def test_undetected_sources_are_never_skipped(self):
        filtered, skipped = intake.apply_ignores(_detected(Steam=True), [
            "RetroArch", "Eden",
        ])
        self.assertTrue(filtered["Steam"])
        self.assertEqual(skipped, [])

    def test_non_list_exclusions_treated_as_empty(self):
        filtered, skipped = intake.apply_ignores(_detected(Steam=True), None)
        self.assertTrue(filtered["Steam"])
        self.assertEqual(skipped, [])


class IgnoreSourcesFlowTests(unittest.TestCase):
    def _run_flow(self, inputs, detected):
        with tempfile.TemporaryDirectory() as raw:
            with patch.object(tool_settings, "SETTINGS_DIR", raw), patch.object(
                intake, "detect_launchers", return_value=detected
            ), patch("builtins.input", side_effect=inputs):
                ignore_sources_flow()
            return json.loads(
                (Path(raw) / "settings.json").read_text(encoding="utf-8")
            )

    def test_toggle_saves_immediately_then_exit(self):
        saved = self._run_flow(
            ["2", ""],
            _detected(Steam=True, Lutris=True),
        )
        self.assertEqual(saved["ignored_sources"], ["Lutris"])

    def test_toggle_twice_un_excludes(self):
        saved = self._run_flow(
            ["1", "1", ""],
            _detected(Steam=True),
        )
        self.assertEqual(saved["ignored_sources"], [])

    def test_invalid_input_reprompts(self):
        saved = self._run_flow(
            ["99", "1", ""],
            _detected(Steam=True),
        )
        self.assertEqual(saved["ignored_sources"], ["Steam"])

    def test_no_sources_detected_returns_early(self):
        with tempfile.TemporaryDirectory() as raw:
            with patch.object(tool_settings, "SETTINGS_DIR", raw), patch.object(
                intake, "detect_launchers", return_value=_detected()
            ), patch("builtins.input", side_effect=AssertionError("should not prompt")):
                self.assertEqual(ignore_sources_flow(), 0)
            self.assertFalse(Path(raw, "settings.json").exists())


class ConnectionPersistenceTests(unittest.TestCase):
    def test_save_writes_servers_to_settings_store(self):
        with tempfile.TemporaryDirectory() as raw:
            with patch.object(tool_settings, "SETTINGS_DIR", raw):
                CONNECTION.save_api_connection("192.168.1.10", 47990, server_name="sunshine")
                host, port = CONNECTION.get_api_connection(server_name="sunshine")

            saved = json.loads(
                (Path(raw) / "settings.json").read_text(encoding="utf-8")
            )

        self.assertEqual(host, "192.168.1.10")
        self.assertEqual(port, 47990)
        self.assertEqual(saved["servers"]["sunshine"], {"host": "192.168.1.10", "port": 47990})

    def test_legacy_server_connection_file_still_read(self):
        with tempfile.TemporaryDirectory() as raw:
            legacy_root = Path(raw) / "legacy-root"
            legacy_root.mkdir()
            (legacy_root / "server_connection.json").write_text(
                json.dumps({"sunshine": {"host": "10.0.0.5", "port": 47991}}),
                encoding="utf-8",
            )
            with patch.object(tool_settings, "SETTINGS_DIR", raw), patch.object(
                CONNECTION, "get_config_root", return_value=str(legacy_root)
            ):
                host, port = CONNECTION.get_api_connection(server_name="sunshine")

        self.assertEqual((host, port), ("10.0.0.5", 47991))

    def test_new_store_wins_over_legacy_connection(self):
        with tempfile.TemporaryDirectory() as raw:
            legacy_root = Path(raw) / "legacy-root"
            legacy_root.mkdir()
            (legacy_root / "server_connection.json").write_text(
                json.dumps({"sunshine": {"host": "10.0.0.5", "port": 47991}}),
                encoding="utf-8",
            )
            with patch.object(tool_settings, "SETTINGS_DIR", raw), patch.object(
                CONNECTION, "get_config_root", return_value=str(legacy_root)
            ):
                CONNECTION.save_api_connection("0.0.0.0", 1234, server_name="sunshine")
                host, port = CONNECTION.get_api_connection(server_name="sunshine")

        self.assertEqual((host, port), ("0.0.0.0", 1234))


class MainFlowIgnoreWiringTests(unittest.TestCase):
    """Drive main() past the ignore filtering so wiring regressions surface."""

    def _run_main(self, detected, games=None, ignored=None):
        """Run main(); returns stdout. Uses self.settings_dir for the store."""
        from config.types import GameSelection
        from lutristosunshine import main

        facts = installation.SunshineInstallation(
            installed=True,
            install_type="native",
            detected_types=["native"],
            unit=installation.SunshineServiceUnitProbe(unit_name=installation.SUNSHINE_UNIT),
            packages=installation.SunshinePackageProbes(detected_types=["native"]),
        )
        fake_games = [
            GameSelection(game_id="1", game_name="Fake Game", display_source="Steam", source="steam")
        ] if games is None else games
        stdout = io.StringIO()
        with patch.object(tool_settings, "SETTINGS_DIR", self.settings_dir), patch.object(
            installation, "resolve_installation", return_value=facts
        ), patch.object(
            CONNECTION, "get_running_servers", return_value=["sunshine"]
        ), patch.object(
            CONNECTION, "is_server_running", return_value=True
        ), patch.object(
            CONNECTION, "ensure_authenticated", return_value=True
        ), patch.object(
            CONNECTION, "get_covers_path", return_value=tempfile.mkdtemp()
        ), patch.object(
            intake, "detect_launchers", return_value=detected
        ), patch.object(
            intake, "collect_games", return_value=fake_games
        ), patch(
            "lutristosunshine.catalog.get_existing_apps", return_value=[]
        ), patch(
            "lutristosunshine.get_user_selection", return_value=[]
        ), patch("builtins.input", return_value="0"), contextlib.redirect_stdout(stdout):
            if ignored is not None:
                tool_settings.update_setting("ignored_sources", ignored)
            main([])
        return stdout.getvalue()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings_dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_main_reaches_game_listing_with_ignored_source(self):
        detected = {name: False for name in (
            "Steam", "Lutris", "Heroic", "Bottles",
            "Faugus", "Ryubing", "RetroArch", "Eden",
        )}
        detected["Steam"] = True
        detected["RetroArch"] = True
        output = self._run_main(detected, ignored=["RetroArch"])

        self.assertIn("Ignored: RetroArch (manage with 'lutristosunshine ignore')", output)
        self.assertIn("Games found in Steam:", output)

    def test_main_reports_all_detected_sources_ignored(self):
        detected = {name: False for name in (
            "Steam", "Lutris", "Heroic", "Bottles",
            "Faugus", "Ryubing", "RetroArch", "Eden",
        )}
        detected["RetroArch"] = True
        output = self._run_main(detected, ignored=["RetroArch"])

        self.assertIn("All detected sources are ignored.", output)


if __name__ == "__main__":
    unittest.main()
