"""Tests for launchers.faugus games.json reading and detect hints."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from launchers.faugus import list_faugus_selections


class TestFaugusDetectHints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _selections(self, games):
        games_path = os.path.join(self.tmp.name, "games.json")
        with open(games_path, "w") as f:
            json.dump(games, f)
        paths = {
            "games_json": games_path,
            "config_json": os.path.join(self.tmp.name, "config.json"),
        }
        with patch("launchers.faugus.get_faugus_paths", return_value=paths):
            return list_faugus_selections()

    def test_host_path_hints(self):
        selections = self._selections([{
            "gameid": "cp2077",
            "title": "Cyberpunk 2077",
            "path": "/games/cyberpunk/bin/x64/Cyberpunk2077.exe",
            "prefix": "/prefixes/cp2077",
        }])
        self.assertEqual(selections[0].detect_hints, {
            "exe": "/games/cyberpunk/bin/x64/Cyberpunk2077.exe",
            "process_name": "Cyberpunk2077.exe",
            "install_dir": "/games/cyberpunk/bin/x64",
        })

    def test_windows_path_keeps_process_name_only(self):
        selections = self._selections([{
            "gameid": "win",
            "title": "Win Game",
            "path": "C:\\Games\\WinGame\\game.exe",
            "prefix": "/prefixes/win",
        }])
        self.assertEqual(selections[0].detect_hints, {"process_name": "game.exe"})


if __name__ == "__main__":
    unittest.main()
