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
| `OMNIROUTE_API_KEY`  | yes | — | Required. Bearer token of your OmniRoute instance. |
| `OMNIROUTE_BASE_URL` | no  | `http://localhost:20128/v1` | Self-hosted by design. Set your instance's URL — e.g. `http://localhost:20128/v1` locally or `https://omniroute.bacnh.com/v1` remotely. |

Both live in `~/.hermes/.env`.

## Context windows (the 200K-floor bug)

OmniRoute (9router fork) stamps its registry default pair — context 200000 /
max output 128000 — onto models whose real window is larger (GLM-5.3 /
GLM-5.3-flash = 1M, …). Hermes trusts that raw `/models` metadata for
route-prefixed ids (`glm-cn/glm-5.3`, …) and silently caps the session at
200K.

Fix without touching Hermes core: sync Hermes' supported
`model_overrides.omniroute.<model>.context_window` section from the live
catalog:

```bash
# preview what would change
~/.hermes/hermes-agent/venv/bin/python \
  ~/.hermes/plugins/model-providers/omniroute/scripts/sync_context_overrides.py --dry-run

# write model_overrides into config.yaml
.../sync_context_overrides.py
```

The tool derives each family's true window from the catalog itself (the max
any route/suffix reports — one honest upstream copy exposes it), overrides
only floor-poisoned ids, preserves entries you wrote by hand, and tracks its
own writes in `~/.hermes/omniroute_context_overrides.json`. Bare model ids
(`glm-5.3`, `deepseek-v4-flash`) need nothing: Hermes' built-in family table
already carries verified windows. Re-run when the catalog grows.

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
- `scripts/sync_context_overrides.py` — seeds `model_overrides.omniroute.*.context_window`
  from the live catalog (see "Context windows" above)