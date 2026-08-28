# hermes-plugins

Plugins for [Hermes Agent](https://hermes-agent.nousresearch.com), published
by **bacnh85**.

The idea: you run Hermes on several machines (CLI, desktop, gateway, LXC/CI,
…). Instead of re-implementing the same provider or backend on each one, this
repo ships ready-to-install plugins so every machine can pick them up in one
command.

Each plugin lives in its own directory with its own `plugin.yaml` manifest.

---

## TL;DR — how to install

**Everything except model-providers — use the native Hermes CLI:**

```bash
hermes plugins install bacnh85/hermes-plugins/munin
hermes plugins update munin          # later updates
```

Memory plugins are discovered from the general plugins dir, which is exactly
where the native CLI puts them. Activate a memory provider explicitly:

```bash
hermes config set memory.provider munin
hermes gateway restart
```

**Model-providers (`omniroute`) — use this repo's installer:**

```bash
git clone https://github.com/bacnh85/hermes-plugins
cd hermes-plugins
python3 install_plugins.py omniroute
```

or without cloning:

```bash
curl -fsSL https://raw.githubusercontent.com/bacnh85/hermes-plugins/main/install_plugins.py | python3 - omniroute
```

---

## Available plugins

| Plugin | Install | Kind | What it does |
|---|---|---|---|
| [`omniroute/`](./omniroute) | `python3 install_plugins.py omniroute` (from a clone, or the curl one-liner above) | model-provider | Add OmniRoute as a Hermes `model.provider` (OpenAI-compatible routing gateway). |
| [`munin/`](./munin) | `hermes plugins install bacnh85/hermes-plugins/munin` | memory | Munin Context Core as a Hermes `memory.provider` — long-term memory w/ E2EE + GraphRAG. Pure-stdlib REST (no Node/MCP subprocess). |

---

## Why model-providers are special

Hermes has two plugin discovery systems, and the native installer's
destination only matches one of them:

| Plugin kind | Discovered from | Native `hermes plugins install` works? |
|---|---|---|
| `memory` (and general) | `~/.hermes/plugins/<name>/` | ✅ yes — that's where it installs |
| `model-provider` | `~/.hermes/plugins/model-providers/<name>/` **only** | ❌ no — it installs to the general dir, which provider discovery never scans |

The failure mode is silent and confusing: a native-installed provider shows
as **"enabled"** in `hermes plugins list` and even passes `hermes doctor`,
but `hermes model` finds no models — because
`providers/__init__.py::_discover_providers()` scans `model-providers/`
only, and the general PluginManager deliberately never imports
`kind: model-provider` modules (it just records their manifests).

`install_plugins.py` exists for exactly this one case: it reads each
plugin's `kind`, and

- `model-provider` → installs locally into
  `~/.hermes/plugins/model-providers/<name>/` (the location provider
  discovery reads **before** the auth/models registries build, so the
  provider lands in `CANONICAL_PROVIDERS` and the `hermes model` picker
  automatically), prompts for the plugin's `requires_env` into
  `~/.hermes/.env`, and points `model.provider` at it via
  `hermes config set`;
- everything else → delegates to the native CLI
  (`hermes plugins install bacnh85/hermes-plugins/<name> --enable`) and
  prints the activation hint. No duplication, no stale copies.

Useful installer flags:

```bash
python3 install_plugins.py                 # handle ALL plugins in the repo
python3 install_plugins.py omniroute munin # several
python3 install_plugins.py --symlink       # dev: symlink instead of copy (live edits)
python3 install_plugins.py --refresh       # overwrite an existing model-provider install
python3 install_plugins.py --no-enable     # native installs: install disabled
python3 install_plugins.py --no-config     # model-providers: don't touch config / .env
```

After any install: `hermes gateway restart`, then verify with
`hermes doctor` / `hermes model` (provider picks) or
`hermes memory status` (memory provider shows `← active`).

---

## Advanced install paths

### Directory drop (override path, model-providers)

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

### Dev (symlink, model-providers)

```bash
ln -s "$PWD/omniroute" ~/.hermes/plugins/model-providers/omniroute
```

Edits in the repo apply on the next Hermes start without reinstalling.
(`install_plugins.py --symlink omniroute` does the same.)

> Note: if a memory/general plugin was ever symlinked or manually copied to
> `~/.hermes/plugins/<name>/`, the native installer refuses to overwrite it
> ("resolves outside the plugins directory" when a symlink points elsewhere).
> Remove the old entry first: `rm ~/.hermes/plugins/<name>`.

---

## Updating

- Native-installed plugins (munin): `hermes plugins update munin`.
- Model-providers: `git pull` in your clone, then
  `python3 install_plugins.py omniroute --refresh` (or just rely on the
  symlink if you installed with `--symlink`).

---

## Adding a new plugin

A plugin is a self-contained directory:

```
munin/
├── __init__.py     # provider logic; memory: register_memory_provider(...)
├── plugin.yaml     # name, kind, requires_env, ...
└── README.md       # per-plugin setup + usage
```

1. Create the directory + `__init__.py` + `plugin.yaml` (unique `name`; keep
   `manifest_version` at `1` so any Hermes can install it).
2. Pick the kind deliberately:
   - `memory` / general → native CLI installs it correctly; nothing extra.
     Memory providers must expose `register_memory_provider` /
     `MemoryProvider` in `__init__.py` (the discovery heuristic scans the
     source) and are activated by the user with
     `hermes config set memory.provider <name>`.
   - `model-provider` → the installer routes it into `model-providers/`.
     Declare `kind: model-provider` and call `register_provider(...)`
     at module level. Do NOT use `kind: standalone` for a provider:
     the general loader imports it too late — `hermes_cli.auth` and
     `hermes_cli.models` build their registries before plugin discovery
     runs, so the picker shows no models. See
     `.agents/skills/hermes-plugin-author/`.
3. Add a `[project.entry-points."hermes_agent.plugins"]` line in
   `pyproject.toml` if distributing via pip.
4. Add a row to the plugin table above with the correct install command.
5. Run the verification snippet in your plugin's README from the Hermes venv.

## License

MIT
