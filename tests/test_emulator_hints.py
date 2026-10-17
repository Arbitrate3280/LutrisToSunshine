"""Detect-hint tests for emulator launchers (RetroArch, Eden, Cemu, Ryubing)."""
import unittest
from unittest.mock import patch

from config.types import RetroArchSource
from launchers import cemu, eden, retroarch, ryubing


class RetroArchHintTests(unittest.TestCase):
    @patch("launchers.retroarch.list_retroarch_games", return_value=[
        ("/roms/a.sfc", "A", RetroArchSource("/cores/snes.so", "snes")),
    ])
    @patch("launchers.retroarch.shutil.which", return_value="/usr/bin/retroarch")
    @patch("launchers.retroarch.get_retroarch_command", return_value="retroarch")
    def test_native_hints(self, command, which, games):
        selection = retroarch.list_retroarch_selections()[0]
        self.assertEqual(selection.detect_hints, {
            "process_name": "retroarch",
            "exe": "/usr/bin/retroarch",
        })

    @patch("launchers.retroarch.list_retroarch_games", return_value=[
        ("/roms/a.sfc", "A", RetroArchSource("/cores/snes.so", "snes")),
    ])
    @patch("launchers.retroarch.get_retroarch_command", return_value="flatpak run org.libretro.RetroArch")
    def test_flatpak_hints(self, command, games):
        selection = retroarch.list_retroarch_selections()[0]
        self.assertEqual(selection.detect_hints, {
            "process_name": "retroarch",
            "env_marker": {"key": "FLATPAK_ID", "value": "org.libretro.RetroArch"},
        })

    @patch("launchers.retroarch.get_retroarch_command", return_value="")
    def test_no_command_no_hints(self, command):
        self.assertIsNone(retroarch._retroarch_detect_hints())


class EdenHintTests(unittest.TestCase):
    @patch("launchers.eden.shutil.which", return_value="/usr/bin/eden")
    @patch("launchers.eden.get_eden_command", return_value="eden")
    def test_native_hints(self, command, which):
        self.assertEqual(eden._eden_detect_hints(), {
            "process_name": "eden",
            "exe": "/usr/bin/eden",
        })

    @patch("launchers.eden.get_eden_command", return_value="flatpak run dev.eden_emu.eden")
    def test_flatpak_hints(self, command):
        self.assertEqual(eden._eden_detect_hints(), {
            "process_name": "eden",
            "env_marker": {"key": "FLATPAK_ID", "value": "dev.eden_emu.eden"},
        })

    @patch("launchers.eden.get_eden_command", return_value="/opt/eden/eden.appimage")
    def test_appimage_hints(self, command):
        self.assertEqual(eden._eden_detect_hints(), {"process_name": "eden"})


class CemuHintTests(unittest.TestCase):
    @patch("launchers.cemu.get_cemu_command", return_value="flatpak run info.cemu.Cemu")
    def test_flatpak_hints(self, command):
        self.assertEqual(cemu._cemu_detect_hints(), {
            "env_marker": {"key": "FLATPAK_ID", "value": "info.cemu.Cemu"},
        })

    @patch("launchers.cemu.shutil.which", return_value="/usr/bin/cemu")
    @patch("launchers.cemu.get_cemu_command", return_value="cemu")
    def test_native_hints(self, command, which):
        self.assertEqual(cemu._cemu_detect_hints(), {"exe": "/usr/bin/cemu"})

    @patch("launchers.cemu.get_cemu_command", return_value="/opt/cemu/cemu.appimage")
    def test_appimage_has_no_hints(self, command):
        self.assertIsNone(cemu._cemu_detect_hints())


class RyubingHintTests(unittest.TestCase):
    @patch("launchers.ryubing.list_ryubing_games", return_value=[("/roms/g.nsp", "G")])
    def test_flatpak_env_marker(self, games):
        selection = ryubing.list_ryubing_selections()[0]
        self.assertEqual(selection.detect_hints, {
            "env_marker": {"key": "FLATPAK_ID", "value": "io.github.ryubing.Ryujinx"},
        })


if __name__ == "__main__":
    unittest.main()
