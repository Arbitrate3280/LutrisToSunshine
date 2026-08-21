"""Tests for launchers.bottles bottle.yml reading."""
import os
import tempfile
import unittest
from unittest.mock import patch

from launchers.bottles import list_bottles_games


def _make_bottle(root, name, config_text):
    bottle_dir = os.path.join(root, "bottles", name)
    os.makedirs(bottle_dir)
    with open(os.path.join(bottle_dir, "bottle.yml"), "w") as f:
        f.write(config_text)


GAMING_YML = """\
Name: Gaming
Environment: gaming
External_Programs:
  abc123:
    name: Portal 2
    path: "C:\\\\Program Files\\\\Portal2\\\\portal2.exe"
    executable: portal2.exe
  def456:
    name: Old Game
    removed: true
"""
APPLICATION_YML = """\
Name: Work
Environment: application
External_Programs:
  xyz789:
    name: Not A Game
"""


class TestListBottlesGames(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_lists_gaming_programs_skips_removed_and_non_gaming(self):
        flatpak_root = os.path.join(self.tmp.name, "fp")
        _make_bottle(flatpak_root, "Gaming", GAMING_YML)
        _make_bottle(flatpak_root, "Work", APPLICATION_YML)
        with patch("launchers.bottles._bottles_data_roots", return_value=[flatpak_root]):
            self.assertEqual(
                list_bottles_games(), [("Portal 2", "Portal 2", "Bottles", "Gaming")]
            )

    def test_skips_corrupt_yml(self):
        root = os.path.join(self.tmp.name, "fp")
        bottle_dir = os.path.join(root, "bottles", "Broken")
        os.makedirs(bottle_dir)
        with open(os.path.join(bottle_dir, "bottle.yml"), "w") as f:
            f.write(":::: not yaml {{{")
        with patch("launchers.bottles._bottles_data_roots", return_value=[root]):
            self.assertEqual(list_bottles_games(), [])

    def test_flatpak_root_wins_on_name_clash(self):
        flatpak_root = os.path.join(self.tmp.name, "fp")
        native_root = os.path.join(self.tmp.name, "native")
        _make_bottle(flatpak_root, "Gaming", GAMING_YML)
        _make_bottle(native_root, "Gaming", GAMING_YML.replace("Portal 2", "Native Game"))
        with patch(
            "launchers.bottles._bottles_data_roots",
            return_value=[flatpak_root, native_root],
        ):
            self.assertEqual(
                list_bottles_games(), [("Portal 2", "Portal 2", "Bottles", "Gaming")]
            )


if __name__ == "__main__":
    unittest.main()
