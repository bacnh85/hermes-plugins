## Finish wiring a2a-switchboard (required)

The plugin installs inert. It needs **two things** before it registers with a
board: gateway settings (URL + peer name) in `config.yaml` and your board
token in `.env`. Nothing runs until both exist.

**1. Point the plugin at your board** (repeat `gateways` with one `-` entry
per board; the alias must match the `name=` in the token map below):

```bash
hermes config set plugins.entries.a2a-switchboard.settings.gateways \
  '[{"name":"fleet","url":"http://172.30.55.22:9920"}]'
hermes config set plugins.entries.a2a-switchboard.settings.peer_name <your-peer-name>
```

**2. Add the board token to `~/.hermes/.env`** — note the required
`name=<alias>:token=<value>` shape (a bare token parses to zero boards):

```bash
A2A_SWITCHBOARD_TOKENS=name=fleet:token=agw_XXXX
```

Gateway token → peer lands in the board's **pending** queue (admin accepts).
Bootstrap token → auto-accepted.

**3. Make sure your local inbound A2A server is enabled** (the plugin
delivers board requests to `127.0.0.1:<A2A_PORT>`, default 9900):

```bash
# in ~/.hermes/.env
A2A_PORT=9900
```

**4. Restart and verify:**

```bash
hermes gateway restart
grep a2a-switchboard ~/.hermes/logs/gateway.log   # expect "registered as ..." + "channel connected"
```

Full details: [README.md](README.md) (Configure / Verify / Troubleshooting).
