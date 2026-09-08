# a2a-switchboard

Fleet [a2a-switchboard](https://github.com/bacnh85/a2a-switchboard) integration
for Hermes Agent: **registration + reverse channel** against one or many
boards.

```
fleet board ──< SSE reverse channel >── this Hermes (no inbound port needed)
dev board  ──< SSE reverse channel >──
```

Two jobs:

1. **Register** — `POST /register` with the board's gateway/bootstrap token;
   the minted per-peer `caller_token` is persisted to
   `~/.hermes/a2a-switchboard/caller_tokens.json` (0600). Steady state is a
   `PATCH /register` heartbeat (skills/IP refresh, admission untouched) with
   the full status-code ladder (401/404 → re-mint, 405 → POST-only, 403/409
   → fail, no fallback).
2. **Reverse channel** — an outbound SSE connection per board
   (`GET /channel?name=<peer>`). The board delivers inbound A2A requests down
   it; the plugin forwards them to the local inbound A2A server
   (`127.0.0.1:<A2A_PORT>`, default 9900) authenticated with your
   `upstream_token`, and posts responses back. Reconnect 1s → 30s capped
   backoff; a live channel **is** the health signal on the board.

## Install

```bash
# from the hermes-plugins repo root
python install_plugins.py a2a-switchboard     # native CLI path
# or: hermes plugins install bacnh85/hermes-plugins/a2a-switchboard
```

## Configure

**settings.yaml → `~/.hermes/config.yaml`**
(`plugins.entries.a2a-switchboard.settings`) — non-secret wiring only:

```yaml
plugins:
  entries:
    a2a-switchboard:
      settings:
        gateways:                     # LIST — as many boards as you like
          - name: fleet
            url: http://172.30.55.22:9920
          - name: dev
            url: http://127.0.0.1:9920
        peer_name: hermes-macbook-m1  # your name on each board
        public_url: ""                # empty = firewalled; channel carries everything
        heartbeat_sec: 60
        gateway_only: true            # register only in gateway/web processes
```

**secrets → `~/.hermes/.env`** (never in config.yaml — see `.env.example`):

```bash
# board tokens, one per alias (gateway token → pending; bootstrap → auto-accept)
A2A_SWITCHBOARD_TOKENS=name=fleet:token=agw_fleet...;name=dev:token=agw_dev...
# the bearer YOUR inbound A2A server accepts (what boards present as you)
A2A_SWITCHBOARD_UPSTREAM_TOKEN=w10YUE...
# optional pre-seeded caller tokens (normally auto-minted + stored)
# A2A_SWITCHBOARD_CALLER_TOKENS=name=fleet:token=agw_...
```

Restart Hermes. Logs to expect:

```
a2a-switchboard[fleet]: registered as 'hermes-macbook-m1' (state=accepted, caller_token minted)
a2a-switchboard[hermes-macbook-m1]: channel connected (HTTP 200)
a2a-switchboard[hermes-macbook-m1]: delivering id=42 POST /
```

Verify on the board: the peer row shows **channel: true** (health by
construction), and a routed request logs `src=<peer_name>`.

## Why a plugin (not core patches)

The old approach patched `plugins/platforms/a2a/tools.py` / `adapter.py`
inside the hermes-agent install — wiped by every `hermes update` and a
maintenance trap. This plugin uses only the public `register(ctx)` surface:
no core files are touched, and `hermes update` can never sweep it away.

## Files

| File | Role |
|---|---|
| `__init__.py` | `register(ctx)`, settings/env parsing, lifecycle (gateway-only) |
| `switchboard_client.py` | POST/PATCH registration ladder + caller-token store (0600) |
| `channel_client.py` | Reverse channel: SSE, chan_secret, local forward, reconnect |
| `settings.yaml` | Documented config template (gateway list, peer name, knobs) |
| `.env.example` | Secret template for `~/.hermes/.env` |
