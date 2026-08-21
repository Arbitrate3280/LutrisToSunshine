"""Tests for the tool-owned settings store (config/settings.py)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import settings


class SettingsStoreTests(unittest.TestCase):
    def test_missing_file_loads_empty_dict(self):
        with tempfile.TemporaryDirectory() as raw:
            with patch.object(settings, "SETTINGS_DIR", raw):
                self.assertEqual(settings.load_settings(), {})

    def test_corrupt_file_loads_empty_dict(self):
        with tempfile.TemporaryDirectory() as raw:
            Path(raw, "settings.json").write_text("{not json", encoding="utf-8")
            with patch.object(settings, "SETTINGS_DIR", raw):
                self.assertEqual(settings.load_settings(), {})

    def test_non_dict_payload_loads_empty_dict(self):
        with tempfile.TemporaryDirectory() as raw:
            Path(raw, "settings.json").write_text("[1, 2]", encoding="utf-8")
            with patch.object(settings, "SETTINGS_DIR", raw):
                self.assertEqual(settings.load_settings(), {})

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as raw:
            with patch.object(settings, "SETTINGS_DIR", raw):
                settings.save_settings({"ignored_sources": ["RetroArch"]})
                self.assertEqual(
                    settings.load_settings(),
                    {"ignored_sources": ["RetroArch"]},
                )
                self.assertEqual(os.listdir(raw), ["settings.json"])

    def test_update_setting_preserves_other_keys(self):
        with tempfile.TemporaryDirectory() as raw:
            with patch.object(settings, "SETTINGS_DIR", raw):
                settings.save_settings({"servers": {"sunshine": {"port": 47990}}})
                settings.update_setting("ignored_sources", ["Steam"])
                self.assertEqual(
                    settings.load_settings(),
                    {
                        "servers": {"sunshine": {"port": 47990}},
                        "ignored_sources": ["Steam"],
                    },
                )

    def test_update_setting_creates_missing_dir_and_file(self):
        with tempfile.TemporaryDirectory() as raw:
            nested = str(Path(raw) / "a" / "b")
            with patch.object(settings, "SETTINGS_DIR", nested):
                settings.update_setting("key", 1)
                payload = json.loads(
                    Path(nested, "settings.json").read_text(encoding="utf-8")
                )

        self.assertEqual(payload, {"key": 1})


if __name__ == "__main__":
    unittest.main()
