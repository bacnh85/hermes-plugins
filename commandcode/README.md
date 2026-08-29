# Command Code — Hermes plugin

Adds **Command Code** (https://commandcode.ai) as a first-class
`model.provider` to Hermes, plus a `/commandcode` usage command.

Command Code's Provider API is an OpenAI-compatible, pay-at-cost surface
fronting Claude, GPT-5.x, Gemini, GLM, Kimi, DeepSeek, Qwen, MiniMax and
more. Models are fetched live from `GET /provider/v1/models`.

This repo ships **two plugins** (Hermes loads them from different places):

| dir | kind | what it gives |
|---|---|---|
| `commandcode/` | `model-provider` | the provider itself: `/model` picker, live catalog, chat completions |
| `commandcode-usage/` | `standalone` | `/commandcode` slash + `hermes commandcode-usage` CLI: 5-hour / weekly windows and monthly credit balance from `/alpha/billing/credits` |

The split is structural: Hermes' general plugin loader never imports
`kind: model-provider` modules (they are handled by `providers/`
discovery), so a slash command cannot live inside the provider plugin.

## Install

```bash
git clone https://github.com/bacnh85/hermes-plugins
cd hermes-plugins

# Provider (routes into ~/.hermes/plugins/model-providers/commandcode/)
# --no-config keeps your current model.provider (e.g. omniroute)
python3 install_plugins.py commandcode --no-config

# Usage command (native install into ~/.hermes/plugins/commandcode-usage/)
~/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main plugins install \
  bacnh85/hermes-plugins/commandcode-usage --enable
```

Then add the key to `~/.hermes/.env` and restart:

```bash
echo "COMMANDCODE_API_KEY=user_..." >> ~/.hermes/.env
hermes gateway restart
```

> ⚠️ `hermes plugins install bacnh85/hermes-plugins/commandcode` (the
> provider) installs into the general plugins dir, which provider discovery
> never scans — it shows "enabled" but the model picker finds no models.
> Use `install_plugins.py` for the provider; the usage plugin installs fine
> natively.

## Environment

| Variable | Secret? | Default | Notes |
|---|---|---|---|
| `COMMANDCODE_API_KEY`  | yes | — | Required. Provider API key from [Studio → API Keys](https://commandcode.ai/studio). |
| `COMMANDCODE_BASE_URL` | no  | `https://api.commandcode.ai/provider/v1` | Override the endpoint (must include `/v1`). |

Both live in `~/.hermes/.env`. The key is the same one pi-commandcode /
pi-sub use — no separate subscription auth needed.

## Use

```bash
hermes model                                    # interactive picker: pick the Command Code provider, then model
hermes config set model.provider commandcode    # make it the default provider
hermes config set model.model deepseek/deepseek-v4-flash
```

Or one-shot from the terminal (real end-to-end check incl. tool calling):

```bash
hermes --provider commandcode --model minimax/minimax-m2.7-free -z "hello"
```

> Note: in-session `/model <arg>` switches model *within the current
> provider* — it does not change provider. Use the interactive `/model`
> picker (arrow keys → provider) or the config/CLI forms above. Beware
> fuzzy matches: OmniRoute proxies a `command-code/…` namespace, so a
> typo'd `commandcode/…` can silently land on OmniRoute's copy instead.

### Usage (5h / weekly / monthly)

```
/commandcode            # in-session: windows + credits for the configured key
/commandcode refresh    # force a fresh fetch
hermes commandcode-usage  # same data from the terminal
```

Example output:

```
Command Code usage (key#1a2b3c4d)
  5-hour : 0% of $14.00 used — resets in 3H 12M
  Weekly : 90% of $35.00 used — resets 2026-09-01 14:23
  Monthly: $0.07 remaining
```

Data comes from `GET https://api.commandcode.ai/alpha/billing/credits`
with the same Provider API key (live-verified 2026-08). `windowLimits`
`resetAt` values are epoch **milliseconds**; `credits.monthlyCredits` is
the remaining monthly USD allowance.

## Context windows

The Command Code `/provider/v1/models` catalog reports `context_length`
for every model (GLM-5.3 = 1M, deepseek-v4 = 1M, claude-5 = 1M, …).
Hermes resolves context length for custom endpoints from the endpoint's
own `/models` metadata, so windows come out correct with **no**
`model_overrides` seeding needed — unlike OmniRoute, whose catalog stamps
a 200K floor on everything.

If a model id ever goes stale (provider renames it), seed a one-off fix
via `model_overrides.commandcode.<model>.context_window` — but note the
pitfall: write it with `hermes_cli.config.save_config` from the venv
python, never `hermes config set` (it dot-splits keys with dots in them).

## Verify

Programmatic check (from the Hermes venv):

```python
from providers import get_provider_profile
p = get_provider_profile("commandcode")          # not None
print(p.name, p.base_url, p.env_vars)
models = p.fetch_models(timeout=30)              # live /models probe
print(len(models), "models")
```

In-session: `/model commandcode/<id>`, then `/commandcode` for usage.

## Files

- `commandcode/__init__.py` — `CommandCodeProfile(ProviderProfile)` + `register_provider(...)` + no-op `register(ctx)`
- `commandcode/plugin.yaml` — manifest (`kind: model-provider`, `requires_env`)
- `commandcode-usage/` — standalone plugin: `/commandcode` slash command + `hermes commandcode-usage` CLI subcommand

## Entries in the `/model` picker

Upstream hermes-agent bundles its own `plugins/model-providers/commandcode/`
(the earlier direct-patch approach, PR #88851 class) which registers two
profiles: `commandcode` and `commandcode-anthropic`. This user plugin loads
after bundled discovery (last-writer-wins) and pops the bundled
`commandcode-anthropic` profile from the registry at import time, so the
picker shows a **single** Command Code entry. Deleting the bundled dir is
NOT enough — `hermes update` / the daily auto-update would restore it.

## License

MIT
