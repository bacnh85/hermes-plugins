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
| [`omniroute/`](./omniroute) | `hermes plugins install bacnh85/hermes-plugins/omniroute` | standalone | Add OmniRoute as a Hermes `model.provider` (OpenAI-compatible routing gateway). |

---

## Quick start

Run the install command for each plugin you want, per machine:

```bash
hermes plugins install bacnh85/hermes-plugins/omniroute          # this repo's plugins
# ...one install per plugin; the subdir in the URL picks which one...
```

The installer prompts for the plugin's `requires_env` (saving to
`~/.hermes/.env`), then asks "Enable '<plugin>' now?" — answer `y`. After
`hermes gateway restart`, the plugin is live.

```bash
hermes model                                                 # pick the provider + model (live /models fetch)
hermes config set model.provider <plugin-slug>
hermes config set model.model <model-id>
```

Each `hermes plugins install` clones the whole repo (the installer's
`<owner>/<repo>/<subdir>` shorthand only takes a subdir of the clone — the
clone itself is always the full repo). The installer has **no plugin
picker**: `<repo>/<subdir>` is mandatory, and the subdir must contain a
`plugin.yaml`.

---

## How it works

`hermes plugins install` clones the subdir into `~/.hermes/plugins/<name>/`
and prompts for `requires_env`. For provider plugins here, the manifest
declares `kind: standalone` — so the general plugin loader imports the
module, which calls `register_provider()` at import time and registers the
provider in `providers.registry`. Enable it with `hermes plugins enable
omniroute`, restart the gateway, done.

The `$HERMES_HOME/plugins/model-providers/<name>/` drop-in path and the pip
entry-point path (`hermes_agent.plugins` group) keep working too — see
[Advanced install paths](#advanced-install-paths).

---

## Migrating from a pre-1.1 manual install

If you previously dropped the plugin into `~/.hermes/plugins/model-providers/omniroute/` (per the old README), it's safe to leave in place: the general-dir copy is loaded later in startup and wins via last-writer-wins. To clean up:

```bash
rm -rf ~/.hermes/plugins/model-providers/omniroute
hermes gateway restart
```

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
hermes plugins enable omniroute
```

Entry-point plugins are opt-in via `plugins.enabled`.

### Dev (symlink)

```bash
ln -s "$PWD/omniroute" ~/.hermes/plugins/omniroute
hermes plugins enable omniroute
```

Edits in the repo apply on the next Hermes start without reinstalling.

---

## Adding a new plugin

A plugin is a self-contained directory:

```
omniroute/
├── __init__.py     # defines the profile + calls register_provider(...) + no-op register(ctx)
├── plugin.yaml     # name, kind: standalone, requires_env, ...
└── README.md       # per-plugin setup + usage
```

For a new one:

1. Create the directory + `__init__.py` + `plugin.yaml` (unique `name`; keep
   `manifest_version` at `1` so any Hermes can install it).
2. For a model-provider-style plugin, declare `kind: standalone` (NOT
   `kind: model-provider` — the general loader skips model-provider imports
   in the top-level plugins dir). Module-level `register_provider()` is what
   gets the provider picked up. See `.agents/skills/hermes-plugin-author/`.
3. Add a `[project.entry-points."hermes_agent.plugins"]` line in
   `pyproject.toml` if distributing via pip.
4. Add a row to the plugin table above with the `hermes plugins install`
   command.
5. Run the verification snippet in your plugin's README from the Hermes venv.

## License

MIT