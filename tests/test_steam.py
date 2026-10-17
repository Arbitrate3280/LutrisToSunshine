"""Tests for launchers.steam appmanifest parsing and detect hints."""
import os
import tempfile
import unittest
from unittest.mock import patch

from launchers.steam import SteamManifest, list_steam_games, list_steam_selections, parse_appmanifest

MANIFEST = """\
"AppState"
{
\t"appid"\t\t"570"
\t"Universe"\t\t"1"
\t"name"\t\t"Dota 2"
\t"installdir"\t\t"dota 2 beta"
}
"""

EXCLUDED_MANIFEST = """\
"AppState"
{
\t"appid"\t\t"1070560"
\t"name"\t\t"Steam Linux Runtime 1.0 (scout)"
\t"installdir"\t\t"SteamLinuxRuntime"
}
"""


class TestParseAppmanifest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, content):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_parses_appid_name_and_installdir(self):
        manifest = parse_appmanifest(self._write("appmanifest_570.acf", MANIFEST))
        self.assertEqual(manifest, SteamManifest("570", "Dota 2", "dota 2 beta"))

    def test_missing_file_returns_none(self):
        self.assertIsNone(parse_appmanifest(os.path.join(self.tmp.name, "nope.acf")))


class TestSteamDetectHints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        steamapps = os.path.join(self.root, "steamapps")
        os.makedirs(steamapps)
        with open(os.path.join(steamapps, "appmanifest_570.acf"), "w") as f:
            f.write(MANIFEST)
        with open(os.path.join(steamapps, "appmanifest_1070560.acf"), "w") as f:
            f.write(EXCLUDED_MANIFEST)

    def _patched(self):
        return (
            patch(
                "launchers.steam.detect_steam_installation",
                return_value=(True, "native"),
            ),
            patch("launchers.steam.get_steam_root", return_value=self.root),
        )

    def test_games_exclude_runtimes(self):
        detect_patch, root_patch = self._patched()
        with detect_patch, root_patch:
            self.assertEqual(list_steam_games(), [("570", "Dota 2")])

    def test_hints_carry_appid_and_install_dir(self):
        detect_patch, root_patch = self._patched()
        with detect_patch, root_patch:
            selections = list_steam_selections()
        self.assertEqual(len(selections), 1)
        self.assertEqual(selections[0].detect_hints, {
            "steam_appid": 570,
            "install_dir": os.path.join(self.root, "steamapps", "common", "dota 2 beta"),
        })


if __name__ == "__main__":
    unittest.main()
