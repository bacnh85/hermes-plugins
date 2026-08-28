# Munin — Hermes memory plugin

[Munin Context Core](https://munin.kalera.dev) as a first-class Hermes
`memory.provider`: long-term memory with E2EE + GraphRAG, shared across
your projects.

**Design note:** this plugin is a **pure-stdlib Python REST client**
(`urllib`) — no Node, no MCP, no pip dependencies. It talks to the same
`/api/mcp/action` endpoint the `@kalera/munin-mcp-server` wraps, but as
an in-process provider, so the classic failure mode
`MCP stdio subprocess for 'munin-memory' has exited` cannot happen.

## Install

```bash
git clone https://github.com/bacnh85/hermes-plugins
cd hermes-plugins
python3 install_plugins.py munin
```

or manual:

```bash
mkdir -p ~/.hermes/plugins
cp -r munin ~/.hermes/plugins/munin
```

Then activate + restart:

```bash
hermes config set memory.provider munin
hermes gateway restart
```

## Environment

| Var | Required | Description |
|-----|----------|-------------|
| `MUNIN_API_KEY` | yes | Munin API key |
| `MUNIN_PROJECT` | yes | Active project id (e.g. `proj_hermes-mac-mini-m4`) |
| `MUNIN_BASE_URL` | no | Default `https://munin.kalera.dev` |
| `MUNIN_TIMEOUT` | no | Request timeout seconds (default 30) |

The installer prompts for the first two and writes them to `~/.hermes/.env`.

## What you get

- **Automatic recall** — before each turn, Hermes runs a 6-signal hybrid
  search (keyword + semantic + named-entity + quoted-phrase + recency +
  pinned) keyed on your message and injects the top matches into context.
- **Ten native tools** — `munin_store_memory` (single + batch),
  `munin_retrieve_memory`, `munin_search_memories`, `munin_list_memories`,
  `munin_recent_memories`, `munin_project_info`, `munin_versions`,
  `munin_rollback`, `munin_diff_memory`, `munin_share_memory` — same
  semantics as the MCP server's tools.
- **Protocol skill** — the provider registers the search-before/store-after
  protocol into the system prompt so agents follow the Munin workflow
  without extra prompting.
- **Desktop config panel** — edit key/project/base-url/timeout in
  Settings → Plugins without touching files.

## Verify

```bash
hermes memory status        # should list munin as active
hermes doctor               # memory section should pass
```

In a chat, ask: *“search munin for the option wheel exit plan”* — the agent
should call `munin_search_memories` natively (no MCP prefix).
