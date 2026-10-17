"""Tests for the Punktfunk host integration: credential parsing, connection
resolution, HTTP transport, catalog payloads, and main-flow routing."""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from config import settings as tool_settings
from config.types import GameSelection
from launchers import intake
from punktfunk import catalog as punktfunk_catalog
from punktfunk.connection import (
    CONNECTION,
    DEFAULT_PUNKTFUNK_HOST,
    DEFAULT_PUNKTFUNK_PORT,
    PunktfunkConnection,
    parse_mgmt_endpoint,
    parse_mgmt_token,
    split_api_url,
)
from sunshine import installation
from sunshine.connection import CONNECTION as SUNSHINE_CONNECTION


def _detected(**overrides):
    detected = {name: False for name in (
        "Steam", "Lutris", "Heroic", "Bottles",
        "Faugus", "Ryubing", "RetroArch", "Eden", "Cemu",
    )}
    detected.update(overrides)
    return detected


class MgmtFileParsingTests(unittest.TestCase):
    def test_parses_plain_assignment(self):
        self.assertEqual(parse_mgmt_token("PUNKTFUNK_MGMT_TOKEN=abc123"), "abc123")

    def test_parses_export_and_quotes(self):
        self.assertEqual(parse_mgmt_token('export PUNKTFUNK_MGMT_TOKEN="abc 123"'), "abc 123")

    def test_ignores_comments_and_unrelated_lines(self):
        content = "# PUNKTFUNK_MGMT_TOKEN=comment\nOTHER=1\nPUNKTFUNK_MGMT_TOKEN=real\n"
        self.assertEqual(parse_mgmt_token(content), "real")

    def test_missing_variable_returns_none(self):
        self.assertIsNone(parse_mgmt_token("OTHER=1"))

    def test_empty_value_returns_none(self):
        self.assertIsNone(parse_mgmt_token("PUNKTFUNK_MGMT_TOKEN="))

    def test_parses_endpoint_url(self):
        self.assertEqual(
            parse_mgmt_endpoint("PUNKTFUNK_MGMT_URL=https://127.0.0.1:47995"),
            "https://127.0.0.1:47995",
        )


class SplitApiUrlTests(unittest.TestCase):
    def test_splits_host_and_port(self):
        self.assertEqual(split_api_url("https://10.1.2.3:47995"), ("10.1.2.3", 47995))

    def test_host_without_port(self):
        self.assertEqual(split_api_url("https://example.com"), ("example.com", None))

    def test_none_returns_none_pair(self):
        self.assertEqual(split_api_url(None), (None, None))

    def test_invalid_url_returns_none_pair(self):
        self.assertEqual(split_api_url("https://[not-a-host"), (None, None))


