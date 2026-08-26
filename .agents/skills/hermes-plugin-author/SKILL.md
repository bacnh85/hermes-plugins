---
name: hermes-plugin-author
description: Author a Hermes Agent plugin in the bacnh85/hermes-plugins repo (provider plugins, directory layout, manifest schema, install/verify). Use when adding or editing a plugin under this repo, when the user asks to make a new plugin, or when wiring an OmniRoute/OpenAI-compatible gateway into Hermes.
---

# Author a Hermes plugin in this repo

This repo ships `hermes plugins install`-friendly plugins. The non-obvious part is a **kind rule** that flips the discovery path: use it wrong and the provider silently never registers. Read it once, then everything else is mechanical.

## When this applies

- Adding a new plugin to `bacnh85/hermes-plugins/`.
- Editing `omniroute/` or any sibling plugin directory.
- Wiring a new provider/gateway into Hermes through this repo.

Do not use this skill for general Hermes internals outside the repo (use the hermes-agent docs at https://hermes-agent.nousresearch.com instead).

## Plugin directory contract

```
<plugin-name>/
├── __init__.py     # defines the profile, calls register_provider(...) at import, no-op register(ctx)
├── plugin.yaml     # manifest: name, kind: standalone, requires_env, version, ...
└── README.md       # install + env table + verify snippet
```

## The kind rule (the hard-won one)

Use **`kind: standalone`**, not `kind: model-provider`. Why:

- `hermes plugins install` always drops the plugin at `~/.hermes/plugins/<name>/` (top-level general dir). See `hermes_cli/plugins_cmd.py::_install_plugin_core` (~line 715) and `_sanitize_plugin_name` (~line 159, rejects subdirs).
- The general loader (`hermes_cli/plugins.py::_discover_and_load_inner` ~line 4135–4146) explicitly *skips* `kind: model-provider` plugins in the top-level dir — it records them for introspection only.
- Provider discovery (`providers/__init__.py::_discover_providers`) only scans `$HERMES_HOME/plugins/model-providers/<name>/` + pip entry points. So a `kind: model-provider` plugin at the top-level path is **never imported** — provider never registers, even though the CLI printed "✓ Installed" and "✓ Enabled".

With `kind: standalone` + the plugin in `plugins.enabled`, the general loader imports `__init__.py` and module-level `register_provider(profile)` fires → the provider shows up in `hermes model`, `hermes doctor`, and `get_provider_profile()`.

The `model-providers/<name>/` drop-in path and the pip entry-point path (`hermes_agent.plugins` group, see `pyproject.toml`) keep working too.

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

    def fetch_models(self, *, api_key=None, base_url=None, timeout=8.0):
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
    above. This stub keeps hermes plugins list clean when installed via
    hermes plugins install (lands in the general plugins dir, where the
    loader expects register(ctx)).
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
kind: standalone              # see kind rule above
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
3. Add a row to root `README.md`'s plugin table with the `hermes plugins install bacnh85/hermes-plugins/<name>` command.
4. Add verify snippet to the plugin's README.
5. Bump `version`.
6. Run the verification below from the Hermes venv.

## Multi-plugin install (the user's question)

For a repo that ships multiple top-level plugin dirs (e.g. `omniroute/`,
`other/`), the install is **one `hermes plugins install` per plugin**:

```bash
hermes plugins install bacnh85/hermes-plugins/omniroute
hermes plugins install bacnh85/hermes-plugins/other-plugin
```

`hermes plugins install` accepts `<owner>/<repo>/<subdir>` shorthand; the
subdir picks the plugin. **There is no interactive plugin picker.** A bare
`<owner>/<repo>` (no subdir) won't work because the repo root has no
`plugin.yaml` — the install would fail. Each install clones the full repo
once and takes only the named subdir; on a multi-machine fleet, expect one
clone per plugin per machine (small repo, but it adds up if the repo
grows).

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
hermes plugins install bacnh85/hermes-plugins/<plugin-slug>
# answer prompts, say y to "Enable <plugin-slug> now?"
hermes gateway restart
hermes plugins list                              # no "no register() function" error
hermes doctor                                    # provider connectivity probe
hermes model                                     # <PluginName> appears in picker
```

## Footgun recap

- `kind: model-provider` → CLI install reports success, provider never registers. Always use `kind: standalone` for `hermes plugins install`-friendly plugins.
- Forgetting `def register(ctx)` → "no register() function" warning + error field in `hermes plugins list`. Add the no-op.
- Hardcoding `http://localhost:...` as the default base URL → fails on every machine that isn't the gateway host. Use a hosted default and let env vars override for self-host.
- Editing `env_vars` but forgetting the docstring's env-var list → install prompt won't offer the new var, runtime won't wire the auto-`_BASE_URL` suffix.