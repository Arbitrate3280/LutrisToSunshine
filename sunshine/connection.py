"""Sunshine/Apollo connection context, authentication, and HTTP transport."""

from __future__ import annotations

import base64
import getpass
import json
import os
import shutil
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

import requests  # type: ignore[import-untyped]
import urllib3  # type: ignore[import-not-found]
from requests.utils import cookiejar_from_dict, dict_from_cookiejar  # type: ignore[import-untyped]

from config.constants import DEFAULT_SUNSHINE_HOST, DEFAULT_SUNSHINE_PORT
from config.settings import load_settings, update_setting
from utils.utils import run_command

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class SunshineConnection:
    """Resolved server context shared by catalog operations."""

    installation_type: Optional[str] = None
    server_name: str = "sunshine"
    auth_session: Optional[requests.Session] = None
    auth_token: Optional[str] = None
    api_host_override: Optional[str] = None
    api_port_override: Optional[int] = None

    def set_installation_type(self, installation_type: str) -> None:
        self.installation_type = installation_type

    def set_server_name(self, name: str) -> None:
        self.server_name = name

    @staticmethod
    def _normalize_api_host(host: Optional[str]) -> str:
        return (host or "").strip() or DEFAULT_SUNSHINE_HOST

    @staticmethod
    def _normalize_api_port(port: Optional[object]) -> int:
        if not isinstance(port, (str, int, float)):
            raise ValueError("Port must be an integer.")
        try:
            normalized = int(port)
        except (TypeError, ValueError):
            raise ValueError("Port must be an integer.")
        if not 1 <= normalized <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
        return normalized

    def set_api_connection(self, host: Optional[str] = None, port: Optional[object] = None) -> None:
        self.api_host_override = self._normalize_api_host(host) if host is not None else None
        self.api_port_override = self._normalize_api_port(port) if port is not None else None
        self.auth_session = None
        self.auth_token = None

    def _get_environment_api_host(self, server_name: Optional[str] = None) -> Optional[str]:
        target = server_name or self.server_name
        for key in (f"LUTRISTOSUNSHINE_{target.upper()}_HOST", "LUTRISTOSUNSHINE_API_HOST"):
            value = os.environ.get(key, "").strip()
            if value:
                return value
        return None

    def _get_environment_api_port(self, server_name: Optional[str] = None) -> Optional[int]:
        target = server_name or self.server_name
        for key in (f"LUTRISTOSUNSHINE_{target.upper()}_PORT", "LUTRISTOSUNSHINE_API_PORT"):
            value = os.environ.get(key, "").strip()
            if not value:
                continue
            try:
                return self._normalize_api_port(value)
            except ValueError:
                continue
        return None

    def get_api_connection(self, server_name: Optional[str] = None) -> Tuple[str, int]:
        target = (server_name or self.server_name or "sunshine").strip().lower()
        saved = self._load_api_connection_settings().get(target, {})
        if not isinstance(saved, dict):
            saved = {}
        saved_host = saved.get("host")
        host = self.api_host_override or self._get_environment_api_host(target) or self._normalize_api_host(saved_host if isinstance(saved_host, str) else None)
        saved_port = saved.get("port")
        try:
            normalized_saved_port = self._normalize_api_port(saved_port) if saved_port is not None else DEFAULT_SUNSHINE_PORT
        except ValueError:
            normalized_saved_port = DEFAULT_SUNSHINE_PORT
        port = self.api_port_override or self._get_environment_api_port(target) or normalized_saved_port
        return host, port

    def get_api_url(self, server_name: Optional[str] = None) -> str:
        host, port = self.get_api_connection(server_name)
        return f"https://{host}:{port}"

    def server_supports_token_auth(self) -> bool:
        return self.server_name != "apollo"

    def get_server_display_name(self) -> str:
        return "Apollo" if self.server_name == "apollo" else "Sunshine"

    def _get_apollo_process_config_root(self) -> Optional[str]:
        result = run_command(["pgrep", "-x", "apollo-bin"])
        if result.returncode != 0:
            return None
        for pid in (result.stdout or "").split():
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as cmdline_file:
                    args = [part.decode() for part in cmdline_file.read().split(b"\0") if part]
            except (OSError, UnicodeDecodeError):
                continue
            for arg in reversed(args[1:]):
                expanded = os.path.expanduser(arg)
                if expanded.endswith(".conf"):
                    return os.path.dirname(os.path.realpath(expanded))
        return None

    def get_config_root(self) -> str:
        if self.installation_type == "flatpak":
            return os.path.expanduser("~/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine")
        if self.server_name == "apollo":
            process_root = self._get_apollo_process_config_root()
            if process_root:
                return process_root
            apollo_root = os.path.expanduser("~/.config/apollo")
            if os.path.isdir(apollo_root):
                return apollo_root
            sunshine_root = os.path.expanduser("~/.config/sunshine")
            return sunshine_root if os.path.isdir(sunshine_root) else apollo_root
        return os.path.expanduser("~/.config/sunshine")

    def get_covers_path(self) -> str:
        return os.path.join(self.get_config_root(), "covers")

    def get_api_key_path(self) -> str:
        return os.path.join(self.get_config_root(), "steamgriddb_api_key.txt")

    def get_credentials_path(self) -> str:
        return os.path.join(self.get_config_root(), "credentials")

    def _load_api_connection_settings(self) -> Dict[str, Dict[str, object]]:
        """Saved per-server connections from the tool settings store.

        Falls back to the legacy ``server_connection.json`` inside the
        Sunshine config root when the store has no servers yet, so
        pre-existing installs keep their saved host/port.
        """
        servers = load_settings().get("servers")
        if isinstance(servers, dict) and servers:
            return servers
        path = os.path.join(self.get_config_root(), "server_connection.json")
        if not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def save_api_connection(self, host: Optional[str], port: Optional[object], server_name: Optional[str] = None) -> None:
        target = (server_name or self.server_name or "sunshine").strip().lower()
        current_host, current_port = self.get_api_connection(server_name=target)
        servers = load_settings().get("servers")
        if not isinstance(servers, dict):
            servers = {}
        servers[target] = {
            "host": self._normalize_api_host(host if host is not None else current_host),
            "port": self._normalize_api_port(port if port is not None else current_port),
        }
        update_setting("servers", servers)

    def detect_apollo_installation(self) -> bool:
        return shutil.which("apollo") is not None

    def get_running_servers(self) -> List[str]:
        result = run_command(["ps", "-A"])
        if result.returncode != 0:
            return []
        output = (result.stdout or "").lower()
        return [name for name in ("sunshine", "apollo") if name in output]

    def is_server_running(self, name: Optional[str] = None) -> bool:
        running = self.get_running_servers()
        return bool(running) if name is None else name in running

    def _cookies_file_path(self) -> str:
        return os.path.join(self.get_credentials_path(), "cookies.json")

    def _token_file_path(self) -> str:
        return os.path.join(self.get_credentials_path(), "auth_token.txt")

    def _load_cached_auth_token(self) -> Optional[str]:
        try:
            with open(self._token_file_path(), encoding="utf-8") as file:
                token = file.read().strip()
        except OSError:
            return None
        return token or None

    def get_cached_auth_token(self) -> Optional[str]:
        return self.auth_token or self._load_cached_auth_token()

    def _save_auth_token(self, token: str) -> None:
        os.makedirs(self.get_credentials_path(), exist_ok=True)
        with open(self._token_file_path(), "w", encoding="utf-8") as file:
            file.write(token)

    def _save_session_cookies(self, session: requests.Session) -> None:
        os.makedirs(self.get_credentials_path(), exist_ok=True)
        try:
            with open(self._cookies_file_path(), "w", encoding="utf-8") as file:
                json.dump(dict_from_cookiejar(session.cookies), file)
        except OSError as error:
            print(f"Warning: Failed to save cookies: {error}")

    def _load_session_from_cookies(self) -> requests.Session:
        session = requests.Session()
        try:
            with open(self._cookies_file_path(), encoding="utf-8") as file:
                session.cookies = cookiejar_from_dict(json.load(file))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            try:
                os.remove(self._cookies_file_path())
            except OSError:
                pass
        return session

    def _validate_session(self, session: requests.Session) -> bool:
        try:
            response = session.get(f"{self.get_api_url()}/api/apps", verify=False, timeout=10)
            return response.status_code == 200
        except requests.exceptions.RequestException:
            return False

    def _validate_token(self, token: str) -> bool:
        if not self.server_supports_token_auth():
            return False
        try:
            response = requests.get(f"{self.get_api_url()}/api/apps", headers={"Authorization": token}, verify=False, timeout=10)
            return response.status_code == 200
        except requests.exceptions.RequestException:
            return False

    def get_sunshine_credentials(self) -> Tuple[str, str]:
        return input("Enter your Sunshine/Apollo username: "), getpass.getpass("Enter your Sunshine/Apollo password: ")

    def get_auth_session(self, allow_prompt: bool = True) -> Optional[requests.Session]:
        if self.auth_session and self._validate_session(self.auth_session):
            return self.auth_session
        session = self._load_session_from_cookies()
        if self._validate_session(session):
            self.auth_session = session
            return session
        if not allow_prompt:
            return None
        if not self.is_server_running():
            print("Error: Sunshine or Apollo is not running. Please start it and try again.")
            return None
        username, password = self.get_sunshine_credentials()
        if not username or not password:
            return None
        session = requests.Session()
        for endpoint in ("/api/login", "/login", "/auth/login", "/api/auth/login"):
            for mode, payload in (("json", {"username": username, "password": password}), ("form", {"username": username, "password": password})):
                try:
                    if mode == "json":
                        session.post(f"{self.get_api_url()}{endpoint}", json=payload, verify=False, timeout=10)
                    else:
                        session.post(f"{self.get_api_url()}{endpoint}", data=payload, verify=False, timeout=10)
                except requests.exceptions.RequestException:
                    continue
                if self._validate_session(session):
                    self._save_session_cookies(session)
                    self.auth_session = session
                    return session
        if not self.server_supports_token_auth():
            print("Error: Authentication failed. Could not obtain a valid session.")
            return None
        session = requests.Session()
        session.auth = (username, password)
        if self._validate_session(session):
            token = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
            self._save_auth_token(token)
            self.auth_token, self.auth_session = token, session
            return session
        print("Error: Authentication failed. Could not obtain a valid session.")
        return None

    def get_auth_token(self) -> Optional[str]:
        if not self.server_supports_token_auth():
            return None
        if not self.is_server_running():
            print("Error: Sunshine or Apollo is not running. Please start it and try again.")
            return None
        token = self.auth_token or self._load_cached_auth_token()
        if token and self._validate_token(token):
            self.auth_token = token
            return token
        if token:
            print("Error: Existing token is invalid. Please re-enter your credentials.")
            try:
                os.remove(self._token_file_path())
            except OSError:
                pass
        username, password = self.get_sunshine_credentials()
        if not username or not password:
            return None
        token = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        if not self._validate_token(token):
            print("Error: Authentication failed. Please check your credentials.")
            return None
        self._save_auth_token(token)
        self.auth_token = token
        return token

    def ensure_authenticated(self, allow_prompt: bool = True) -> bool:
        if self.get_auth_session(allow_prompt=False) is not None:
            return True
        if self.server_supports_token_auth():
            token = self.auth_token or self._load_cached_auth_token()
            if token and self._validate_token(token):
                self.auth_token = token
                return True
        if not allow_prompt:
            return False
        if self.get_auth_session(allow_prompt=True) is not None:
            return True
        return self.get_auth_token() is not None if self.server_supports_token_auth() else False

    def api_request(
        self,
        method: str,
        endpoint: str,
        *,
        session: Optional[requests.Session] = None,
        token: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
        json: Optional[Mapping[str, object]] = None,
    ) -> Tuple[Optional[Mapping[str, object]], Optional[str]]:
        url = f"{self.get_api_url()}{endpoint}"
        session = session or self.auth_session
        token = token or self.auth_token
        headers = headers or {}
        if session is None:
            session = self.get_auth_session(allow_prompt=False)
        if session is None and token is None:
            if not self.ensure_authenticated(allow_prompt=True):
                return None, "Error: Could not obtain authentication token or session."
            session, token = self.auth_session, self.auth_token
        try:
            if session is not None:
                response = session.request(method, url, headers=headers, verify=False, json=json)
            else:
                token = token or self.get_auth_token()
                if not token:
                    return None, "Error: Could not obtain authentication token or session."
                response = requests.request(method, url, headers={**headers, "Authorization": token}, verify=False, json=json)
            response.raise_for_status()
            try:
                payload = response.json()
                return (payload if isinstance(payload, dict) else {"text": response.text}), None
            except ValueError:
                return {"text": response.text}, None
        except requests.exceptions.RequestException as error:
            return None, str(error)


CONNECTION = SunshineConnection()
