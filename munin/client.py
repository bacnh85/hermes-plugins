"""Munin memory plugin — Hermes MemoryProvider for Munin Context Core.

Long-term memory with E2EE + GraphRAG, hosted at munin.kalera.dev.
Pure-stdlib REST client (urllib) — no Node subprocess, no MCP transport,
no pip dependencies. This replaces the flaky stdio MCP path: the plugin
talks to the same /api/mcp/action endpoint the MCP server wraps, so a
crashed subprocess can never take memory down again.

Env vars (via ~/.hermes/.env or environment):
  MUNIN_API_KEY    — Munin API key (required)
  MUNIN_PROJECT    — active project id (required, e.g. proj_hermes-mac-mini-m4)
  MUNIN_BASE_URL   — API base (default https://munin.kalera.dev)
  MUNIN_TIMEOUT    — request timeout seconds (default 30)

Config via config.json memory section or the desktop config panel
(see config_schema.py). Env wins over config; both fall back to defaults.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://munin.kalera.dev"
DEFAULT_TIMEOUT = 30.0
CLIENT_NAME = "hermes-munin-plugin"
CLIENT_VERSION = "1.0.0"

# Actions the server advertises as optional — degraded gracefully when absent.
_OPTIONAL_ACTIONS = {"share", "versions", "diff", "rollback", "acknowledge_setup"}


def _load_dotenv(path: str) -> Dict[str, str]:
    """Minimal .env reader (KEY=VALUE lines, no shell interpretation)."""
    out: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                out[key.strip()] = val.strip().strip("'\"")
    except OSError:
        pass
    return out


class _MuninHTTP:
    """Tiny REST client for the Munin Context Core /api/mcp surface."""

    def __init__(self, api_key: str, project: str, base_url: str, timeout: float):
        self.api_key = api_key
        self.project = project
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._caps: Optional[Dict[str, Any]] = None

    # -- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        # Cloudflare in front of munin.kalera.dev 403s the default
        # Python-urllib UA (error 1010) — identify as the SDK the server
        # already sees from the Node MCP client.
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"{CLIENT_NAME}/{CLIENT_VERSION}",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            raise RuntimeError(f"Munin HTTP {e.code} on {path}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Munin request failed ({path}): {e.reason}") from e
        except TimeoutError as e:
            raise RuntimeError(f"Munin request timed out after {self.timeout}s ({path})") from e
        if payload.get("ok") is False or payload.get("success") is False:
            err = payload.get("error") or {}
            if isinstance(err, str):
                err = {"code": err, "message": err}
            raise RuntimeError(f"Munin error {err.get('code', 'INTERNAL_ERROR')}: {err.get('message', '')}")
        return payload.get("data", payload)

    # -- endpoint wrappers --------------------------------------------------

    def capabilities(self, force: bool = False) -> Dict[str, Any]:
        if self._caps is None or force:
            self._caps = self._request("GET", "/api/mcp/capabilities")
        return self._caps

    def project_info(self) -> Dict[str, Any]:
        # The MCP server builds this client-side from /capabilities plus the
        # local encryption-key flag — same here.
        caps = self.capabilities()
        enc = os.environ.get("MUNIN_ENCRYPTION_KEY", "")
        if not enc:
            hermes_home = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
            for name in (".env.local", ".env"):
                enc = _load_dotenv(str(hermes_home / name)).get("MUNIN_ENCRYPTION_KEY", "")
                if enc:
                    break
        return {"capabilities": caps, "encryptionKeyConfigured": bool(enc)}

    def _action(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = {
            "apiKey": self.api_key,
            "project": self.project,
            "projectId": self.project,  # fallback for un-restarted servers
            "action": action,
            "payload": payload,
            "client": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
        }
        return self._request("POST", "/api/mcp/action", body)

    # -- public operations --------------------------------------------------

    def store(self, memories: List[Dict[str, Any]]) -> Dict[str, Any]:
        if len(memories) == 1:
            return self._action("store", memories[0])
        return self._action("store_batch", {"memories": memories})

    def retrieve(self, key: str) -> Dict[str, Any]:
        return self._action("retrieve", {"key": key})

    def search(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._action("search", payload)

    def list(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._action("list", payload)

    def recent(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._action("recent", payload)

    def passthrough(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._action(action, payload)

    def action_supported(self, action: str) -> bool:
        try:
            caps = self.capabilities()
            actions = caps.get("actions", {}) if isinstance(caps, dict) else {}
            return (
                action in actions.get("core", [])
                or action in actions.get("optional", [])
            )
        except Exception:
            return action not in _OPTIONAL_ACTIONS  # assume core actions exist
