"""Switchboard client: registration + heartbeat against N a2a-switchboards.

One ``GatewayClient`` per configured board. Implements the a2a-switchboard
registration protocol (docs/INTEGRATION.md §1):

  POST   /register   shared gateway/bootstrap token → mints per-peer
                     ``caller_token`` (disclosed ONCE; persisted 0600)
  PATCH  /register   steady-state heartbeat with the caller_token — partial
                     update {name, url?, card?, upstream_token?}; admission
                     state is never changed by PATCH
  DELETE /register   deregister (same token class that registered)

Status-code ladder for PATCH (verified against src/peers.rs 0.7.x):
  200 keep going · 401/404 caller token dead or entry gone → clear + POST
  fallback · 405 pre-PATCH board → POST-only for the process lifetime ·
  403 revoked / 409 foreign identity → FAIL, never re-register over it.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from hermes_cli.urllib_security import open_credentialed_url

logger = logging.getLogger(__name__)

REGISTER_TIMEOUT = 15
HEARTBEAT_MIN_INTERVAL = 60.0  # s, matches board politeness (rate limit 20/min/IP)

# Per-board caller-token store (mode 0600, under HERMES_HOME)
_TOKEN_DIRNAME = "a2a-switchboard"
_TOKEN_FILENAME = "caller_tokens.json"


def _token_store_path() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / _TOKEN_DIRNAME / _TOKEN_FILENAME


def load_caller_token(board_name: str) -> str:
    """Persisted caller_token for a board ('' when none)."""
    try:
        data = json.loads(_token_store_path().read_text(encoding="utf-8"))
        return str(data.get(board_name, "") or "")
    except Exception:
        return ""


def store_caller_token(board_name: str, token: str) -> None:
    """Persist a minted caller_token (0600 file, atomic replace)."""
    path = _token_store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, str] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        if data.get(board_name) == token:
            return
        data[board_name] = token
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        logger.info("a2a-switchboard[%s]: caller_token stored", board_name)
    except Exception as exc:
        logger.warning("a2a-switchboard[%s]: could not store caller_token: %s", board_name, exc)


def clear_caller_token(board_name: str) -> None:
    path = _token_store_path()
    try:
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        if board_name in data:
            del data[board_name]
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
    except Exception:
        pass


class GatewayClient:
    """Registration + heartbeat state machine for ONE switchboard."""

    def __init__(
        self,
        *,
        board_name: str,
        url: str,
        token: str,
        peer_name: str,
        upstream_token: str = "",
        public_url: str = "",
        card: Optional[dict] = None,
    ) -> None:
        self.board_name = board_name
        self.url = url.rstrip("/")
        self.token = (token or "").strip()
        self.peer_name = peer_name
        self.upstream_token = (upstream_token or "").strip()
        self.public_url = (public_url or "").strip()
        self.card = card
        # POST-only for the process lifetime once a pre-PATCH board answers 405
        self._post_only = False
        self._last_beat = 0.0
        self._registered = False

    # ── wire helpers ─────────────────────────────────────────────────────

    def _request(
        self, method: str, path_qs: str, body: Optional[dict], bearer: str
    ) -> tuple[int, dict]:
        req = urllib.request.Request(
            self.url + path_qs,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            method=method,
        )
        req.add_header("Authorization", f"Bearer {bearer}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with open_credentialed_url(req, timeout=REGISTER_TIMEOUT) as resp:
                raw = resp.read()
                code = resp.status
        except urllib.error.HTTPError as exc:
            code = exc.code
            try:
                raw = exc.read() if exc.fp else b"{}"
            except Exception:
                raw = b"{}"
        try:
            return code, (json.loads(raw.decode("utf-8")) if raw else {})
        except Exception:
            return code, {}

    # ── registration ladder ──────────────────────────────────────────────

    def register(self, *, force: bool = False) -> bool:
        """Register (POST) or heartbeat (PATCH). Returns True when accepted.

        Heartbeats are rate-limited to one per HEARTBEAT_MIN_INTERVAL unless
        ``force`` (first beat, or a token just changed).
        """
        import time

        now = time.monotonic()
        if self._registered and not force and (now - self._last_beat) < HEARTBEAT_MIN_INTERVAL:
            return True

        # PATCH-first whenever we hold a caller token (steady state). On the
        # very first call this is what makes a pre-registered peer (caller
        # token seeded/persisted) resume instead of colliding via POST.
        if not self._post_only:
            caller = load_caller_token(self.board_name)
            if caller:
                code, body = self._heartbeat(caller)
                self._last_beat = now
                if code == 200:
                    self._registered = True
                    return True
                if code == 401 or code == 404:
                    # dead token / entry deleted — re-mint below
                    clear_caller_token(self.board_name)
                elif code == 405:
                    self._post_only = True  # old board, stick to POST
                elif code in (403, 409):
                    # revoked / another identity owns the name — do NOT retry
                    logger.error(
                        "a2a-switchboard[%s]: PATCH /register → %s (%s); NOT re-registering",
                        self.board_name, code,
                        (body or {}).get("status") or (body or {}).get("detail") or "denied",
                    )
                    self._last_beat = now
                    return False

        return self._register_post()

    def _heartbeat(self, caller_token: str) -> tuple[int, dict]:
        body: dict[str, Any] = {"name": self.peer_name}
        if self.public_url:
            body["url"] = self.public_url
        if self.card:
            body["card"] = self.card
        if self.upstream_token:
            body["upstream_token"] = self.upstream_token
        return self._request("PATCH", "/register", body, caller_token)

    def _register_post(self) -> bool:
        import time

        if not self.token:
            logger.warning(
                "a2a-switchboard[%s]: no token configured (A2A_SWITCHBOARD_TOKENS); skipping",
                self.board_name,
            )
            return False
        body: dict[str, Any] = {
            "name": self.peer_name,
            "url": self.public_url or f"http://127.0.0.1:9900/",
        }
        if self.card:
            body["card"] = self.card
        if self.upstream_token:
            body["upstream_token"] = self.upstream_token

        code, resp = self._request("POST", "/register", body, self.token)
        if code not in (200, 201):
            logger.warning(
                "a2a-switchboard[%s]: POST /register → %s %s",
                self.board_name, code, resp,
            )
            return False

        minted = str(resp.get("caller_token") or "")
        if minted:
            store_caller_token(self.board_name, minted)
        self._registered = True
        self._last_beat = time.monotonic()
        logger.info(
            "a2a-switchboard[%s]: registered as %r (state=%s%s)",
            self.board_name,
            self.peer_name,
            resp.get("state"),
            ", caller_token minted" if minted else "",
        )
        return resp.get("state") in ("accepted", "updated", "registered")

    def deregister(self) -> None:
        bearer = load_caller_token(self.board_name) or self.token
        if bearer:
            self._request("DELETE", f"/register?name={urllib.parse.quote(self.peer_name, safe='')}", None, bearer)
        clear_caller_token(self.board_name)
        self._registered = False
