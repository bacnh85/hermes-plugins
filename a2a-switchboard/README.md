# a2a-switchboard

Fleet [a2a-switchboard](https://github.com/bacnh85/a2a-switchboard) integration
for Hermes Agent: **registration + reverse channel** against one or many
boards.

```
board A (remote) ──< SSE reverse channel >──┐
board B (local dev) ──< SSE reverse channel >──┤── this Hermes (zero inbound ports)
                                            ┘
```

Two jobs, one plugin:

1. **Registration** — `POST /register` with each board's gateway/bootstrap
   token. The board mints a per-peer `caller_token` (disclosed once); the
   plugin persists it to `~/.hermes/a2a-switchboard/caller_tokens.json`
   (mode 0600). Steady state is a `PATCH /register` heartbeat every
   `heartbeat_sec` that re-announces skills/public URL, with the full
   status-code ladder:
   `200` keep going · `401/404` caller token dead or entry gone → clear +
   POST re-mint · `405` pre-PATCH board → POST-only for the process ·
   `403` revoked / `409` foreign identity → **FAIL, no fallback**.
2. **Reverse channel** — an outbound SSE connection per board
   (`GET /channel?name=<peer>`, auto-reconnect 1s → 30s capped backoff).
   The board delivers inbound A2A requests down the channel; the plugin
   forwards each to your local inbound A2A server
   (`127.0.0.1:<A2A_PORT>`, default 9900) authenticated with your
   `upstream_token`, then posts the response back. A live channel **is**
   the health signal on the board — no probe port needed.

Result: any firewalled/NAT'd Hermes is reachable by the fleet through the
board, and its outbound calls are attributed (`src=<peer_name>`) instead of
logging as a bootstrap class.

> **This replaces the old core patches.** The previous approach patched
> `plugins/platforms/a2a/tools.py` / `adapter.py` inside the hermes-agent
> install — wiped by every `hermes update` and a maintenance trap. This
> plugin uses only the public `register(ctx)` plugin surface: no core files
> are touched, and updates can never sweep it away. If your hermes-agent
> still carries those patches, delete them (this plugin supersedes them;
> running both causes double registration).

---

## Requirements

- Hermes Agent (any recent build with the plugin manager, 2026-08+)
- An inbound A2A server on this agent (`hermes` a2a platform, port 9900 by
  default) — the plugin delivers board requests to it
- Network access from this agent **to** each board (outbound only — that is
  the whole point)
- Your token on each board: ask the board operator, or take a
  gateway/bootstrap token from the board's `data/state.json` / first-run log
  (gateway token → peer lands in *pending* until an admin accepts you;
  bootstrap token → auto-accepted)

---

## Install

```bash
# from any machine with Hermes (native CLI — recommended)
hermes plugins install bacnh85/hermes-plugins/a2a-switchboard

# or from a clone of this repo
git clone https://github.com/bacnh85/hermes-plugins
cd hermes-plugins
python3 install_plugins.py a2a-switchboard

# then restart the gateway so the plugin loads and opens channels
hermes gateway restart
```

Manual equivalent (what the native installer does): copy this directory to
`~/.hermes/plugins/a2a-switchboard/` and restart the gateway.

---

## Configure — two files, strict split

### 1. `~/.hermes/config.yaml` — non-secret wiring

Edit `plugins.entries.a2a-switchboard.settings` (the installed
`settings.yaml` documents every field):

```yaml
plugins:
  enabled:
    - a2a-switchboard          # add to the existing list if present
  entries:
    a2a-switchboard:
      settings:
        gateways:                          # LIST — register with as many boards as you like
          - name: fleet                    # alias (referenced by the .env token map)
            url: http://172.30.55.22:9920
            public_url: http://172.30.60.66:9900   # optional per-board override
          - name: dev
            url: http://127.0.0.1:9920
            public_url: http://127.0.0.1:9900
        peer_name: hermes-macbook-m1       # your name on every board [a-zA-Z0-9._-]
        public_url: ""                     # global fallback (see below)
        heartbeat_sec: 60                  # PATCH heartbeat interval (min 60)
        gateway_only: true                 # channels only in gateway/web processes
```

**`public_url` — when to set it.** It is the URL a board would use to reach
this agent *directly* (delivery falls back to direct when the channel is
down). Rules of thumb:

- Firewalled/NAT'd agent → leave empty. The reverse channel carries
  everything.
- Remote board that CAN reach this host on the LAN → set
  `gateways[].public_url` to this host's LAN address
  (`http://<lan-ip>:9900`).
- Board running on this same host → `http://127.0.0.1:9900`.
- A wrong value here is worse than empty: the board would pin an address it
  can never reach. When in doubt, leave it empty.

### 2. `~/.hermes/.env` — ALL secrets (never in config.yaml)

```bash
# Board tokens — one entry per gateway alias, semicolon-separated.
# gateway token → pending queue (admin accepts); bootstrap → auto-accept.
A2A_SWITCHBOARD_TOKENS=name=fleet:token=agw_XXXX;name=dev:token=agw_YYYY

# Your upstream token — the bearer YOUR inbound A2A server accepts from
# boards (must match an entry in this agent's A2A_PEER_TOKENS, e.g.
# 'a2a-switchboard:w10YUE...' or the shared A2A_BEARER_TOKEN).
A2A_SWITCHBOARD_UPSTREAM_TOKEN=w10YUE...

# OPTIONAL — pre-seed caller tokens when migrating an already-registered
# peer. Normally you leave this unset: boards mint caller tokens on first
# registration and the plugin stores them automatically.
# A2A_SWITCHBOARD_CALLER_TOKENS=name=fleet:token=agw_ZZZZ
```

Token classes, in one breath:
`A2A_SWITCHBOARD_TOKENS` = the **board's** secret that lets you register ·
`A2A_SWITCHBOARD_CALLER_TOKENS` = **your per-board identity** the board
mints for you (never share) · `A2A_SWITCHBOARD_UPSTREAM_TOKEN` = the secret
**others present as you** when calling your A2A server.

If your A2A server uses per-peer tokens, add the upstream token under a
stable name so boards can call you:

```bash
# in ~/.hermes/.env of THIS agent
A2A_PEER_TOKENS=existing...,a2a-switchboard:w10YUE...
```

---

## Verify (do not skip)

Restart, then confirm each step:

```bash
hermes gateway restart
```

1. **Gateway log** (`~/.hermes/logs/gateway.log`) — expect:

   ```
   a2a-switchboard[fleet]: registered as 'hermes-macbook-m1' (state=accepted, caller_token minted)
   a2a-switchboard[hermes-macbook-m1]: reverse channel → http://172.30.55.22:9920 (local :9900)
   a2a-switchboard[hermes-macbook-m1]: channel connected (HTTP 200)
   a2a-switchboard[hermes-macbook-m1]: hello, chan_secret=4b81…
   ```

2. **Sockets** — the gateway holds one SSE per board:

   ```bash
   lsof -a -p $(pgrep -f "hermes.*gateway run" | head -1) -iTCP -n -P | grep 9920
   # one ESTABLISHED line per board
   ```

3. **Directory** — each board lists you healthy:

   ```bash
   curl -s -H "Authorization: Bearer <your-token-for-the-board>" \
     http://172.30.55.22:9920/.well-known/agent.json | python3 -m json.tool | grep -A4 hermes-macbook
   # "healthy": true  (a live channel IS the health signal)
   ```

4. **Round trip** — call yourself THROUGH a board; the reply proves
   board → channel → your A2A server → back:

   ```bash
   CT=$(python3 -c "import json;print(json.load(open('$HOME/.hermes/a2a-switchboard/caller_tokens.json'))['fleet'])")
   curl -s -m 120 -X POST http://172.30.55.22:9920/peer/hermes-macbook-m1/ \
     -H "Authorization: Bearer $CT" -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":"t1","method":"message/send","params":{"message":{"role":"user","parts":[{"kind":"text","text":"Reply with exactly one word: PONG"}]}}}'
   # → TASK_STATE_COMPLETED ... "PONG"
   ```

5. **Board routing log** (operator-side, `data/routing.jsonl`) — your calls
   show `src: hermes-macbook-m1`, not a bootstrap class.

---

## Operation notes

- **Files the plugin owns**: `~/.hermes/a2a-switchboard/caller_tokens.json`
  (0600, per-board caller tokens — auto-managed; delete an entry to force a
  re-mint on the next POST).
- **Token rotation on a board** (board reset, new bootstrap token): put the
  new token in `A2A_SWITCHBOARD_TOKENS`, drop the stale alias from
  `A2A_SWITCHBOARD_CALLER_TOKENS` (or delete it from the store file), and
  restart. The plugin re-registers and re-mints automatically.
- **Peer landss in *pending***: a gateway-class token was used. An admin
  accepts you on the board's UI; everything else is automatic.
- **`gateway_only: true`** (default) keeps CLI one-shots fast — registration
  and channels run only in the long-lived gateway/web process.
- **Heartbeats** re-announce your skill list (from `~/.hermes/skills/`) so
  the board directory stays current.
- **Upgrading**: `hermes plugins update a2a-switchboard` (or re-run
  `install_plugins.py a2a-switchboard --refresh`), then
  `hermes gateway restart`.

## Troubleshooting

| Symptom | Cause → fix |
|---|---|
| `POST /register → 401 invalid or missing gateway token` | Board rotated/re-set its tokens. Take the new gateway/bootstrap token from the board, update `A2A_SWITCHBOARD_TOKENS`, restart. |
| `PATCH /register → 409 peer registered by another identity` | The board entry belongs to a different caller token (re-registered by someone else, or your store was deleted). Ask the board admin to DELETE the entry, clear the alias from the caller store, restart. |
| `PATCH → 403` | Your peer was revoked on that board — admin action required; do **not** re-register (the plugin won't). |
| Directory shows you but `healthy: false` | You are direct-registered (no channel) and the board cannot reach `public_url`. Fix or empty `public_url` so the reverse channel is authoritative. |
| Channel connects but requests hang | Is the local A2A server up (`curl http://127.0.0.1:9900/health`)? Does its token list accept `A2A_SWITCHBOARD_UPSTREAM_TOKEN`? |
| Everything worked until `hermes update` | It cannot be the plugin — updates never touch user plugins. Check whether an OLD core patch resurfaced (double registration); remove it. |
| Log: `no token (add name=<alias>:token=…)` | Alias mismatch: `gateways[].name` must match `name=<alias>` in `A2A_SWITCHBOARD_TOKENS` exactly. |

---

## Architecture (for reviewers)

| File | Role |
|---|---|
| `__init__.py` | `register(ctx)`: settings/env parsing, gateway-only lifecycle, per-board wiring, heartbeat loop |
| `switchboard_client.py` | `GatewayClient`: POST/PATCH/DELETE ladder, caller-token store (0600, atomic) |
| `channel_client.py` | `ChannelClient`: SSE (`read1()` — plain `read(n)` starves chunked SSE), `chan_secret` binding, local forward with upstream token, reconnect backoff |
| `settings.yaml` | Documented template of the config.yaml settings block |
| `.env.example` | Secret template for `~/.hermes/.env` |

Design constraints honored: pure stdlib (urllib/threading only) · secrets
only in `.env` · no core patches (public `register(ctx)` surface) ·
`open_credentialed_url` for credentialed calls (strips Authorization on
cross-origin redirects) · delivery handled off the SSE reader thread so a
slow agent turn never stalls the channel.

Live-verified end-to-end (2026-09-08/09, boards 0.7.x): registration +
heartbeat ladder, dual simultaneous channels, inbound PONG via both boards,
outbound attributed calls — see repo history (`444d5c7`, `2f96f66`,
`fcf617c`).
