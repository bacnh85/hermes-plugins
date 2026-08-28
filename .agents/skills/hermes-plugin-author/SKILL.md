---
name: hermes-plugin-author
description: Author a Hermes Agent plugin in the bacnh85/hermes-plugins repo (provider plugins, directory layout, manifest schema, install/verify). Use when adding or editing a plugin under this repo, when the user asks to make a new plugin, or when wiring an OmniRoute/OpenAI-compatible gateway into Hermes.
---

# Author a Hermes plugin in this repo

This repo ships plugins that install cleanly on any Hermes machine. The
non-obvious part is a **kind rule** that flips the discovery path — and with
it the install command: memory/general plugins use the native
`hermes plugins install` CLI; model-providers must go through this repo's
`install_plugins.py`. Use the wrong pair and the provider silently never
registers. Read it once, then everything else is mechanical.

## When this applies

- Adding a new plugin to `bacnh85/hermes-plugins/`.
- Editing `omniroute/` or any sibling plugin directory.
- Wiring a new provider/gateway into Hermes through this repo.

Do not use this skill for general Hermes internals outside the repo (use the hermes-agent docs at https://hermes-agent.nousresearch.com instead).

## Plugin directory contract

```
<plugin-name>/
├── __init__.py     # defines the profile, calls register_provider(...) at import, no-op register(ctx)
├── plugin.yaml     # manifest: name, kind: model-provider, requires_env, version, ...
└── README.md       # install + env table + verify snippet
```

## The kind rule (the hard-won one)

Use **`kind: model-provider`**, not `kind: standalone`. Why:

- Model-provider plugins are discovered by `providers/__init__.py::_discover_providers()`, which scans **only** `$HERMES_HOME/plugins/model-providers/<name>/` (+ pip entry points). The provider registry imports the module at first `list_providers()` call — **before** `hermes_cli.auth.PROVIDER_REGISTRY` and `hermes_cli.models.CANONICAL_PROVIDERS` build, so the provider lands in the picker and credential resolution automatically.
- `kind: standalone` in the **general** plugins dir (`~/.hermes/plugins/<name>/`) does get imported by the general loader (`hermes_cli/plugins.py::_discover_and_load_inner`), and module-level `register_provider()` fires — but **too late**: `PROVIDER_REGISTRY`/`CANONICAL_PROVIDERS` were already built at import time and are never re-extended. Result: the provider shows as "enabled" in `hermes plugins list` and even appears in doctor, but `hermes model` finds **no models** — only the static `auto/*` fallback models.
- `kind: model-provider` in the **general** dir is worse: the general loader skips it entirely (records for introspection only) and the provider registry never scans there → nothing registers at all.

So the plugin must live at `$HERMES_HOME/plugins/model-providers/<name>/`.

**Install paths** (all land in `model-providers/`):
- `python3 install_plugins.py <name>` from a clone of this repo — routes
  `kind: model-provider` plugins here, and delegates everything else to the
  native CLI
- directory drop: `cp -r <name> ~/.hermes/plugins/model-providers/<name>`
- dev symlink: `ln -s "$PWD/<name>" ~/.hermes/plugins/model-providers/<name>`
- pip entry point (`hermes_agent.plugins` group, see `pyproject.toml`)

**`hermes plugins install bacnh85/hermes-plugins/<name>` does NOT work for
provider plugins**: it always installs to `~/.hermes/plugins/<name>/` (the
general dir) regardless of kind. The CLI prints "✓ Installed / ✓ Enabled"
but the provider never registers. It IS the canonical path for every other
kind (`memory`, general) — those discovery systems read the general dir.

## Provider profile template

```python
"""<PluginName> model provider plugin for Hermes.

Describe the gateway, the env vars, and the install command here.
"""

from __future__ import annotations

import logging
import os

from providers import register_provider
from providers.base import ProviderProfile

logger = logging.getLogger(__name__)

DEFAULT_<PLUGIN>_BASE_URL = "https://example.com/v1"  # not http://localhost — gateways are rarely same-host


class <PluginName>Profile(ProviderProfile):
    """<One-line description>."""

    def fetch_models(self, *, api_key=None, base_url=None, timeout=30.0):
        # 30s default: self-hosted / reverse-proxied endpoints can take ~10s+
        # cold to answer /v1/models. At the 8s base default the probe times
        # out and the picker falls back to static models ("can't select").
        resolved = os.getenv("<PLUGIN>_BASE_URL", "").strip() or base_url or self.base_url
        return super().fetch_models(api_key=api_key, base_url=resolved, timeout=timeout)


profile = <PluginName>Profile(
    name="<plugin-slug>",
    aliases=("<short>",),
    display_name="<DisplayName>",
    description="<one-line>",
    signup_url="<signup or homepage>",
    env_vars=("<PLUGIN>_API_KEY", "<PLUGIN>_BASE_URL"),
    base_url=DEFAULT_<PLUGIN>_BASE_URL,
    auth_type="api_key",
    api_mode="chat_completions",   # or "anthropic_messages", "responses", "bedrock_converse"
    fallback_models=("auto/best-chat",),
)

register_provider(profile)


def register(ctx) -> None:
    """No-op for the general-plugin loader contract.

    Provider registration is the module-level register_provider(profile)
    above. This stub only matters if the directory ever gets imported by
    the general loader (e.g. a stray copy in the general plugins dir) —
    the supported install is model-providers/ via install_plugins.py.
    """
    return None
```

Field notes:
- `env_vars` is the canonical list — runtime auto-wires `{NAME}_BASE_URL` from it.
- `auth_type="api_key"` means Bearer token via env. Other types exist for OAuth etc.
- For `api_mode`, match the protocol the upstream speaks (`chat_completions` for OpenAI-compatible; `anthropic_messages` for Anthropic; etc.).
- `fetch_models` override is only needed when the env var overrides `base_url` — keep the override minimal (`os.getenv(...).strip() or base_url or self.base_url`).

## plugin.yaml manifest schema

```yaml
name: <plugin-slug>           # unique; matches directory name
kind: model-provider          # see kind rule above
version: 0.1.0                # bump on each publish
description: <one-line>
author: <github user>
license: MIT
homepage: <url>
tags: [<provider, gateway>]
requires_env:
  - name: <PLUGIN>_API_KEY
    description: "<plain-language purpose>"
    url: "<signup or keys page>"
    secret: true               # masked input via getpass
  - name: <PLUGIN>_BASE_URL
    description: "Base URL (Enter for hosted default <url>; self-host: <url>)"
    url: "<docs>"
    secret: false              # plain input; Enter = keep default
```

`requires_env` rich format keys: `name`, `description`, `url`, `secret` (verified in `hermes_cli/plugins_cmd.py::_prompt_plugin_env_vars` ~line 430). No `optional` flag — empty Enter = skip (default applies at runtime).

## Checklist for a new plugin

1. Create `<name>/` with `__init__.py` (template above), `plugin.yaml`, `README.md`.
2. Add to `pyproject.toml` entry points: `[project.entry-points."hermes_agent.plugins"]` line + (optional) `[project.entry-points."hermes_agent.plugin_capabilities"]` line.
3. Add a row to root `README.md`'s plugin table with the correct install
   command (native CLI for memory/general, `install_plugins.py <name>` for
   model-providers).
4. Add verify snippet to the plugin's README.
5. Bump `version`.
6. Run the verification below from the Hermes venv.

## Multi-plugin install

One command handles every plugin in the repo — the installer routes each by
its `kind`:

```bash
git clone https://github.com/bacnh85/hermes-plugins
cd hermes-plugins
python3 install_plugins.py            # all plugins
python3 install_plugins.py munin      # or just some
```

Under the hood: `model-provider` → local install into
`plugins/model-providers/`; everything else → delegates to
`hermes plugins install bacnh85/hermes-plugins/<name>`. Do not run
`hermes plugins install` directly for provider plugins — it always installs
to the general `plugins/<name>/` dir, which model-provider discovery never
scans (see the kind rule).

## Verification (from `~/.hermes/hermes-agent/` venv)

YAML parses:
```bash
python -c "import yaml; yaml.safe_load(open('omniroute/plugin.yaml'))"
```

Live provider check:
```bash
cd ~/.hermes/hermes-agent && python -c "
import sys; sys.path.insert(0,'.'); sys.path.insert(0,'<path to this repo>')
import omniroute    # or your plugin
from providers import get_provider_profile
p = get_provider_profile('<plugin-slug>')
print(p.name, p.base_url, p.env_vars)
"
```

End-to-end on the target machine:
```bash
cd hermes-plugins
python3 install_plugins.py <plugin-slug>     # routes into plugins/model-providers/
hermes gateway restart
hermes plugins list                          # no "no register() function" error
hermes doctor                                # provider connectivity probe
hermes model                                 # <PluginName> appears in picker WITH models
```

## Footgun recap

- `hermes plugins install .../<provider-plugin>` installs into the general dir — provider never becomes selectable in the picker. Provider plugins go through `install_plugins.py` / drop / symlink into `plugins/model-providers/`. (Memory/general plugins: the native CLI is correct.)
- Forgetting `def register(ctx)` → "no register() function" warning + error field in `hermes plugins list`. Add the no-op.
- Hardcoding `http://localhost:...` as the default base URL → fails on every machine that isn't the gateway host. Use a hosted default and let env vars override for self-host.
- Editing `env_vars` but forgetting the docstring's env-var list → install prompt won't offer the new var, runtime won't wire the auto-`_BASE_URL` suffix.
- Keeping the inherited 8s `fetch_models` timeout → slow self-hosted endpoints time out and the picker falls back to static `auto/*` models ("can't select models"). Override with a 30s default.