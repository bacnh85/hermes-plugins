# hermes-plugins

Hermes Agent plugins, published by **bacnh85**.

This repo ships ready-to-install plugins for [Hermes Agent](https://hermes-agent.nousresearch.com)
so you don't have to re-implement the same provider/backend on every Hermes
instance you run (CLI, desktop, gateway, LXC/CI, …).

Each plugin lives in its own directory. See the per-plugin README for setup.

## Plugins in this repo

| Plugin | Kind | What it does |
|---|---|---|
| [`omniroute/`](./omniroute) | model-provider | Add OmniRoute as a `model.provider` (OpenAI-compatible gateway). |

More plugins will be added here over time.

---

## Installing a plugin

You have two supported options. Pick whichever fits your workflow.

### Option A — pip (recommended, first-class for provider plugins)

Provider plugins are discovered by Hermes through the `hermes_agent.plugins`
entry-point group. Install this repo into the Hermes Python environment, then
tell Hermes to load it:

```bash
# 1. Install into the Hermes venv
~/.hermes/hermes-agent/venv/bin/pip install -e git+https://github.com/bacnh85/hermes-plugins.git#egg=hermes-plugins

# 2. Enable the plugin (entry-point plugins are opt-in)
hermes plugins enable omniroute

# 3. Add your API key to ~/.hermes/.env
#    OMNIROUTE_API_KEY=your-key-here
```

> Entry-point plugins are gated by `plugins.enabled` — a plugin is never
> imported just because it's pip-installed. `hermes plugins enable <name>`
> adds it to the allow-list for you.

### Option B — directory drop

`hermes plugins install` drops plugins into `$HERMES_HOME/plugins/<name>/`,
which the provider-discovery scanner (which reads
`$HERMES_HOME/plugins/model-providers/`) does **not** pick up. So for
model-provider plugins, drop the plugin directory in the location discovery
actually reads:

```bash
# Clone once, then copy the plugin(s) where provider discovery looks:
git clone https://github.com/bacnh85/hermes-plugins
mkdir -p ~/.hermes/plugins/model-providers
cp -r hermes-plugins/omniroute ~/.hermes/plugins/model-providers/omniroute
```

Next session, `hermes doctor` lists the provider under **Provider
Connectivity**.

---

## Using a plugin after install

```bash
hermes model                                  # pick OmniRoute + a model (fetches live /models)
# or non-interactively:
hermes config set model.provider omniroute
hermes config set model.model <model-id>
```

Secret (`OMNIROUTE_API_KEY`) lives in `~/.hermes/.env`; all non-secret
settings go in `config.yaml` via `hermes config set`.

---

## Developing / adding a plugin

Each plugin is a self-contained directory:

```
omniroute/
├── __init__.py     # register_provider(ProviderProfile) at module level
├── plugin.yaml     # manifest — kind: model-provider + requires_env
└── README.md
```

`pyproject.toml` at the repo root declares the `hermes_agent.plugins` entry
point for pip distribution. Add a new plugin by adding a directory + a
`[project.entry-points."hermes_agent.plugins"]` line.

Validate a model-provider before releasing with the actual registration
path (not `hermes plugins doctor`, which checks the general `register(ctx)`
API — model-providers register via module-level `register_provider`):

```python
# from the Hermes venv
from providers import get_provider_profile, list_providers
assert "omniroute" in [p.name for p in list_providers()]
get_provider_profile("omniroute").fetch_models(timeout=12)   # live /models
```

## Releasing

```bash
git add -A && git commit -m "feat: add omniroute model-provider plugin"
git push origin main
```

## License

MIT