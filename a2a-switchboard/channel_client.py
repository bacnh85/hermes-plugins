"""
A2A switchboard reverse-channel client.

Lets a firewalled/NAT'd Hermes receive inbound A2A requests over an
OUTBOUND SSE connection to an a2a-switchboard gateway. One ChannelClient
per switchboard; all connections are peer-initiated, so a single open
inbound port (or none — only outbound) keeps the agent reachable.

Protocol (a2a-switchboard docs/INTEGRATION.md §4):
  GET /channel?name=<peer>   → SSE stream (Authorization: Bearer <reg token>)
    event: hello   data: <per-connection chan_secret>
    event: request data: {id, method, path, query, headers, body_b64, chan_secret}
    event: ping    (keepalive)
  POST /channel/response/{id}?name=<peer>
    {id, status, headers, body_b64, chan_secret}

Delivered requests are forwarded to the LOCAL inbound A2A server
(127.0.0.1:<A2A_PORT>) with the upstream_token as bearer — the same token
the switchboard would present when proxying to us directly. Reconnects
with capped exponential backoff (1s → 30s); each reconnect gets a fresh
chan_secret. Runs in a daemon thread.
"""

from __future__ import annotations

import base64
import json
import logging
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from hermes_cli.urllib_security import open_credentialed_url

logger = logging.getLogger(__name__)

# Reconnection backoff caps (protocol rule: 1s → 30s)
_INITIAL_DELAY = 1.0
_MAX_DELAY = 30.0
_BACKOFF_FACTOR = 1.5

# Envelope/response body cap enforced by the switchboard (4 MiB both ways)
_BODY_CAP = 4 * 1024 * 1024

# Timeouts: SSE read blocks by design (60s covers slow TLS handshakes);
# local forward gets the switchboard's proxy budget; response POST is small.
_SSE_CONNECT_TIMEOUT = 60
_LOCAL_FORWARD_TIMEOUT = 600
_RESPONSE_POST_TIMEOUT = 30


