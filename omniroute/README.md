# OmniRoute — Hermes model-provider plugin

Adds **OmniRoute** as a first-class `model.provider` to Hermes.

OmniRoute is an OpenAI-compatible routing gateway. This plugin lets Hermes
route inference through it from `.env` credentials:

- `OMNIROUTE_API_KEY`  — API key (Bearer token)
- `OMNIROUTE_BASE_URL` — base URL override (optional; default `https://omniroute.bacnh.com/v1`)

Both belong in `~/.hermes/.env`.

## Install

See the [repo README](../README.md). Recommended: `pip install` + `hermes
plugins enable omniroute`. Or drop this directory at
`~/.hermes/plugins/model-providers/omniroute/`.

> Note for model-provider plugins: `hermes plugins install` copies into
> `$HERMES_HOME/plugins/<name>/`, which the provider scanner does **not**
> read — use the pip path or the `model-providers/` directory drop above.

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
- `plugin.yaml`   — manifest (`kind: model-provider`, `requires_env`, v2 fields)