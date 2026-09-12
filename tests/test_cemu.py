"""Tests for launchers.cemu settings.xml parsing and title scanning."""
import os
import tempfile
import unittest
from unittest.mock import patch

from config.types import GameSelection, LauncherSource
from launchers import cemu
from launchers.cemu import (
    build_cemu_command,
    detect_cemu_installation,
    get_cemu_game_dirs,
    list_cemu_games,
)


SETTINGS_XML = """\
<content>
  <GamePaths>
    <Entry>{games_dir}</Entry>
  </GamePaths>
  <GameCache>
    <Entry>
      <path>{games_dir}/breath-of-the-wild.wua</path>
      <title_id>00050000101c9400</title_id>
      <name>sdk</name>
      <custom_name>The Legend of Zelda: Breath of the Wild</custom_name>
      <favorite>1</favorite>
    </Entry>
  </GameCache>
</content>
"""


def _make_title_folder(root, name="Mario Kart 8"):
    title_dir = os.path.join(root, name)
    for sub in ("content", "code", "meta"):
        os.makedirs(os.path.join(title_dir, sub))
    with open(os.path.join(title_dir, "code", "game.rpx"), "w") as f:
        f.write("rpx")
    return title_dir


class TestCemuLauncher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.games_dir = os.path.join(self.tmp.name, "games")
        os.makedirs(self.games_dir)

        self.settings_path = os.path.join(self.tmp.name, "settings.xml")
        with open(self.settings_path, "w") as f:
            f.write(SETTINGS_XML.format(games_dir=self.games_dir))

        patcher = patch.object(
            cemu, "get_cemu_settings_paths", return_value=[self.settings_path]
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _touch(self, *parts):
        path = os.path.join(*parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("data")
        return path

    def test_reads_game_dirs_from_settings(self):
        self.assertEqual(get_cemu_game_dirs(), [self.games_dir])

    def test_lists_files_prefers_game_cache_names(self):
        wua = self._touch(self.games_dir, "breath-of-the-wild.wua")
        self._touch(self.games_dir, "eliti-force.wux")
        games = list_cemu_games()
        self.assertIn((wua, "The Legend of Zelda: Breath of the Wild"), games)
        by_path = dict(games)
        self.assertEqual(by_path[os.path.join(self.games_dir, "eliti-force.wux")], "eliti-force")

    def test_lists_extracted_title_folder_and_prunes_system_dirs(self):
        title_dir = _make_title_folder(self.games_dir)
        self._touch(title_dir, "code", "sneaky.wua")
        games = list_cemu_games()
        self.assertIn((title_dir, "Mario Kart 8"), games)
        self.assertFalse([path for path, _ in games if "/content/" in path or "/code/" in path])

    def test_missing_settings_and_dirs_yield_no_games(self):
        with patch.object(cemu, "get_cemu_settings_paths", return_value=[]):
            self.assertEqual(list_cemu_games(), [])

    def test_build_command_uses_fullscreen_and_game_flags(self):
        game = GameSelection("/games/z Zelda.wua", "Zelda", "Cemu", LauncherSource("Cemu"))
        with patch.object(cemu, "get_cemu_command", return_value="flatpak run info.cemu.Cemu"):
            self.assertEqual(
                build_cemu_command(game),
                "flatpak run info.cemu.Cemu -f -g '/games/z Zelda.wua'",
            )

    def test_build_command_without_install_returns_none(self):
        game = GameSelection("/games/z.wua", "Z", "Cemu", LauncherSource("Cemu"))
        with patch.object(cemu, "get_cemu_command", return_value=""):
            self.assertIsNone(build_cemu_command(game))

    def test_detect_installation(self):
        with patch.object(cemu, "get_cemu_command", return_value="cemu"):
            self.assertTrue(detect_cemu_installation())
        with patch.object(cemu, "get_cemu_command", return_value=""):
            self.assertFalse(detect_cemu_installation())


if __name__ == "__main__":
    unittest.main()
