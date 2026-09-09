"""a2a-switchboard plugin for Hermes Agent.

Connects this Hermes to one or more a2a-switchboard gateways:

  1. REGISTRATION — POST/PATCH /register with heartbeat refresh
     (switchboard_client.GatewayClient, one per board).
  2. REVERSE CHANNEL — an outbound SSE connection per board
     (channel_client.ChannelClient), so the fleet can deliver A2A requests
     to this agent even when firewalls/NAT block every inbound route.

Configuration surface:
  - settings.yaml (rendered to plugins.entries.a2a-switchboard.settings in
    ~/.hermes/config.yaml): gateway URLs + aliases, peer name, public_url,
    heartbeat/idle knobs, gateway_only.
  - ~/.hermes/.env (REQUIRED for anything credential-shaped):
      A2A_SWITCHBOARD_TOKENS         name=<alias>:token=<agw_...>;...   (secrets)
      A2A_SWITCHBOARD_UPSTREAM_TOKEN  your inbound-server bearer token   (secret)
      A2A_SWITCHBOARD_CALLER_TOKENS   optional pre-seeded caller tokens  (secrets)

Lifecycle: register(ctx) is called by Hermes' PluginManager for every
kind: standalone plugin. Registration + channels start in gateway/web
processes only (gateway_only=true, the default) — CLI one-shots stay fast.
Teardown is wired to on_session_finalize? No — process exit: the channel
threads are daemon threads, and deregistration is intentionally NOT
automatic (a board entry surviving a restart is the desired steady state).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PLUGIN_ID = "a2a-switchboard"

# tokens parse: name=<alias>:token=<value>[;name=<alias>:token=<value>]...
_TOKEN_ENTRY = re.compile(r"name=([^:,;\s]+)\s*:\s*token=([^,;\s]+)")


def _parse_token_map(raw: str, kind: str) -> dict[str, str]:
    """Parse 'name=a:token=x;name=b:token=y' → {a: x, b: y}. Last wins."""
    out: dict[str, str] = {}
    for alias, token in _TOKEN_ENTRY.findall(raw or ""):
        out[alias.strip()] = token.strip()
    if (raw or "").strip() and not out:
        logger.warning(
            "a2a-switchboard: %s set but no 'name=<alias>:token=<value>' entries parsed", kind
        )
    return out


def _settings(ctx: Any) -> dict[str, Any]:
    """Plugin settings dict (settings.yaml → config subtree)."""
    try:
        value = ctx.get_config("")  # whole subtree, when supported
        if isinstance(value, dict) and value:
            return value
    except Exception:
        pass
    # Fallback: read the file-relative keys one by one
    cfg: dict[str, Any] = {}
    for key in (
        "gateways", "peer_name", "public_url", "heartbeat_sec",
        "idle_timeout_sec", "gateway_only", "local_port",
    ):
        try:
            v = ctx.get_config(key)
        except Exception:
            v = None
        if v is not None:
            cfg[key] = v
    return cfg


def _in_gatewayish_process() -> bool:
    """True in the long-lived server processes (gateway / web / desktop)."""
    if os.environ.get("_HERMES_GATEWAY") == "1":
        return True
    # The dashboard/web server imports hermes_cli.web_server.
    return "hermes_cli.web_server" in os.environ.get("_HERMES_LOADED_MODULES", "") or bool(
        os.environ.get("HERMES_WEB_SERVER")
    )


class _Runtime:
    """Owns the per-board GatewayClient + ChannelClient pairs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._channels: list[Any] = []
        self._clients: list[Any] = []
        self._started = False

    def start(self, cfg: dict[str, Any]) -> str:
        from .switchboard_client import (
            GatewayClient,
            load_caller_token,
            store_caller_token,
        )

        gateways = cfg.get("gateways") or []
        if isinstance(gateways, dict):  # tolerate single-mapping shorthand
            gateways = [gateways]
        if not gateways:
            return "no gateways configured (settings.gateways)"

        tokens = _parse_token_map(
            os.environ.get("A2A_SWITCHBOARD_TOKENS", ""), "A2A_SWITCHBOARD_TOKENS"
        )
        caller_seeds = _parse_token_map(
            os.environ.get("A2A_SWITCHBOARD_CALLER_TOKENS", ""),
            "A2A_SWITCHBOARD_CALLER_TOKENS",
        )
        # Seed pre-migrated caller tokens into the 0600 store so the
        # PATCH-first path finds them uniformly.
        for alias, seed in caller_seeds.items():
            if not load_caller_token(alias):
                store_caller_token(alias, seed)
        upstream = (os.environ.get("A2A_SWITCHBOARD_UPSTREAM_TOKEN", "") or "").strip()
        peer_name = str(cfg.get("peer_name") or os.uname().nodename.split(".")[0]).strip()
        public_url = str(cfg.get("public_url") or "").strip()

        try:
            local_port = int(os.environ.get("A2A_PORT") or cfg.get("local_port") or 9900)
        except (TypeError, ValueError):
            local_port = 9900

        from .channel_client import ChannelClient

        started: list[str] = []
        notes: list[str] = []
        for gw in gateways:
            if not isinstance(gw, dict) or not gw.get("name") or not gw.get("url"):
                notes.append("skipped an invalid gateways[] entry")
                continue
            alias = str(gw["name"]).strip()
            url = str(gw["url"]).strip().rstrip("/")
            # Per-gateway public_url override: what THIS board would use to
            # reach us directly (LAN address for remote boards, loopback for
            # a board on this host). Falls back to the global public_url.
            gw_public = str(gw.get("public_url") or public_url)
            token = tokens.get(alias) or caller_seeds.get(alias)
            if not token:
                persisted = load_caller_token(alias)
                token = persisted  # PATCH-only path still works with a stored ct
            if not token:
                notes.append(f"{alias}: no token (add name={alias}:token=… to A2A_SWITCHBOARD_TOKENS)")
                continue

            client = GatewayClient(
                board_name=alias,
                url=url,
                token=token,
                peer_name=peer_name,
                upstream_token=upstream,
                public_url=gw_public,
                card=_agent_card(),
            )
            ok = client.register(force=True)
            with self._lock:
                self._clients.append(client)

            # A board is worth a channel when it accepted us OR we hold a
            # stored caller token (entry already accepted earlier).
            if ok or load_caller_token(alias):
                chan = ChannelClient(
                    board_url=url,
                    peer_name=peer_name,
                    token=load_caller_token(alias) or token,
                    local_port=local_port,
                    local_auth_token=upstream or None,
                )
                chan.start()
                with self._lock:
                    self._channels.append(chan)
                started.append(alias)
            else:
                notes.append(f"{alias}: registration failed (pending/rejected?)")

        parts = [f"channels on: {', '.join(startled) if (startled := started) else 'none'}"]
        parts += [f"[{n}]" for n in notes]
        self._start_heartbeat(cfg)
        return "; ".join(parts)

    def _start_heartbeat(self, cfg: dict[str, Any]) -> None:
        """Background loop re-PATCHing each board every heartbeat_sec so
        skill/IP changes propagate (GatewayClient rate-limits internally)."""
        try:
            interval = max(60, int(cfg.get("heartbeat_sec") or 60))
        except (TypeError, ValueError):
            interval = 60

        def _loop() -> None:
            import time

            while True:
                time.sleep(interval)
                with self._lock:
                    clients = list(self._clients)
                for client in clients:
                    try:
                        client.register()
                    except Exception as exc:
                        logger.warning(
                            "a2a-switchboard[%s]: heartbeat failed: %s",
                            client.board_name, exc,
                        )

        threading.Thread(
            target=_loop, name="a2a-switchboard-heartbeat", daemon=True
        ).start()

    def stop(self) -> None:
        with self._lock:
            channels, self._channels = self._channels, []
        for chan in channels:
            try:
                chan.stop()
            except Exception:
                pass


