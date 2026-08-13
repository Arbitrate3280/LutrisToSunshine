import unittest
from unittest.mock import patch

from sunshine.catalog import full_apps, get_existing_apps
from sunshine.connection import SunshineConnection


class SunshineCatalogTests(unittest.TestCase):
    def test_full_apps_default_path_initializes_optional_auth(self) -> None:
        connection = SunshineConnection()
        response = {
            "apps": [
                {
                    "name": "Desktop",
                    "cmd": "",
                    "index": 17,
                    "working-dir": "/games",
                    "prep-cmd": [{"do": "run", "elevated": True}],
                },
                {"cmd": "missing name"},
                "not an app",
            ]
        }
        with patch.object(connection, "api_request", return_value=(response, None)) as request:
            apps, error = full_apps(connection=connection)

        self.assertIsNone(error)
        self.assertEqual(
            apps,
            [
                {
                    "name": "Desktop",
                    "cmd": "",
                    "working-dir": "/games",
                    "index": 17,
                    "prep-cmd": [{"do": "run", "elevated": True}],
                }
            ],
        )
        request.assert_called_once_with("GET", "/api/apps", session=None, token=None)

    def test_existing_apps_reuses_typed_catalog(self) -> None:
        connection = SunshineConnection()
        with patch.object(
            connection,
            "api_request",
            return_value=({"apps": [{"name": "Desktop"}, {"name": "Game"}]}, None),
        ):
            self.assertEqual(
                get_existing_apps(connection=connection),
                [{"name": "Desktop"}, {"name": "Game"}],
            )


if __name__ == "__main__":
    unittest.main()
