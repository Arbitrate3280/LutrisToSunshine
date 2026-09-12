import unittest
from unittest.mock import patch

from display.command_wrap import get_app_prep_commands, wrap_command
from sunshine.catalog import (
    _finalize_for_installation,
    full_apps,
    get_existing_apps,
    reconcile_display_apps,
)
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

    def _reconcile(self, connection: SunshineConnection, payload: dict) -> list:
        """Run a reconcile against ``payload`` and return the POSTed apps."""
        posted = []

        def fake_api_request(method, path, **kwargs):
            if method == "GET":
                return payload, None
            posted.append(kwargs.get("json"))
            return None, None

        with patch.object(connection, "api_request", side_effect=fake_api_request):
            updated, error = reconcile_display_apps(
                enable_display=True,
                connection=connection,
                display_enabled_fn=lambda: True,
            )
        self.assertIsNone(error)
        return updated, posted

    def test_reconcile_drops_stale_escaped_managed_prep_without_flatpak(self) -> None:
        # A payload kept from an earlier Flatpak setup is reconciled on a host
        # that no longer reports the flatpak install type, so the host-escape
        # strip never runs. The managed script must still be recognised.
        prep = get_app_prep_commands(True)
        connection = SunshineConnection()
        payload = {
            "apps": [
                {
                    "name": "Game",
                    "cmd": "lutris lutris:rungame/test",
                    "index": 0,
                    "prep-cmd": [
                        *prep,
                        {
                            "do": f"flatpak-spawn --host {prep[0]['do']}",
                            "undo": f"flatpak-spawn --host {prep[0]['undo']}",
                        },
                        {"do": "notify-send launching", "undo": ""},
                    ],
                }
            ]
        }

        updated, posted = self._reconcile(connection, payload)

        self.assertEqual(updated, 1)
        self.assertEqual(len(posted), 1)
        self.assertEqual(
            [entry["do"] for entry in posted[0]["prep-cmd"]],
            [prep[0]["do"], "notify-send launching"],
        )

    def test_reconcile_drops_env_prefixed_managed_prep_on_flatpak(self) -> None:
        # The escape strip only removes a bare `flatpak-spawn --host ` prefix,
        # so an env-prefixed escape would survive as a user prep command.
        prep = get_app_prep_commands(True)
        connection = SunshineConnection()
        connection.set_installation_type("flatpak")
        payload = {
            "apps": [
                {
                    "name": "Game",
                    "cmd": "lutris lutris:rungame/test",
                    "index": 0,
                    "prep-cmd": [
                        *prep,
                        {
                            "do": f"flatpak-spawn --host env SWAYSOCK=/run/user/1000/x {prep[0]['do']}",
                            "undo": f"flatpak-spawn --host env SWAYSOCK=/run/user/1000/x {prep[0]['undo']}",
                        },
                    ],
                }
            ]
        }

        updated, posted = self._reconcile(connection, payload)

        self.assertEqual(updated, 1)
        self.assertEqual(len(posted), 1)
        self.assertEqual(
            [entry["do"] for entry in posted[0]["prep-cmd"]],
            [prep[0]["do"]],
        )


if __name__ == "__main__":
    unittest.main()
