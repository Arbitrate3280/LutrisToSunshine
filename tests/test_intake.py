"""Tests for the intake submit seam forwarding detect hints."""
import unittest
from unittest.mock import MagicMock, patch

from config.types import GameSelection, LauncherSource
from launchers import intake


class SubmitGamesDetectTests(unittest.TestCase):
    def _run(self, game, **overrides):
        submit = MagicMock()
        kwargs = {
            "download_images": False,
            "api_key": None,
            "default_image": "default.png",
            "download_image_fn": MagicMock(),
            "submit_game_fn": submit,
        }
        kwargs.update(overrides)
        with patch.object(intake, "build_game_command", return_value="launch-cmd"):
            added = intake.submit_games([game], **kwargs)
        return added, submit

    def test_forwards_detect_hints(self):
        hints = {"steam_appid": 570}
        game = GameSelection("570", "Dota", "Steam", LauncherSource("Steam"), hints)
        added, submit = self._run(game)
        self.assertTrue(added)
        submit.assert_called_once_with("Dota", "launch-cmd", "default.png", detect=hints)

    def test_forwards_none_when_no_hints(self):
        game = GameSelection("1", "Game", "Lutris", LauncherSource("Lutris"))
        added, submit = self._run(game)
        self.assertTrue(added)
        submit.assert_called_once_with("Game", "launch-cmd", "default.png", detect=None)

    def test_forwards_detect_hints_with_downloaded_image(self):
        hints = {"exe": "/games/portal/portal"}
        game = GameSelection("1", "Portal", "Lutris", LauncherSource("Lutris"), hints)
        added, submit = self._run(
            game,
            download_images=True,
            api_key="key",
            download_image_fn=lambda name, key: "cover.png",
        )
        self.assertTrue(added)
        submit.assert_called_once_with("Portal", "launch-cmd", "cover.png", detect=hints)

    def test_skips_games_without_a_command(self):
        game = GameSelection("1", "Ghost", "Lutris", LauncherSource("Lutris"))
        submit = MagicMock()
        with patch.object(intake, "build_game_command", return_value=None):
            added = intake.submit_games(
                [game],
                download_images=False,
                api_key=None,
                default_image="default.png",
                download_image_fn=MagicMock(),
                submit_game_fn=submit,
            )
        self.assertFalse(added)
        submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