class ConnectionResolutionTests(unittest.TestCase):
    """Precedence: override > environment > saved > mgmt-endpoint > default."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_root = Path(self._tmp.name) / "punktfunk"
        self.config_root.mkdir()
        self.connection = PunktfunkConnection()
        root_patcher = patch.object(
            self.connection, "get_config_root", lambda: str(self.config_root)
        )
        root_patcher.start()
        self.addCleanup(root_patcher.stop)
        settings_patcher = patch.object(tool_settings, "SETTINGS_DIR", self._tmp.name)
        settings_patcher.start()
        self.addCleanup(settings_patcher.stop)
        env_patcher = patch.dict("os.environ", {
            "LUTRISTOSUNSHINE_PUNKTFUNK_HOST": "",
            "LUTRISTOSUNSHINE_PUNKTFUNK_PORT": "",
            "LUTRISTOSUNSHINE_API_HOST": "",
            "LUTRISTOSUNSHINE_API_PORT": "",
        })
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def _write_endpoint(self, url: str) -> None:
        (self.config_root / "mgmt-endpoint").write_text(
            f"PUNKTFUNK_MGMT_URL={url}\n", encoding="utf-8"
        )

    def test_defaults_without_saved_or_endpoint(self):
        self.assertEqual(
            self.connection.get_api_connection(),
            (DEFAULT_PUNKTFUNK_HOST, DEFAULT_PUNKTFUNK_PORT),
        )

    def test_endpoint_file_supplies_host_and_port(self):
        self._write_endpoint("https://10.9.9.9:47995")
        self.assertEqual(self.connection.get_api_connection(), ("10.9.9.9", 47995))

    def test_saved_connection_wins_over_endpoint(self):
        self._write_endpoint("https://10.9.9.9:47995")
        self.connection.save_api_connection("192.168.1.10", 47991)
        self.assertEqual(self.connection.get_api_connection(), ("192.168.1.10", 47991))
        saved = json.loads(
            (Path(self._tmp.name) / "settings.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved["servers"]["punktfunk"], {"host": "192.168.1.10", "port": 47991})

    def test_environment_wins_over_saved(self):
        self.connection.save_api_connection("192.168.1.10", 47991)
        with patch.dict("os.environ", {
            "LUTRISTOSUNSHINE_PUNKTFUNK_HOST": "env-host",
            "LUTRISTOSUNSHINE_PUNKTFUNK_PORT": "5001",
        }):
            self.assertEqual(self.connection.get_api_connection(), ("env-host", 5001))

    def test_generic_environment_fallbacks(self):
        with patch.dict("os.environ", {
            "LUTRISTOSUNSHINE_API_HOST": "generic-host",
            "LUTRISTOSUNSHINE_API_PORT": "5002",
        }):
            self.assertEqual(self.connection.get_api_connection(), ("generic-host", 5002))

    def test_override_wins_over_environment(self):
        with patch.dict("os.environ", {
            "LUTRISTOSUNSHINE_PUNKTFUNK_HOST": "env-host",
            "LUTRISTOSUNSHINE_PUNKTFUNK_PORT": "5001",
        }):
            self.connection.set_api_connection("127.0.0.2", 7000)
            self.assertEqual(self.connection.get_api_connection(), ("127.0.0.2", 7000))

    def test_invalid_saved_port_falls_back(self):
        tool_settings.update_setting("servers", {"punktfunk": {"host": "saved-host", "port": "nope"}})
        self.assertEqual(self.connection.get_api_connection(), ("saved-host", DEFAULT_PUNKTFUNK_PORT))

    def test_api_url_is_https(self):
        self.assertEqual(self.connection.get_api_url(), f"https://127.0.0.1:{DEFAULT_PUNKTFUNK_PORT}")


class IsRunningTests(unittest.TestCase):
    @patch("punktfunk.connection.run_command")
    def test_matches_host_process(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            ["ps", "-A"], 0, " 2487 ?  00:00:01 punktfunk-host\n", ""
        )
        self.assertTrue(CONNECTION.is_running())
        self.assertEqual(CONNECTION.get_running_servers(), ["punktfunk"])

    @patch("punktfunk.connection.run_command")
    def test_tray_process_does_not_count(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            ["ps", "-A"], 0, " 4024 ?  00:00:00 punktfunk-tray\n", ""
        )
        self.assertFalse(CONNECTION.is_running())
        self.assertEqual(CONNECTION.get_running_servers(), [])

    @patch("punktfunk.connection.run_command")
    def test_ps_failure_is_not_running(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(["ps", "-A"], 1, "", "error")
        self.assertFalse(CONNECTION.is_running())


class ApiRequestTests(unittest.TestCase):
    def setUp(self):
        self.connection = PunktfunkConnection()
        token_patcher = patch.object(self.connection, "load_mgmt_token", return_value="tok-123")
        token_patcher.start()
        self.addCleanup(token_patcher.stop)
        endpoint_patcher = patch.object(self.connection, "load_mgmt_endpoint", return_value=None)
        endpoint_patcher.start()
        self.addCleanup(endpoint_patcher.stop)

    def test_missing_token_returns_error(self):
        with patch.object(self.connection, "load_mgmt_token", return_value=None):
            payload, error = self.connection.api_request("GET", "/api/v1/status")
        self.assertIsNone(payload)
        self.assertIn("mgmt-token", error)

    @patch("punktfunk.connection.requests.request")
    def test_rejected_token_reported_specifically(self, mock_request):
        response = MagicMock()
        response.status_code = 401
        mock_request.return_value = response
        payload, error = self.connection.api_request("GET", "/api/v1/status")
        self.assertIsNone(payload)
        self.assertIn("401", error)
        response.raise_for_status.assert_not_called()

    @patch("punktfunk.connection.requests.request")
    def test_success_returns_json_payload(self, mock_request):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"version": "0.44.1"}
        mock_request.return_value = response
        payload, error = self.connection.api_request("GET", "/api/v1/status")
        self.assertEqual(payload, {"version": "0.44.1"})
        self.assertIsNone(error)
        headers = mock_request.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer tok-123")

    @patch("punktfunk.connection.requests.request")
    def test_http_failure_appends_host_detail(self, mock_request):
        response = MagicMock()
        response.text = "invalid token"
        mock_request.side_effect = requests.exceptions.HTTPError("403 Client Error", response=response)
        payload, error = self.connection.api_request("GET", "/api/v1/status")
        self.assertIsNone(payload)
        self.assertIn("host said: invalid token", error)


class EnsureAuthenticatedTests(unittest.TestCase):
    def setUp(self):
        self.connection = PunktfunkConnection()

    def test_missing_token_file_returns_actionable_error(self):
        with patch.object(self.connection, "load_mgmt_token", return_value=None), patch.object(
            self.connection, "get_mgmt_token_path", return_value="/cfg/mgmt-token"
        ):
            authenticated, error = self.connection.ensure_authenticated()
        self.assertFalse(authenticated)
        self.assertIn("/cfg/mgmt-token", error)

    def test_status_probe_success_caches_token(self):
        with patch.object(self.connection, "load_mgmt_token", return_value="tok-123"), patch.object(
            self.connection, "api_request", return_value=({"version": "1"}, None)
        ) as mock_request:
            authenticated, error = self.connection.ensure_authenticated()
        self.assertTrue(authenticated)
        self.assertIsNone(error)
        self.assertEqual(self.connection.auth_token, "tok-123")
        mock_request.assert_called_once_with("GET", "/api/v1/status", token="tok-123")

    def test_status_probe_failure_propagates_error(self):
        with patch.object(self.connection, "load_mgmt_token", return_value="tok-123"), patch.object(
            self.connection, "api_request", return_value=(None, "connection refused")
        ):
            authenticated, error = self.connection.ensure_authenticated()
        self.assertFalse(authenticated)
        self.assertEqual(error, "connection refused")


class CatalogTests(unittest.TestCase):
    def test_get_existing_apps_maps_titles(self):
        connection = MagicMock()
        connection.api_request.return_value = (
            [{"title": "Alpha"}, {"title": "Beta"}, {"index": 3}],
            None,
        )
        self.assertEqual(
            punktfunk_catalog.get_existing_apps(connection=connection),
            [{"name": "Alpha"}, {"name": "Beta"}],
        )

    def test_get_existing_apps_reports_errors(self):
        connection = MagicMock()
        connection.get_server_display_name.return_value = "Punktfunk"
        connection.api_request.return_value = (None, "boom")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            apps = punktfunk_catalog.get_existing_apps(connection=connection)
        self.assertEqual(apps, [])
        self.assertIn("Error retrieving existing apps from Punktfunk API: boom", stdout.getvalue())

    def test_get_existing_apps_tolerates_non_list_payload(self):
        connection = MagicMock()
        connection.api_request.return_value = ({"apps": []}, None)
        self.assertEqual(punktfunk_catalog.get_existing_apps(connection=connection), [])

    def test_resolve_art_missing_file_is_empty(self):
        self.assertEqual(punktfunk_catalog.resolve_art("/does/not/exist.png"), {})

    def test_resolve_art_prefers_recorded_source_url(self):
        with tempfile.TemporaryDirectory() as raw:
            image_path = Path(raw) / "cover.png"
            image_path.write_bytes(b"png")
            (Path(raw) / "cover.png.source-url").write_text(
                "https://cdn.steamgriddb.com/x.png\n", encoding="utf-8"
            )
            art = punktfunk_catalog.resolve_art(str(image_path))
        self.assertEqual(art, {
            "portrait": "https://cdn.steamgriddb.com/x.png",
            "header": "https://cdn.steamgriddb.com/x.png",
        })

    def test_resolve_art_falls_back_to_local_path(self):
        with tempfile.TemporaryDirectory() as raw:
            image_path = Path(raw) / "cover.png"
            image_path.write_bytes(b"png")
            art = punktfunk_catalog.resolve_art(str(image_path))
        self.assertEqual(art, {"portrait": str(image_path), "header": str(image_path)})

    def test_submit_command_posts_library_custom_payload(self):
        connection = MagicMock()
        connection.get_server_display_name.return_value = "Punktfunk"
        connection.api_request.return_value = ({}, None)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            punktfunk_catalog.submit_command("My Game", "run-my-game", connection=connection)
        connection.api_request.assert_called_once_with(
            "POST",
            "/api/v1/library/custom",
            json={"title": "My Game", "launch": {"kind": "command", "value": "run-my-game"}},
        )
        self.assertIn("Added My Game to Punktfunk.", stdout.getvalue())

    def test_submit_command_reports_errors(self):
        connection = MagicMock()
        connection.get_server_display_name.return_value = "Punktfunk"
        connection.api_request.return_value = (None, "rejected")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            punktfunk_catalog.submit_command("My Game", "run-my-game", connection=connection)
        self.assertIn("Error adding My Game to Punktfunk via API: rejected", stdout.getvalue())

    def test_submit_command_includes_detect_hints(self):
        connection = MagicMock()
        connection.get_server_display_name.return_value = "Punktfunk"
        connection.api_request.return_value = ({}, None)
        with contextlib.redirect_stdout(io.StringIO()):
            punktfunk_catalog.submit_command(
                "My Game",
                "run-my-game",
                connection=connection,
                detect={"steam_appid": 570, "exe": "/games/dota/dota.sh", "install_dir": ""},
            )
        payload = connection.api_request.call_args.kwargs["json"]
        self.assertEqual(payload["detect"], {
            "steam_appid": 570,
            "exe": "/games/dota/dota.sh",
        })

    def test_submit_command_omits_empty_detect(self):
        connection = MagicMock()
        connection.get_server_display_name.return_value = "Punktfunk"
        connection.api_request.return_value = ({}, None)
        with contextlib.redirect_stdout(io.StringIO()):
            punktfunk_catalog.submit_command(
                "My Game", "run-my-game", connection=connection, detect={"exe": ""}
            )
        payload = connection.api_request.call_args.kwargs["json"]
        self.assertNotIn("detect", payload)


class PunktfunkMainFlowTests(unittest.TestCase):
    """Drive main() so host selection and catalog routing stay wired."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.covers_dir = tempfile.mkdtemp()

    def _facts(self):
        return installation.SunshineInstallation(
            installed=False,
            install_type=None,
            detected_types=[],
            unit=None,
            packages=None,
        )

    def _run_main(self, games, argv, inputs=()):
        from lutristosunshine import main

        self.existing_apps_mock = MagicMock(return_value=[])
        stdout = io.StringIO()
        with patch.object(tool_settings, "SETTINGS_DIR", self._tmp.name), patch.object(
            installation, "resolve_installation", return_value=self._facts()
        ), patch.object(
            SUNSHINE_CONNECTION, "get_running_servers", return_value=[]
        ), patch.object(
            CONNECTION, "detect_punktfunk_installation", return_value=True
        ), patch.object(
            CONNECTION, "get_running_servers", return_value=["punktfunk"]
        ), patch.object(
            CONNECTION, "ensure_authenticated", return_value=(True, None)
        ), patch.object(
            CONNECTION, "get_covers_path", return_value=self.covers_dir
        ), patch.object(
            intake, "detect_launchers", return_value=_detected(Steam=True)
        ), patch.object(
            intake, "collect_games", return_value=games
        ), patch.object(
            punktfunk_catalog, "get_existing_apps", new=self.existing_apps_mock
        ), patch(
            "builtins.input", side_effect=inputs
        ), contextlib.redirect_stdout(stdout):
            main(argv)
        return stdout.getvalue()

    def test_routes_existing_app_lookup_to_punktfunk(self):
        games = [GameSelection(game_id="1", game_name="Fake Game", display_source="Steam", source="steam")]
        with patch("lutristosunshine.get_user_selection", return_value=[]):
            stdout = self._run_main(games, [])
        self.assertIn("Games found in Steam:", stdout)
        self.assertIn("No games ready to add.", stdout)
        self.existing_apps_mock.assert_called_once_with(connection=CONNECTION)

    def test_submits_selected_games_through_punktfunk_catalog(self):
        games = [GameSelection(game_id="1", game_name="Fake Game", display_source="Steam", source="steam")]
        with patch.object(intake, "build_game_command", return_value="game-cmd"), patch.object(
            punktfunk_catalog, "submit_command"
        ) as mock_submit:
            stdout = self._run_main(games, ["--all"], inputs=["n"])
        mock_submit.assert_called_once_with(
            "Fake Game", "game-cmd", "default.png", connection=CONNECTION, detect=None
        )
        self.assertIn("Games added to Punktfunk successfully.", stdout)


if __name__ == "__main__":
    unittest.main()
