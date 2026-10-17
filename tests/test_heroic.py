"""Tests for launchers.heroic installed.json reading and detect hints."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from launchers import heroic
from launchers.heroic import list_heroic_games, list_heroic_selections

ENV_MARKER = {"env_marker": {"key": "HEROIC_APP_NAME"}}


class TestHeroicDetectHints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _paths(self, **files):
        paths = {}
        for runner, data in files.items():
            path = os.path.join(self.tmp.name, f"{runner}.json")
            with open(path, "w") as f:
                json.dump(data, f)
            paths[runner] = path
        return paths

    def _selections(self, paths):
        with patch(
            "launchers.heroic.get_heroic_command", return_value=("heroic", "native")
        ), patch.dict(heroic.HEROIC_PATHS, {"native": paths}):
            return list_heroic_games(), list_heroic_selections()

    def test_legendary_hints(self):
        paths = self._paths(legendary={
            "Sugar": {
                "title": "Sugar",
                "install_path": "/games/sugar",
                "executable": "bin/sugar.exe",
            }
        })
        games, selections = self._selections(paths)
        self.assertEqual(games, [("Sugar", "Sugar", "Heroic", "legendary")])
        self.assertEqual(selections[0].detect_hints, {
            **ENV_MARKER,
            "exe": "/games/sugar/bin/sugar.exe",
            "process_name": "sugar.exe",
            "install_dir": "/games/sugar",
        })

    def test_gog_hints_without_executable(self):
        paths = self._paths(gog={"installed": [{"appName": "123", "install_path": "/games/gog_game"}]})
        games, selections = self._selections(paths)
        self.assertEqual(games, [("123", "gog_game", "Heroic", "gog")])
        self.assertEqual(selections[0].detect_hints, {
            **ENV_MARKER,
            "install_dir": "/games/gog_game",
        })

    def test_sideload_hints(self):
        paths = self._paths(sideload={
            "games": [
                {
                    "app_name": "mygame",
                    "title": "My Game",
                    "install": {"executable": "/opt/mygame/run.sh"},
                }
            ]
        })
        games, selections = self._selections(paths)
        self.assertEqual(games, [("mygame", "My Game", "Heroic", "sideload")])
        self.assertEqual(selections[0].detect_hints, {
            **ENV_MARKER,
            "exe": "/opt/mygame/run.sh",
            "process_name": "run.sh",
        })

    def test_windows_executable_stays_verbatim(self):
        paths = self._paths(legendary={
            "Game": {
                "title": "Game",
                "install_path": "/games/win",
                "executable": "C:\\Games\\win\\game.exe",
            }
        })
        _, selections = self._selections(paths)
        self.assertEqual(selections[0].detect_hints, {
            **ENV_MARKER,
            "exe": "C:\\Games\\win\\game.exe",
            "process_name": "game.exe",
            "install_dir": "/games/win",
        })

    def test_env_marker_alone_when_nothing_else_known(self):
        paths = self._paths(legendary={"NoMeta": {"title": "No Meta"}})
        _, selections = self._selections(paths)
        self.assertEqual(selections[0].detect_hints, ENV_MARKER)


if __name__ == "__main__":
    unittest.main()
