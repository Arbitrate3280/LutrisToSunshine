import tempfile
import unittest

import lutristosunshine
from sunshine import sunshine


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def request(self, method, url, headers=None, verify=None, **kwargs):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "verify": verify,
                "kwargs": kwargs,
            }
        )
        return FakeResponse(self.payload)


class ApolloAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_server_name = sunshine.SERVER_NAME
        self.original_auth_session = sunshine.AUTH_SESSION
        self.original_auth_token = sunshine.AUTH_TOKEN

    def tearDown(self) -> None:
        sunshine.SERVER_NAME = self.original_server_name
        sunshine.AUTH_SESSION = self.original_auth_session
        sunshine.AUTH_TOKEN = self.original_auth_token

    def test_ensure_authenticated_uses_prompted_session_for_apollo(self) -> None:
        sunshine.SERVER_NAME = "apollo"
        sunshine.AUTH_SESSION = None
        sunshine.AUTH_TOKEN = None
        calls = []
        fake_session = object()

        original_get_auth_session = sunshine.get_auth_session
        original_get_auth_token = sunshine.get_auth_token
        original_load_cached_auth_token = sunshine._load_cached_auth_token
        try:
            sunshine.get_auth_session = (
                lambda allow_prompt=True: calls.append(("session", allow_prompt))
                or (fake_session if allow_prompt else None)
            )
            sunshine.get_auth_token = lambda: (_ for _ in ()).throw(
                AssertionError("Apollo auth must not fall back to token auth")
            )
            sunshine._load_cached_auth_token = lambda: None

            result = sunshine.ensure_authenticated(allow_prompt=True)
        finally:
            sunshine.get_auth_session = original_get_auth_session
            sunshine.get_auth_token = original_get_auth_token
            sunshine._load_cached_auth_token = original_load_cached_auth_token

        self.assertTrue(result)
        self.assertEqual(calls, [("session", False), ("session", True)])

    def test_get_config_root_prefers_running_apollo_process_config_path(self) -> None:
        original_get_apollo_process_config_root = sunshine._get_apollo_process_config_root
        original_isdir = sunshine.os.path.isdir
        try:
            sunshine.SERVER_NAME = "apollo"
            sunshine._get_apollo_process_config_root = lambda: "/tmp/apollo-config"
            sunshine.os.path.isdir = lambda path: False

            root = sunshine._get_config_root()
        finally:
            sunshine._get_apollo_process_config_root = original_get_apollo_process_config_root
            sunshine.os.path.isdir = original_isdir

        self.assertEqual(root, "/tmp/apollo-config")

    def test_get_config_root_falls_back_to_apollo_then_sunshine(self) -> None:
        original_get_apollo_process_config_root = sunshine._get_apollo_process_config_root
        original_expanduser = sunshine.os.path.expanduser
        original_isdir = sunshine.os.path.isdir
        try:
            sunshine.SERVER_NAME = "apollo"
            sunshine._get_apollo_process_config_root = lambda: None
            paths = {
                "~/.config/apollo": "/tmp/apollo-home",
                "~/.config/sunshine": "/tmp/sunshine-home",
            }
            sunshine.os.path.expanduser = lambda path: paths.get(path, path)
            sunshine.os.path.isdir = lambda path: path == "/tmp/apollo-home"

            root = sunshine._get_config_root()

            sunshine.os.path.isdir = lambda path: path == "/tmp/sunshine-home"
            fallback_root = sunshine._get_config_root()
        finally:
            sunshine._get_apollo_process_config_root = original_get_apollo_process_config_root
            sunshine.os.path.expanduser = original_expanduser
            sunshine.os.path.isdir = original_isdir

        self.assertEqual(root, "/tmp/apollo-home")
        self.assertEqual(fallback_root, "/tmp/sunshine-home")

    def test_sunshine_api_request_rehydrates_apollo_session_without_token_header(self) -> None:
        sunshine.SERVER_NAME = "apollo"
        sunshine.AUTH_SESSION = None
        sunshine.AUTH_TOKEN = None
        fake_session = FakeSession({"apps": []})
        ensure_calls = []

        original_get_auth_session = sunshine.get_auth_session
        original_ensure_authenticated = sunshine.ensure_authenticated
        original_get_auth_token = sunshine.get_auth_token
        try:
            sunshine.get_auth_session = lambda allow_prompt=True: None

            def fake_ensure_authenticated(allow_prompt: bool = True) -> bool:
                ensure_calls.append(allow_prompt)
                sunshine.AUTH_SESSION = fake_session
                return True

            sunshine.ensure_authenticated = fake_ensure_authenticated
            sunshine.get_auth_token = lambda: (_ for _ in ()).throw(
                AssertionError("Apollo API requests must not fall back to token auth")
            )

            data, error = sunshine.sunshine_api_request("GET", "/api/apps")
        finally:
            sunshine.get_auth_session = original_get_auth_session
            sunshine.ensure_authenticated = original_ensure_authenticated
            sunshine.get_auth_token = original_get_auth_token

        self.assertIsNone(error)
        self.assertEqual(data, {"apps": []})
        self.assertEqual(ensure_calls, [True])
        self.assertEqual(len(fake_session.calls), 1)
        self.assertEqual(fake_session.calls[0]["headers"], {})


