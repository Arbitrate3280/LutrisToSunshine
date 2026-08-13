import subprocess
import unittest
from unittest.mock import patch

from config.types import GameSelection, LauncherSource, RetroArchSource
from display.diagnostics import DisplaySnapshot, DoctorCheck, DoctorReport, as_display_snapshot
from launchers import intake
from utils.utils import run_command


class ArchitectureSeamTests(unittest.TestCase):
    def test_intake_filters_existing_duplicates_and_invalid_retroarch(self) -> None:
        games = [
            GameSelection("1", "Same Game", "Bottles", LauncherSource("Bottle")),
            GameSelection("2", " same   game ", "Steam", LauncherSource("Steam")),
            GameSelection("3", "Old Game", "Lutris", LauncherSource("Lutris")),
            GameSelection("4", "Retro", "RetroArch", RetroArchSource("DETECT", "")),
        ]

        result = intake.select_games(games, range(len(games)), ["Old Game"])

        self.assertEqual([game.game_name for game in result.games], [" same   game "])
        self.assertEqual(result.skipped_duplicates[0][0].game_name, "Same Game")
        self.assertEqual(result.invalid_games[0][0].game_name, "Retro")

    @patch("launchers.steam.get_steam_command", return_value="steam")
    def test_registry_command_builder_stays_in_launcher_intake(self, _get_command) -> None:
        game = GameSelection("42", "Game", "Steam", LauncherSource("Steam"))
        self.assertEqual(intake.build_game_command(game), "steam steam://run/42")

    def test_diagnostics_reports_are_typed_with_legacy_read_compatibility(self) -> None:
        snapshot = as_display_snapshot({"configured": True, "status_summary": "READY"})
        report = DoctorReport("healthy", [DoctorCheck("Setup", "pass", "ok")], "done")

        self.assertIsInstance(snapshot, DisplaySnapshot)
        self.assertTrue(snapshot.configured)
        self.assertEqual(snapshot["status_summary"], "READY")
        self.assertEqual(report["checks"][0]["status"], "pass")

    def test_command_runner_requires_argv_and_does_not_enable_shell(self) -> None:
        completed = subprocess.CompletedProcess(["true"], 0, "", "")
        with patch("utils.utils.subprocess.run", return_value=completed) as run:
            self.assertIs(run_command(["true"]), completed)
        self.assertNotIn("shell", run.call_args.kwargs)

        with self.assertRaises(TypeError):
            run_command("true")  # type: ignore[arg-type]

    @patch("utils.utils.subprocess.run", side_effect=FileNotFoundError("flatpak"))
    def test_command_runner_reports_missing_executable_without_raising(self, _run) -> None:
        result = run_command(["flatpak", "info", "missing"])
        self.assertEqual(result.returncode, 127)


if __name__ == "__main__":
    unittest.main()
