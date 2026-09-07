# zai-usage (Hermes companion plugin)

Z.ai **Coding Plan usage** at a glance — billing-plane only, consumes zero
model tokens:

- 5h rolling token window: % used + reset time (local)
- 24h per-model token totals
- 48h totals with the previous-24h delta

Sources (live-verified 2026-09-07, Bearer auth with the coding-plan key):

- `GET https://api.z.ai/api/monitor/usage/quota/limit`
- `GET https://api.z.ai/api/monitor/usage/model-usage?startTime=...&endTime=...`
  (UTC, `YYYY-MM-DD HH:MM:SS`)

## Install

```bash
hermes plugins install bacnh85/hermes-plugins/zai-usage --enable
hermes gateway restart
```

Requires `ZAI_ANTHROPIC_API_KEY` in `~/.hermes/.env` (same key as the
`zai-anthropic` provider).

## Use

- In-session: `/zai` (or `/zai refresh` to skip the 60s cache)
- Terminal: `hermes zai-usage [--refresh]`

Example:

```
5h window: 26% used, resets 10:00
24h usage:
  GLM-5.3-Flash: 275.7M
  GLM-5.3: 26.0M
48h usage:
  GLM-5.3-Flash: 456.1M (prev 24h: 180.4M)
  GLM-5.3: 77.8M (prev 24h: 51.8M)
```

Failed fetches fall back to the last successful report marked `(stale)`.
