import unittest
from unittest.mock import patch

from display.command_wrap import get_app_prep_commands, wrap_command
from sunshine.catalog import _finalize_for_installation, full_apps, get_existing_apps
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


    def test_flatpak_finalize_skips_managed_scripts_but_escapes_raw(self) -> None:
        wrapped = wrap_command("lutris lutris:rungame/test", "cmd") or ""
        prep = get_app_prep_commands(True)
        finalized = _finalize_for_installation({"cmd": wrapped, "prep-cmd": prep}, "flatpak")
        # Managed wrapper/prep hop to the host themselves (keeping
        # SUNSHINE_CLIENT_*); escaping them drops that env.
        self.assertEqual(finalized["cmd"], wrapped)
        self.assertEqual(finalized["prep-cmd"], prep)
        raw = _finalize_for_installation({"cmd": "lutris lutris:rungame/x"}, "flatpak")
        self.assertTrue(raw["cmd"].startswith("flatpak-spawn --host "))
        # A game command merely mentioning our script prefix is not managed.
        tricky = _finalize_for_installation({"cmd": "my-game lutristosunshine-fan-mod"}, "flatpak")
        self.assertTrue(tricky["cmd"].startswith("flatpak-spawn --host "))
        again = _finalize_for_installation(finalized, "flatpak")
        self.assertEqual(again, finalized)


if __name__ == "__main__":
    unittest.main()
