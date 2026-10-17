"""Punktfunk host connection context, authentication, and HTTP transport.

Punktfunk (https://git.unom.io/unom/punktfunk) is a Sunshine-like streaming
host with its own game library. Custom (hand-added) library entries are
managed through its HTTPS management API, which authenticates with a bearer
token the host writes on first start to ``mgmt-token`` inside its config
directory (``~/.config/punktfunk/`` on Linux, ``%ProgramData%\\punktfunk\\``
on Windows). The file holds ``PUNKTFUNK_MGMT_TOKEN=<token>``.

The management API listens on ``0.0.0.0:47990`` by default -- the same port
as Sunshine's web UI -- so both hosts cannot serve that port at the same
time (see ``punktfunk-host detect-conflicts``).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests  # type: ignore[import-untyped]
import urllib3  # type: ignore[import-not-found]

from config.settings import SETTINGS_DIR, load_settings, update_setting
from utils.utils import run_command

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_PUNKTFUNK_HOST = "127.0.0.1"
DEFAULT_PUNKTFUNK_PORT = 47990
MGMT_TOKEN_FILENAME = "mgmt-token"
MGMT_TOKEN_VARIABLE = "PUNKTFUNK_MGMT_TOKEN"
MGMT_ENDPOINT_FILENAME = "mgmt-endpoint"
MGMT_URL_VARIABLE = "PUNKTFUNK_MGMT_URL"


@dataclass
class PunktfunkConnection:
    """Resolved Punktfunk host context shared by catalog operations."""

    auth_token: Optional[str] = None
    api_host_override: Optional[str] = None
    api_port_override: Optional[int] = None

    def set_api_connection(self, host: Optional[str] = None, port: Optional[object] = None) -> None:
        self.api_host_override = self._normalize_api_host(host) if host is not None else None
        self.api_port_override = self._normalize_api_port(port) if port is not None else None
        self.auth_token = None

    @staticmethod
    def _normalize_api_host(host: Optional[str]) -> str:
        return (host or "").strip() or DEFAULT_PUNKTFUNK_HOST

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

    def _get_environment_api_host(self) -> Optional[str]:
        for key in ("LUTRISTOSUNSHINE_PUNKTFUNK_HOST", "LUTRISTOSUNSHINE_API_HOST"):
            value = os.environ.get(key, "").strip()
            if value:
                return value
        return None

    def _get_environment_api_port(self) -> Optional[int]:
        for key in ("LUTRISTOSUNSHINE_PUNKTFUNK_PORT", "LUTRISTOSUNSHINE_API_PORT"):
            value = os.environ.get(key, "").strip()
            if not value:
                continue
            try:
                return self._normalize_api_port(value)
            except ValueError:
                continue
        return None

    def get_config_root(self) -> str:
        if os.name == "nt":
            program_data = os.environ.get("ProgramData", "C:\\ProgramData")
            return os.path.join(program_data, "punktfunk")
        return os.path.expanduser("~/.config/punktfunk")

    def get_covers_path(self) -> str:
        return os.path.join(SETTINGS_DIR, "covers")

    def get_api_key_path(self) -> str:
        return os.path.join(SETTINGS_DIR, "steamgriddb_api_key.txt")

    def get_mgmt_token_path(self) -> str:
        return os.path.join(self.get_config_root(), MGMT_TOKEN_FILENAME)

    def get_mgmt_endpoint_path(self) -> str:
        return os.path.join(self.get_config_root(), MGMT_ENDPOINT_FILENAME)

    def load_mgmt_token(self) -> Optional[str]:
        try:
            with open(self.get_mgmt_token_path(), encoding="utf-8") as file:
                content = file.read()
        except OSError:
            return None
        return parse_mgmt_token(content)

    def load_mgmt_endpoint(self) -> Optional[str]:
        try:
            with open(self.get_mgmt_endpoint_path(), encoding="utf-8") as file:
                content = file.read()
        except OSError:
            return None
        return parse_mgmt_endpoint(content)

    def _load_api_connection_settings(self) -> Dict[str, object]:
        servers = load_settings().get("servers")
        if not isinstance(servers, dict):
            return {}
        saved = servers.get("punktfunk")
        return saved if isinstance(saved, dict) else {}

    def save_api_connection(self, host: Optional[str], port: Optional[object]) -> None:
        current_host, current_port = self.get_api_connection()
        servers = load_settings().get("servers")
        if not isinstance(servers, dict):
            servers = {}
        servers["punktfunk"] = {
            "host": self._normalize_api_host(host if host is not None else current_host),
            "port": self._normalize_api_port(port if port is not None else current_port),
        }
        update_setting("servers", servers)

    def get_api_connection(self) -> Tuple[str, int]:
        saved = self._load_api_connection_settings()
        saved_host = saved.get("host")
        endpoint_host, endpoint_port = split_api_url(self.load_mgmt_endpoint())
        if self.api_host_override is not None:
            host = self.api_host_override
        else:
            host = self._get_environment_api_host() or self._normalize_api_host(
                saved_host if isinstance(saved_host, str) else None
            )
        if host == DEFAULT_PUNKTFUNK_HOST and endpoint_host is not None:
            host = endpoint_host
        saved_port = saved.get("port")
        try:
            normalized_saved_port = self._normalize_api_port(saved_port) if saved_port is not None else None
        except ValueError:
            normalized_saved_port = None
        port = (
            self.api_port_override
            or self._get_environment_api_port()
            or normalized_saved_port
            or endpoint_port
            or DEFAULT_PUNKTFUNK_PORT
        )
        return host, port

    def get_api_url(self) -> str:
        host, port = self.get_api_connection()
        return f"https://{host}:{port}"

    def get_server_display_name(self) -> str:
        return "Punktfunk"

    def detect_punktfunk_installation(self) -> bool:
        return shutil.which("punktfunk-host") is not None

    def is_running(self) -> bool:
        result = run_command(["ps", "-A"])
        if result.returncode != 0:
            return False
        return "punktfunk-host" in (result.stdout or "").lower()

    def get_running_servers(self) -> List[str]:
        if self.is_running():
            return ["punktfunk"]
        return []

    def api_request(
        self,
        method: str,
        endpoint: str,
        *,
        token: Optional[str] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[Any], Optional[str]]:
        url = f"{self.get_api_url()}{endpoint}"
        token = token or self.auth_token or self.load_mgmt_token()
        if not token:
            return None, f"Error: No Punktfunk management token found at {self.get_mgmt_token_path()}. Start punktfunk-host once so it writes mgmt-token, then try again."
        try:
            response = requests.request(
                method,
                url,
                headers={"Authorization": f"Bearer {token}"},
                verify=False,
                json=json,
                timeout=10,
            )
            if response.status_code == 401:
                return None, "Error: Punktfunk rejected the management token (401). Is this the token of this host?"
            response.raise_for_status()
            try:
                return response.json(), None
            except ValueError:
                return {"text": response.text}, None
        except requests.exceptions.RequestException as error:
            detail = _response_detail(getattr(error, "response", None))
            return None, f"{error}{detail}"

    def ensure_authenticated(self) -> Tuple[bool, Optional[str]]:
        token = self.auth_token or self.load_mgmt_token()
        if not token:
            return False, f"No Punktfunk management token found at {self.get_mgmt_token_path()}. Start punktfunk-host once so it writes mgmt-token, then try again."
        _, error = self.api_request("GET", "/api/v1/status", token=token)
        if error:
            return False, error
        self.auth_token = token
        return True, None


def parse_mgmt_token(content: str) -> Optional[str]:
    """Extract ``PUNKTFUNK_MGMT_TOKEN`` from the host's ``mgmt-token`` file."""
    return _parse_shell_assignment(content, MGMT_TOKEN_VARIABLE)


def parse_mgmt_endpoint(content: str) -> Optional[str]:
    """Extract ``PUNKTFUNK_MGMT_URL`` from the host's ``mgmt-endpoint`` file."""
    return _parse_shell_assignment(content, MGMT_URL_VARIABLE)


def _parse_shell_assignment(content: str, variable: str) -> Optional[str]:
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[len("export "):].strip()
        key, separator, value = line.partition("=")
        if not separator or key.strip() != variable:
            continue
        parsed = value.strip().strip("'\"").strip()
        if not parsed:
            continue
        return parsed
    return None


def split_api_url(url: Optional[str]) -> Tuple[Optional[str], Optional[int]]:
    """Split a management endpoint URL into ``(host, port)``."""
    if not url or not isinstance(url, str):
        return None, None
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
        try:
            port = parts.port
        except ValueError:
            port = None
        if port is not None and not 1 <= port <= 65535:
            port = None
        return host, port
    except ValueError:
        return None, None


def _response_detail(response: object) -> str:
    try:
        body = response.text if response is not None else ""
    except (AttributeError, ValueError):
        return ""
    body = (body or "").strip()
    if not body:
        return ""
    return f" -- host said: {body[:300]}"


CONNECTION = PunktfunkConnection()
