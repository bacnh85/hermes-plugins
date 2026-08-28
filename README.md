# hermes-plugins

Plugins for [Hermes Agent](https://hermes-agent.nousresearch.com), published
by **bacnh85**.

The idea: you run Hermes on several machines (CLI, desktop, gateway, LXC/CI,
…). Instead of re-implementing the same provider or backend on each one, this
repo ships ready-to-install plugins so every machine can pick them up in one
command.

Each plugin lives in its own directory with its own `plugin.yaml` manifest.

---

## Available plugins

| Plugin | Install | Kind | What it does |
|---|---|---|---|
| [`omniroute/`](./omniroute) | `python3 install_plugins.py omniroute` (from a clone) | model-provider | Add OmniRoute as a Hermes `model.provider` (OpenAI-compatible routing gateway). |
| [`munin/`](./munin) | `python3 install_plugins.py munin` (from a clone) | memory | Munin Context Core as a Hermes `memory.provider` — long-term memory w/ E2EE + GraphRAG. Pure-stdlib REST (no Node/MCP subprocess). |

---

## Quick start

```bash
git clone https://github.com/bacnh85/hermes-plugins
cd hermes-plugins
python3 install_plugins.py omniroute        # one command per plugin
```

The installer routes each plugin to the directory its own discovery system
reads — **model providers go to `~/.hermes/plugins/model-providers/<name>/`**,
general plugins to `~/.hermes/plugins/<name>/` — prompts for the plugin's
`requires_env` (saving to `~/.hermes/.env`), and for model providers points
`model.provider` at the plugin via `hermes config set`. After restart, the
plugin is live.

```bash
hermes model                                                 # pick the provider + model (live /models fetch)
hermes config set model.provider <plugin-slug>
hermes config set model.model <model-id>
```

> ⚠️ **`hermes plugins install` is NOT the way to install provider plugins
> from this repo.** The built-in installer always installs to
> `~/.hermes/plugins/<name>/` (the general plugins dir) regardless of kind.
> Model providers are discovered from
> `~/.hermes/plugins/model-providers/<name>/` only — a general-dir install
> shows the plugin as "enabled" but the model picker finds no models. Use
> `install_plugins.py` (kind-aware), a directory drop, or a symlink into
> `model-providers/`.

---

## How it works

`install_plugins.py` reads each plugin's `kind` and routes it to the location
its discovery system scans:

- `kind: model-provider` → `~/.hermes/plugins/model-providers/<name>/`
  (provider discovery in `providers/__init__.py::_discover_providers()`)
- `kind: memory` → `~/.hermes/plugins/<name>/` (the general plugins dir —
  Hermes' memory-provider loader scans it; activate with
  `hermes config set memory.provider <name>`)
- anything else → `~/.hermes/plugins/<name>/` (general `PluginManager`)

The `$HERMES_HOME/plugins/model-providers/<name>/` drop-in path is the
core-blessed location: the provider registry scans it at first
`list_providers()` call, **before** the auth/models registries build — so the
provider lands in `CANONICAL_PROVIDERS` (the `hermes model` picker) and
`PROVIDER_REGISTRY` (credential resolution) automatically. The pip
entry-point path (`hermes_agent.plugins` group) works too — see
[Advanced install paths](#advanced-install-paths).

## Advanced install paths

### Directory drop (override path)

```bash
git clone https://github.com/bacnh85/hermes-plugins
mkdir -p ~/.hermes/plugins/model-providers
cp -r hermes-plugins/omniroute ~/.hermes/plugins/model-providers/omniroute
```

Next start picks it up via `providers/__init__.py::_discover_providers()`.

### Pip (entry point)

```bash
~/.hermes/hermes-agent/venv/bin/pip install -e git+https://github.com/bacnh85/hermes-plugins.git#egg=hermes-plugins
```

Entry-point providers are discovered by `providers/__init__.py` (step 0).

### Dev (symlink)

```bash
ln -s "$PWD/omniroute" ~/.hermes/plugins/model-providers/omniroute
```

Edits in the repo apply on the next Hermes start without reinstalling.

---

## Adding a new plugin

A plugin is a self-contained directory:

```
omniroute/
├── __init__.py     # defines the profile + calls register_provider(...) + no-op register(ctx)
├── plugin.yaml     # name, kind: model-provider, requires_env, ...
└── README.md       # per-plugin setup + usage
```

For a new one:

1. Create the directory + `__init__.py` + `plugin.yaml` (unique `name`; keep
   `manifest_version` at `1` so any Hermes can install it).
2. For a model-provider plugin, declare `kind: model-provider` (NOT
   `standalone` — standalone in the general plugins dir registers the
   provider too late for the auth/models registries, so the picker shows no
   models). Module-level `register_provider()` is what gets the provider
   picked up. See `.agents/skills/hermes-plugin-author/`.
3. Add a `[project.entry-points."hermes_agent.plugins"]` line in
   `pyproject.toml` if distributing via pip.
4. Add a row to the plugin table above with the install command.
5. Run the verification snippet in your plugin's README from the Hermes venv.

## License

MIT