# hermes-plugins

Plugins for [Hermes Agent](https://hermes-agent.nousresearch.com), published
by **bacnh85**.

The idea: you run Hermes on several machines (CLI, desktop, gateway, LXC/CI,
…). Instead of re-implementing the same provider or backend on each one, this
repo ships ready-to-install plugins and a small installer so every machine can
pick them up in one command.

Each plugin lives in its own directory with its own `plugin.yaml` manifest.

---

## Available plugins

| Plugin | Kind | What it does |
|---|---|---|
| [`omniroute/`](./omniroute) | model-provider | Add OmniRoute as a Hermes `model.provider` (OpenAI-compatible routing gateway). |

---

## Quick start (recommended)

Clone the repo once, then run the installer on each machine you want to use
the plugin on:

```bash
git clone https://github.com/bacnh85/hermes-plugins
cd hermes-plugins
python install_plugins.py           # installs every plugin in the repo
```

That one command, per machine, will:

- find your Hermes home (`$HERMES_HOME`, or `~/.hermes`),
- copy each plugin into the directory its own discovery system reads (see
  *How install works* below),
- prompt you for any API keys the plugin declares (and save them to `.env`),
- and point Hermes at the provider (for model-provider plugins).

You can also install a subset:

```bash
python install_plugins.py omniroute          # just one plugin
python install_plugins.py omniroute other    # several
python install_plugins.py --no-config        # install only; never touch config or .env
python install_plugins.py --symlink          # symlink instead of copy (dev: live updates)
```

`--symlink` is handy on a dev machine — the plugin directory is linked straight
to the repo, so edits apply on the next Hermes start without reinstalling.

> The installer is **stdlib-only** and works on Windows, macOS, and Linux. No
> pip dependencies required.

---

## How the plugins are installed

Hermes discovers different plugin kinds in different sub-directories of its
plugin home. The installer reads each plugin's `kind` from `plugin.yaml` and
routes the plugin to the right place:

| kind | Installed to |
|---|---|
| `model-provider` | `$HERMES_HOME/plugins/model-providers/<name>` |
| `memory` | `$HERMES_HOME/plugins/memory/<name>` |
| anything else (general plugin) | `$HERMES_HOME/plugins/<name>` |

For example, `omniroute` (a model provider) lands at
`~/.hermes/plugins/model-providers/omniroute`, where Hermes' provider discovery
actually looks.

---

## Installing without the installer

If you'd rather do it by hand, "drop the directory" is the equivalent of what
the installer does under the hood:

```bash
git clone https://github.com/bacnh85/hermes-plugins
mkdir -p ~/.hermes/plugins/model-providers
cp -r hermes-plugins/omniroute ~/.hermes/plugins/model-providers/omniroute
```

Next Hermes start (or `hermes doctor`) picks up the provider.

### Via pip (entry point)

The repo also distributes over pip. Provider plugins are discovered through
the `hermes_agent.plugins` entry-point group:

```bash
~/.hermes/hermes-agent/venv/bin/pip install -e git+https://github.com/bacnh85/hermes-plugins.git#egg=hermes-plugins
hermes plugins enable omniroute          # entry-point plugins are opt-in
```

---

## A note on `hermes plugins install`

You might expect `hermes plugins install <owner>/<repo>` to be the way to
install these. It's a real, official CLI command — but it installs **general**
plugins into the top-level `~/.hermes/plugins/<name>/` directory, which the
**model-provider** discovery system does not read. So for this repo's plugins
(which are model providers), `hermes plugins install` reports success but the
provider never registers.

Verified against the current release:

```
hermes plugins install bacnh85/hermes-plugins/omniroute
# -> "✓ Installed: .../plugins/omniroute"  (env-var prompt, security scan all pass)
# -> but get_provider_profile("omniroute") is None
#    because provider discovery only scans plugins/model-providers/<name>/
```

That's why this repo ships the directory-drop / installer approach above.

---

## Using a plugin after install

Model-provider example (`omniroute`):

```bash
hermes model                                  # pick OmniRoute + a model (fetches live /models)
# or non-interactively:
hermes config set model.provider omniroute
hermes config set model.model <model-id>
```

API keys (secrets) go in `~/.hermes/.env`; all non-secret settings go in
`config.yaml` via `hermes config set` (never hand-edit `config.yaml`).

See each plugin's own README for specific env vars and usage.

---

## Adding a new plugin to this repo

A plugin is a self-contained directory with a `plugin.yaml` manifest:

```
omniroute/
├── __init__.py     # registers the provider/plugin (module-level side effect)
├── plugin.yaml     # name, kind, requires_env, ...
└── README.md       # per-plugin setup + usage
```

To add a new one:

1. Create the directory + `__init__.py` + `plugin.yaml` (set a unique `name`;
   keep `manifest_version` at `1` so any Hermes can install it).
2. Add a `[project.entry-points."hermes_agent.plugins"]` line in
   `pyproject.toml` if distributing via pip.
3. Add a row to the plugin table above.

### Validating a model-provider plugin

`hermes plugins doctor` only checks general `register(ctx)` plugins; model
providers register at module level, so verify them directly from the Hermes
venv:

```python
from providers import get_provider_profile, list_providers
assert "omniroute" in [p.name for p in list_providers()]
get_provider_profile("omniroute").fetch_models(timeout=12)   # live /models probe
```

---

## License

MIT