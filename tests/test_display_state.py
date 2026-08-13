import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from display import state


class DisplayStateTests(unittest.TestCase):
    def test_load_state_preserves_persisted_sunshine_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            state_path = Path(tempdir) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "sunshine_unit_name": "app-dev.lizardbyte.app.Sunshine.service",
                        "paths": {"sunshine_conf": "/flatpak/config/sunshine/sunshine.conf"},
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(state, "DISPLAY_STATE_PATH", state_path), patch.object(
                state, "LEGACY_STATE_PATH", Path(tempdir) / "missing-legacy.json"
            ):
                loaded = state.load_state()

        self.assertEqual(loaded.paths.sunshine_conf, "/flatpak/config/sunshine/sunshine.conf")


if __name__ == "__main__":
    unittest.main()
