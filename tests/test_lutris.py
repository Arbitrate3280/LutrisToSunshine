"""Tests for launchers.lutris PGA database reading."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from launchers.lutris import list_lutris_games


def _make_pga_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE games (id INTEGER PRIMARY KEY, name TEXT)"
    )
    conn.executemany("INSERT INTO games (id, name) VALUES (?, ?)", rows)
    conn.commit()
    conn.close()


class TestListLutrisGames(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_reads_games_from_pga_db(self):
        db_path = os.path.join(self.tmp.name, "pga.db")
        _make_pga_db(db_path, [(1, "Portal"), (2, "Half-Life 2")])
        with patch("launchers.lutris._pga_db_candidates", return_value=[db_path]):
            self.assertEqual(
                list_lutris_games(), [("1", "Portal"), ("2", "Half-Life 2")]
            )

    def test_skips_corrupt_db_and_falls_through(self):
        corrupt = os.path.join(self.tmp.name, "corrupt.db")
        with open(corrupt, "w") as f:
            f.write("not a database")
        good = os.path.join(self.tmp.name, "pga.db")
        _make_pga_db(good, [(5, "Doom")])
        with patch(
            "launchers.lutris._pga_db_candidates", return_value=[corrupt, good]
        ):
            self.assertEqual(list_lutris_games(), [("5", "Doom")])

    def test_returns_empty_when_no_db(self):
        missing = os.path.join(self.tmp.name, "missing.db")
        with patch("launchers.lutris._pga_db_candidates", return_value=[missing]):
            self.assertEqual(list_lutris_games(), [])


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
