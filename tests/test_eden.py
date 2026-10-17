"""Tests for launchers.eden qt-config reading and Flatpak support."""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from config.types import GameSelection, LauncherSource
from launchers import eden
from launchers.eden import (
    build_eden_command,
    detect_eden_installation,
    get_eden_game_dirs,
    list_eden_games,
)


def _qt_config(game_dirs):
    entries = ["SDMC", "UserNAND", "SysNAND", *game_dirs]
    lines = [f"Paths\\gamedirs\\size={len(entries)}"]
    for index, path in enumerate(entries, start=1):
        lines.append(f"Paths\\gamedirs\\{index}\\path={path}")
    return "\n".join(lines) + "\n"


class TestEdenLauncher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write_config(self, name, game_dirs):
        path = os.path.join(self.tmp.name, name, "qt-config.ini")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write(_qt_config(game_dirs))
        return path

    def _touch_game(self, directory, name):
        path = os.path.join(directory, name)
        os.makedirs(directory, exist_ok=True)
        with open(path, "w") as f:
            f.write("nsp")
        return path

    def test_reads_game_dirs_from_every_existing_config(self):
        flatpak_dir = os.path.join(self.tmp.name, "portal-titles")
        native_dir = os.path.join(self.tmp.name, "native-titles")
        flatpak_config = self._write_config("flatpak", [flatpak_dir])
        native_config = self._write_config("native", [native_dir, flatpak_dir])
        with patch.object(
            eden, "get_eden_qt_config_paths", return_value=[flatpak_config, native_config]
        ):
            self.assertEqual(get_eden_game_dirs(), [flatpak_dir, native_dir])

    def test_flatpak_config_is_first_candidate(self):
        home = self.tmp.name
        xdg_config = os.path.join(self.tmp.name, "xdg")
        with patch.dict(os.environ, {"HOME": home, "XDG_CONFIG_HOME": xdg_config}):
            paths = eden.get_eden_qt_config_paths()
        self.assertEqual(
            paths[0],
            os.path.join(
                home, ".var", "app", "dev.eden_emu.eden", "config", "eden", "qt-config.ini"
            ),
        )
        self.assertEqual(paths[1], os.path.join(xdg_config, "eden", "qt-config.ini"))

    def test_skips_virtual_gamedirs(self):
        config = self._write_config("flatpak", [])
        with patch.object(eden, "get_eden_qt_config_paths", return_value=[config]):
            self.assertEqual(get_eden_game_dirs(), [])

    def test_lists_base_games_and_skips_updates_and_dlc(self):
        games_dir = os.path.join(self.tmp.name, "titles")
        base = self._touch_game(
            games_dir, "Super Mario Bros. Wonder[010015100B514000][1.0.0][0][16.0.3].nsp"
        )
        self._touch_game(games_dir, "Super Mario Bros. Wonder[update][16.1.0].nsp")
        self._touch_game(games_dir, "Super Mario Bros. Wonder[010015100B514800][DLC].nca")
        config = self._write_config("flatpak", [games_dir])
        with patch.object(eden, "get_eden_qt_config_paths", return_value=[config]):
            self.assertEqual(
                list_eden_games(), [(base, "Super Mario Bros. Wonder")]
            )

    def test_build_command_uses_flatpak(self):
        game = GameSelection("/titles/DressMaker.nsp", "DressMaker", "Eden", LauncherSource("Eden"))
        flatpak_command = "flatpak run dev.eden_emu.eden"
        with patch.object(eden, "get_eden_command", return_value=flatpak_command):
            self.assertEqual(
                build_eden_command(game),
                "flatpak run dev.eden_emu.eden -f -g /titles/DressMaker.nsp",
            )

    def test_detect_installation_prefers_flatpak(self):
        with patch.object(eden, "run_command", return_value=MagicMock(returncode=0)), \
                patch.object(eden.shutil, "which", return_value=None):
            self.assertEqual(eden.get_eden_command(), "flatpak run dev.eden_emu.eden")
            self.assertTrue(detect_eden_installation())

    def test_detect_installation_falls_back_to_native(self):
        with patch.object(eden, "run_command", return_value=MagicMock(returncode=1)), \
                patch.object(eden.shutil, "which", return_value="/usr/bin/eden"):
            self.assertEqual(eden.get_eden_command(), "eden")

    def test_detect_installation_without_install(self):
        with patch.object(eden, "run_command", return_value=MagicMock(returncode=1)), \
                patch.object(eden.shutil, "which", return_value=None), \
                patch.object(eden, "_candidate_home_dirs", return_value=[]):
            self.assertFalse(detect_eden_installation())


if __name__ == "__main__":
    unittest.main()