class ChannelClient:
    """Reverse channel to ONE a2a-switchboard gateway.

    Usage::

        client = ChannelClient(
            board_url="http://172.30.55.22:9920",
            peer_name="hermes-macbook-m1",
            token="agw_...",
            local_port=9900,
            local_auth_token="upstream-token-our-server-accepts",
        )
        client.start()
        ...
        client.stop()
    """

    def __init__(
        self,
        *,
        board_url: str,
        peer_name: str,
        token: str,
        local_port: int = 9900,
        local_host: str = "127.0.0.1",
        local_auth_token: Optional[str] = None,
    ) -> None:
        self._board_url = board_url.rstrip("/")
        self._peer_name = peer_name
        self._token = token
        self._local_host = local_host
        self._local_port = local_port
        self._local_auth_token = (local_auth_token or "").strip()

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._chan_secret: Optional[str] = None
        self._delay = _INITIAL_DELAY

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"a2a-channel-{self._peer_name}",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "a2a-switchboard[%s]: reverse channel → %s (local :%s)",
            self._peer_name, self._board_url, self._local_port,
        )

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._thread = None
        logger.info("a2a-switchboard[%s]: reverse channel stopped", self._peer_name)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── main loop ────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._connect_and_read()
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                logger.warning(
                    "a2a-switchboard[%s]: channel dropped (%s); reconnect in %.1fs",
                    self._peer_name, exc, self._delay,
                )
                self._stop_event.wait(timeout=self._delay)
                self._delay = min(self._delay * _BACKOFF_FACTOR, _MAX_DELAY)

    def _connect_and_read(self) -> None:
        url = "{}/channel?name={}".format(
            self._board_url, urllib.parse.quote(self._peer_name, safe="")
        )
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Accept", "text/event-stream")

        logger.info("a2a-switchboard[%s]: connecting channel", self._peer_name)
        with open_credentialed_url(req, timeout=_SSE_CONNECT_TIMEOUT) as resp:
            logger.info(
                "a2a-switchboard[%s]: channel connected (HTTP %s)",
                self._peer_name, resp.status,
            )
            self._delay = _INITIAL_DELAY
            self._chan_secret = None
            self._read_sse_stream(resp)

    def _read_sse_stream(self, resp: Any) -> None:
        event_type: Optional[str] = None
        data_lines: list[str] = []
        buf = b""

        while not self._stop_event.is_set():
            # read1: single syscall, returns whatever the SSE stream has now.
            # Plain read(n) on a chunked stream waits to fill n bytes — with
            # only small events/keepalives trickling in it would block for
            # minutes before the first event ever surfaces.
            reader = getattr(resp, "read1", None) or resp.read
            chunk = reader(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line_bytes, buf = buf.split(b"\n", 1)
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\r")
                if not line:
                    if event_type and data_lines:
                        self._handle_event(event_type, "\n".join(data_lines))
                    event_type = None
                    data_lines = []
                elif line.startswith(":"):
                    continue  # SSE keepalive comment
                elif ":" in line:
                    field, value = line.split(":", 1)
                    field = field.strip()
                    if field == "event":
                        event_type = value.strip()
                    elif field == "data":
                        data_lines.append(value.strip())
                elif line.startswith("data"):
                    data_lines.append(line[4:].strip())

    def _handle_event(self, event_type: str, data: str) -> None:
        if event_type == "hello":
            self._handle_hello(data)
        elif event_type == "request":
            # Deliveries execute off the SSE read loop so a slow local agent
            # can never stall subsequent events (ping keeps the flow alive).
            threading.Thread(
                target=self._handle_request,
                args=(data,),
                name=f"a2a-channel-req-{self._peer_name}",
                daemon=True,
            ).start()
        elif event_type == "ping":
            logger.debug("a2a-switchboard[%s]: ping", self._peer_name)
        else:
            logger.debug(
                "a2a-switchboard[%s]: unknown event %r", self._peer_name, event_type
            )

    def _handle_hello(self, data: str) -> None:
        # Boards send the secret as a raw token; JSON accepted for forward-compat.
        data = data.strip()
        try:
            payload = json.loads(data)
            secret = (
                payload.get("chan_secret") or payload.get("secret") or str(payload)
                if isinstance(payload, dict)
                else str(payload)
            )
        except (json.JSONDecodeError, TypeError):
            secret = data
        self._chan_secret = secret or None
        logger.info(
            "a2a-switchboard[%s]: hello, chan_secret=%s",
            self._peer_name,
            (self._chan_secret[:8] + "…") if self._chan_secret else None,
        )

    # ── request delivery ─────────────────────────────────────────────────

    def _handle_request(self, data: str) -> None:
        try:
            envelope = json.loads(data)
        except json.JSONDecodeError as exc:
            logger.warning("a2a-switchboard[%s]: bad envelope: %s", self._peer_name, exc)
            return

        req_id = envelope.get("id")
        method = envelope.get("method", "POST")
        path = envelope.get("path", "/")
        query = envelope.get("query") or ""
        headers = envelope.get("headers") or {}
        chan_secret = envelope.get("chan_secret")

        try:
            body = base64.b64decode(envelope.get("body_b64") or "", validate=False)
        except Exception:
            body = b""

        logger.info(
            "a2a-switchboard[%s]: delivering id=%s %s %s",
            self._peer_name, req_id, method, path,
        )

        status, resp_headers, resp_body = self._forward_local(method, path, query, headers, body)
        self._post_response(req_id, status, resp_headers, resp_body, chan_secret)

    def _forward_local(
        self, method: str, path: str, query: str, headers: dict, body: bytes
    ) -> tuple[int, dict, bytes]:
        local_url = f"http://{self._local_host}:{self._local_port}{path}"
        if query:
            local_url += f"?{query}"

        local_req = urllib.request.Request(
            local_url, data=body if body else None, method=method
        )
        # Our own credential for the local inbound server (upstream_token);
        # envelopes carry no caller credentials — strip what the board sent.
        if self._local_auth_token:
            local_req.add_header("Authorization", f"Bearer {self._local_auth_token}")
        for key, value in headers.items():
            if key.lower() in ("authorization", "host", "content-length"):
                continue
            local_req.add_header(key, value)

        try:
            with urllib.request.urlopen(
                local_req, timeout=_LOCAL_FORWARD_TIMEOUT
            ) as resp:
                return resp.status, dict(resp.headers), resp.read(_BODY_CAP)
        except urllib.error.HTTPError as exc:
            return (
                exc.code,
                dict(exc.headers) if exc.headers else {},
                exc.read() if exc.fp else b"",
            )
        except Exception as exc:
            logger.error(
                "a2a-switchboard[%s]: local forward failed: %s", self._peer_name, exc
            )
            return 502, {}, json.dumps({"error": str(exc)}).encode("utf-8")

    def _post_response(
        self,
        req_id: Any,
        status: int,
        headers: dict,
        body: bytes,
        chan_secret: Optional[str],
    ) -> None:
        url = "{}/channel/response/{}?name={}".format(
            self._board_url,
            urllib.parse.quote(str(req_id), safe=""),
            urllib.parse.quote(self._peer_name, safe=""),
        )
        payload: dict[str, Any] = {
            "id": req_id,
            "status": status,
            "headers": headers,
            "body_b64": base64.b64encode(body).decode("ascii") if body else "",
        }
        if chan_secret:
            payload["chan_secret"] = chan_secret

        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST"
        )
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Content-Type", "application/json")
        try:
            with open_credentialed_url(req, timeout=_RESPONSE_POST_TIMEOUT) as resp:
                resp.read()
        except Exception as exc:
            logger.error(
                "a2a-switchboard[%s]: response post failed (id=%s): %s",
                self._peer_name, req_id, exc,
            )

    def _make_ssl_context(self) -> Optional[ssl.SSLContext]:  # pragma: no cover
        # Kept for callers that probed the old helper; open_credentialed_url
        # handles TLS itself.
        if not self._board_url.startswith("https"):
            return None
        try:
            return ssl.create_default_context()
        except Exception:
            return None
