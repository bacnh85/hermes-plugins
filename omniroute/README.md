# OmniRoute — Hermes plugin

Adds **OmniRoute** as a first-class `model.provider` to Hermes.

OmniRoute is an OpenAI-compatible routing gateway. This plugin lets Hermes
route inference through it from `.env` credentials.

## Install

```bash
hermes plugins install bacnh85/hermes-plugins/omniroute
```

The installer prompts for the two env vars below, writes them to
`~/.hermes/.env`, then asks "Enable 'omniroute' now?" — answer `y`. After
`hermes gateway restart`, OmniRoute appears in `hermes model` and
`get_provider_profile("omniroute")`.

## Environment

| Variable | Secret? | Default | Notes |
|---|---|---|---|
| `OMNIROUTE_API_KEY`  | yes | — | Required. Bearer token from <https://omniroute.online/>. |
| `OMNIROUTE_BASE_URL` | no  | `https://omniroute.online/v1` | Press Enter for the hosted default. Set `http://localhost:20128/v1` for a local OmniRoute install, or any remote URL. |

Both live in `~/.hermes/.env`.

## Use

```bash
hermes model                                              # pick OmniRoute + model (live /models fetch)
hermes config set model.provider omniroute
hermes config set model.model <model-id>                  # e.g. deepseek-v4-flash-0731
```

Or per-session: `/model omniroute/<model-id>`.

## Verify

```bash
hermes doctor          # shows OmniRoute under "Provider Connectivity" with a /models probe
```

Programmatic check (from the Hermes venv):

```python
from providers import get_provider_profile, list_providers
assert "omniroute" in [p.name for p in list_providers()]
p = get_provider_profile("omniroute")
print(p.name, p.base_url, p.env_vars)
models = p.fetch_models(timeout=12)   # live /models probe
```

## Alternative install paths

The pip entry-point path still works (advanced; for venv-bound setups):

```bash
~/.hermes/hermes-agent/venv/bin/pip install -e git+https://github.com/bacnh85/hermes-plugins.git#egg=hermes-plugins
hermes plugins enable omniroute
```

For a dev machine editing the repo, symlink to the general plugins dir:

```bash
ln -s "$PWD/omniroute" ~/.hermes/plugins/omniroute
hermes plugins enable omniroute
```

## Files

- `__init__.py` — `OmniRouteProfile(ProviderProfile)` + `register_provider(...)` + no-op `register(ctx)`
- `plugin.yaml` — manifest (`kind: standalone`, `requires_env`; v1 to stay install-compatible)