# OmniRoute — Hermes model-provider plugin

Adds **OmniRoute** as a first-class `model.provider` to Hermes.

OmniRoute is an OpenAI-compatible routing gateway. This plugin lets Hermes
route inference through it from `.env` credentials:

- `OMNIROUTE_API_KEY`  — API key (Bearer token)
- `OMNIROUTE_BASE_URL` — base URL override (optional; default `http://localhost:20128/v1`)

Both belong in `~/.hermes/.env`.

## Install

See the [repo README](../README.md) — the two supported paths are **pip
entry-point** or **directory drop** into
`~/.hermes/plugins/model-providers/omniroute/`.

> **`hermes plugins install` is NOT the way to install this plugin.** It has
> no local-path support and always installs to
> `$HERMES_HOME/plugins/<name>/` — the provider scanner does **not** read
> that location, so the provider would never register. The CLI's "✓ Installed"
> is misleading for model providers. Use the directory drop (or symlink) or
> the pip entry-point path from the repo README. On a dev machine editing
> this repo: `ln -s .../hermes-plugins/omniroute ~/.hermes/plugins/model-providers/omniroute`.

## Use

```bash
hermes model                                              # pick OmniRoute + model (live /models fetch)
hermes config set model.provider omniroute
hermes config set model.model <model-id>                  # e.g. deepseek-v4-flash-0731
```

Or per-session: `/model omniroute/<model-id>`.

## Verify

```bash
hermes doctor          # shows omni route under "Provider Connectivity" with a /models probe
```

Programmatic check:

```python
from providers import get_provider_profile, list_providers
assert "omniroute" in [p.name for p in list_providers()]
p = get_provider_profile("omniroute")
print(p.name, p.base_url, p.env_vars)
models = p.fetch_models(timeout=12)   # live /models probe
```

> `hermes plugins doctor` is for general `register(ctx)` plugins; for
> model-providers the check above (provider discovery + live fetch) is the
> real validation.

## Files

- `__init__.py` — `OmniRouteProfile(ProviderProfile)` + `register_provider(...)`
- `plugin.yaml`   — manifest (`kind: model-provider`, `requires_env`; v1 to stay install-compatible)