_runtime = _Runtime()


def _agent_card() -> Optional[dict]:
    """Capability card for the board directory — Hermes skill names included
    so the directory reflects what this agent offers (refreshed per beat)."""
    import pathlib

    skills_dir = pathlib.Path(
        os.environ.get("HERMES_HOME") or str(pathlib.Path.home() / ".hermes")
    ) / "skills"
    try:
        skills = sorted(
            p.name for p in skills_dir.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))
        )
    except OSError:
        skills = []
    return {
        "name": os.uname().nodename.split(".")[0],
        "description": "Hermes Agent (a2a-switchboard plugin)",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "skills": skills,
    }


def _maybe_start(ctx: Any, cfg: dict[str, Any]) -> None:
    if cfg.get("gateway_only", True) and not _in_gatewayish_process():
        logger.info("a2a-switchboard: gateway_only=true, non-gateway process — skipping")
        return
    if _runtime._started:
        return
    _runtime._started = True
    status = _runtime.start(cfg)
    logger.info("a2a-switchboard: %s", status)


def register(ctx) -> None:
    """PluginManager entry point."""
    cfg = _settings(ctx)
    if not cfg.get("gateways"):
        logger.info("a2a-switchboard: no settings.gateways configured — inactive")
        return
    # Defer to a worker thread: register() runs inside plugin discovery and
    # must never block process startup on network I/O.
    threading.Thread(
        target=_maybe_start, args=(ctx, cfg), name="a2a-switchboard-start", daemon=True
    ).start()


def shutdown() -> None:
    """Best-effort teardown (exported for hosts that call it)."""
    _runtime.stop()
