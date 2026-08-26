# OmniRoute — Hermes plugin

Adds **OmniRoute** as a first-class `model.provider` to Hermes.

OmniRoute is an OpenAI-compatible routing gateway. This plugin lets Hermes
route inference through it from `.env` credentials.

## Install

```bash
# Recommended: kind-aware installer (routes into plugins/model-providers/)
git clone https://github.com/bacnh85/hermes-plugins
cd hermes-plugins
python3 install_plugins.py omniroute

# Alternative: directory drop (the location provider discovery reads)
mkdir -p ~/.hermes/plugins/model-providers
cp -r omniroute ~/.hermes/plugins/model-providers/omniroute

# Alternative: dev symlink (live edits)
ln -s "$PWD/omniroute" ~/.hermes/plugins/model-providers/omniroute
```

> ⚠️ `hermes plugins install bacnh85/hermes-plugins/omniroute` installs into
> `~/.hermes/plugins/<name>/` (the general plugins dir). Model-provider
> plugins are discovered from `~/.hermes/plugins/model-providers/<name>/`
> only — a general-dir install shows the provider as "enabled" but the model
> picker finds no models. Use one of the paths above, then restart the
> gateway.

The installer prompts for the two env vars below, writes them to
`~/.hermes/.env`. After `hermes gateway restart`, OmniRoute appears in
`hermes model` and `get_provider_profile("omniroute")`.

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
models = p.fetch_models(timeout=30)   # live /models probe (30s — slow endpoints)
```

## Alternative install paths

The pip entry-point path still works (advanced; for venv-bound setups):

```bash
~/.hermes/hermes-agent/venv/bin/pip install -e git+https://github.com/bacnh85/hermes-plugins.git#egg=hermes-plugins
```

## Files

- `__init__.py` — `OmniRouteProfile(ProviderProfile)` + `register_provider(...)` + no-op `register(ctx)`
- `plugin.yaml` — manifest (`kind: model-provider`, `requires_env`; v1 to stay install-compatible)