class CliAuthBootstrapTests(unittest.TestCase):
    def test_main_uses_unified_auth_bootstrap_for_apollo(self) -> None:
        auth_calls = []

        original_detect_sunshine_installation = lutristosunshine.detect_sunshine_installation
        original_detect_apollo_installation = lutristosunshine.detect_apollo_installation
        original_get_running_servers = lutristosunshine.get_running_servers
        original_set_installation_type = lutristosunshine.set_installation_type
        original_set_server_name = lutristosunshine.set_server_name
        original_is_server_running = lutristosunshine.is_server_running
        original_get_covers_path = lutristosunshine.get_covers_path
        original_ensure_authenticated = lutristosunshine.ensure_authenticated
        original_get_lutris_command = lutristosunshine.get_lutris_command
        original_get_heroic_command = lutristosunshine.get_heroic_command
        original_detect_bottles_installation = lutristosunshine.detect_bottles_installation
        original_detect_steam_installation = lutristosunshine.detect_steam_installation
        original_detect_faugus_installation = lutristosunshine.detect_faugus_installation
        original_detect_ryubing_installation = lutristosunshine.detect_ryubing_installation
        original_detect_retroarch_installation = lutristosunshine.detect_retroarch_installation
        original_detect_eden_installation = lutristosunshine.detect_eden_installation
        try:
            lutristosunshine.detect_sunshine_installation = lambda: (False, "")
            lutristosunshine.detect_apollo_installation = lambda: True
            lutristosunshine.get_running_servers = lambda: ["apollo"]
            lutristosunshine.set_installation_type = lambda type_: None
            lutristosunshine.set_server_name = lambda name: None
            lutristosunshine.is_server_running = lambda name=None: True
            lutristosunshine.ensure_authenticated = lambda allow_prompt=True: auth_calls.append(allow_prompt) or True
            lutristosunshine.get_lutris_command = lambda: ""
            lutristosunshine.get_heroic_command = lambda: ("", "")
            lutristosunshine.detect_bottles_installation = lambda: False
            lutristosunshine.detect_steam_installation = lambda: (False, "")
            lutristosunshine.detect_faugus_installation = lambda: False
            lutristosunshine.detect_ryubing_installation = lambda: False
            lutristosunshine.detect_retroarch_installation = lambda: False
            lutristosunshine.detect_eden_installation = lambda: False

            with tempfile.TemporaryDirectory() as tempdir:
                lutristosunshine.get_covers_path = lambda: tempdir
                lutristosunshine.main([])
        finally:
            lutristosunshine.detect_sunshine_installation = original_detect_sunshine_installation
            lutristosunshine.detect_apollo_installation = original_detect_apollo_installation
            lutristosunshine.get_running_servers = original_get_running_servers
            lutristosunshine.set_installation_type = original_set_installation_type
            lutristosunshine.set_server_name = original_set_server_name
            lutristosunshine.is_server_running = original_is_server_running
            lutristosunshine.get_covers_path = original_get_covers_path
            lutristosunshine.ensure_authenticated = original_ensure_authenticated
            lutristosunshine.get_lutris_command = original_get_lutris_command
            lutristosunshine.get_heroic_command = original_get_heroic_command
            lutristosunshine.detect_bottles_installation = original_detect_bottles_installation
            lutristosunshine.detect_steam_installation = original_detect_steam_installation
            lutristosunshine.detect_faugus_installation = original_detect_faugus_installation
            lutristosunshine.detect_ryubing_installation = original_detect_ryubing_installation
            lutristosunshine.detect_retroarch_installation = original_detect_retroarch_installation
            lutristosunshine.detect_eden_installation = original_detect_eden_installation

        self.assertEqual(auth_calls, [True])


if __name__ == "__main__":
    unittest.main()
