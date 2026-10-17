"""Tests for launchers.lutris PGA database reading."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from launchers.lutris import list_lutris_games, list_lutris_selections


def _make_pga_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE games (id INTEGER PRIMARY KEY, name TEXT, executable TEXT,"
        " directory TEXT, configpath TEXT)"
    )
    conn.executemany(
        "INSERT INTO games (id, name, executable, directory, configpath)"
        " VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


class TestListLutrisGames(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_reads_games_from_pga_db(self):
        db_path = os.path.join(self.tmp.name, "pga.db")
        _make_pga_db(db_path, [(1, "Portal", None, None, None), (2, "Half-Life 2", None, None, None)])
        with patch("launchers.lutris._pga_db_candidates", return_value=[db_path]):
            self.assertEqual(
                list_lutris_games(), [("1", "Portal"), ("2", "Half-Life 2")]
            )

    def test_skips_corrupt_db_and_falls_through(self):
        corrupt = os.path.join(self.tmp.name, "corrupt.db")
        with open(corrupt, "w") as f:
            f.write("not a database")
        good = os.path.join(self.tmp.name, "pga.db")
        _make_pga_db(good, [(5, "Doom", None, None, None)])
        with patch(
            "launchers.lutris._pga_db_candidates", return_value=[corrupt, good]
        ):
            self.assertEqual(list_lutris_games(), [("5", "Doom")])

    def test_returns_empty_when_no_db(self):
        missing = os.path.join(self.tmp.name, "missing.db")
        with patch("launchers.lutris._pga_db_candidates", return_value=[missing]):
            self.assertEqual(list_lutris_games(), [])


class TestLutrisDetectHints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _selections(self, row):
        db_path = os.path.join(self.tmp.name, "pga.db")
        _make_pga_db(db_path, [row])
        with patch("launchers.lutris._pga_db_candidates", return_value=[db_path]):
            return list_lutris_selections()

    def test_uses_db_executable_and_directory(self):
        game = self._selections((1, "KOTOR", "/games/kotor/kotor.exe", "/games/kotor", None))[0]
        self.assertEqual(game.detect_hints, {
            "exe": "/games/kotor/kotor.exe",
            "process_name": "kotor.exe",
            "install_dir": "/games/kotor",
        })

    def test_yml_executable_resolves_against_wine_prefix(self):
        yml_path = os.path.join(self.tmp.name, "game.yml")
        with open(yml_path, "w") as f:
            f.write(
                "game:\n"
                "  exe: drive_c/Games/Portal2/portal2.exe\n"
                "wine:\n"
                "  prefix: /games/prefixes/portal2\n"
            )
        game = self._selections((7, "Portal 2", None, None, yml_path))[0]
        self.assertEqual(game.detect_hints, {
            "exe": "/games/prefixes/portal2/drive_c/Games/Portal2/portal2.exe",
            "process_name": "portal2.exe",
            "install_dir": "/games/prefixes/portal2/drive_c/Games/Portal2",
        })

    def test_yml_exe_preferred_over_db_and_directory_wins(self):
        yml_path = os.path.join(self.tmp.name, "game.yml")
        with open(yml_path, "w") as f:
            f.write("game:\n  exe: /games/witcher/witcher3.exe\n")
        game = self._selections(
            (9, "Witcher 3", "/games/stale/exe", "/games/witcher", yml_path)
        )[0]
        self.assertEqual(game.detect_hints, {
            "exe": "/games/witcher/witcher3.exe",
            "process_name": "witcher3.exe",
            "install_dir": "/games/witcher",
        })

    def test_no_hints_when_nothing_known(self):
        game = self._selections((11, "Mystery", None, None, None))[0]
        self.assertIsNone(game.detect_hints)


class TestPgaDbCandidatesOrder(unittest.TestCase):
    def test_active_install_dirs_come_first(self):
        from launchers import lutris

        with patch.object(lutris, "_is_flatpak_install", return_value=True), \
             patch.object(lutris.os.path, "expanduser", return_value="/home/x"):
            flatpak_first = lutris._pga_db_candidates()
        with patch.object(lutris, "_is_flatpak_install", return_value=False), \
             patch.object(lutris.os.path, "expanduser", return_value="/home/x"):
            native_first = lutris._pga_db_candidates()

        fp_data = "/home/x/.var/app/net.lutris.Lutris/data/lutris/pga.db"
        native = "/home/x/.local/share/lutris/pga.db"
        self.assertEqual(flatpak_first.index(fp_data) < flatpak_first.index(native), True)
        self.assertEqual(native_first.index(native) < native_first.index(fp_data), True)


if __name__ == "__main__":
    unittest.main()
