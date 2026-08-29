"""Command Code model provider plugin for Hermes.

Command Code (https://commandcode.ai) exposes an OpenAI-compatible
"Provider API" on ``https://api.commandcode.ai/provider/v1`` — pay-at-cost
access to Claude, GPT-5.x, Gemini, GLM, Kimi, DeepSeek, Qwen, MiniMax and
more behind one keyed endpoint. It speaks the OpenAI Chat Completions
surface, so the stock ``chat_completions`` transport applies unchanged —
no custom adapter needed.

This plugin lets Hermes treat Command Code like any other keyed aggregator:

  - API key:   read from ``COMMANDCODE_API_KEY`` in ``~/.hermes/.env``.
  - Base URL:  read from ``COMMANDCODE_BASE_URL`` in ``~/.hermes/.env``
               (optional). Hermes honours the trailing ``_BASE_URL`` env var
               automatically via the profile's ``env_vars``.

Install: ``python3 install_plugins.py commandcode --no-config`` (kind-aware
installer, routes into ``$HERMES_HOME/plugins/model-providers/commandcode/``;
``--no-config`` leaves your current ``model.provider`` untouched).

Usage windows (5-hour / weekly / monthly credits) are served by the
companion ``commandcode-usage`` plugin (kind: standalone) — the general
plugin loader never imports ``kind: model-provider`` modules, so a slash
command cannot live here.

NOTE: ``hermes plugins install bacnh85/hermes-plugins/commandcode`` does
NOT work for this plugin: it lands in the general plugins dir, which
provider discovery never scans, and the general PluginManager does not
import ``kind: model-provider`` modules. The provider then shows "enabled"
in ``hermes plugins list`` but the model picker finds no models.
"""

from __future__ import annotations

import logging
import os

from providers import register_provider
from providers.base import ProviderProfile

logger = logging.getLogger(__name__)

DEFAULT_COMMANDCODE_BASE_URL = "https://api.commandcode.ai/provider/v1"


class CommandCodeProfile(ProviderProfile):
    """Command Code — OpenAI-compatible Provider API (commandcode.ai)."""

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> list[str] | None:
        # Honour a custom endpoint set via the env var before falling back
        # to the caller-supplied base or the profile default. The built-in
        # implementation appends "/models" to whatever base we hand it.
        #
        # Timeout: the default fetch_models timeout is 8s, but remote
        # endpoints behind WAF/CDN layers can take ~10s+ to answer
        # /v1/models cold. At 8s the probe times out and the picker falls
        # back to the static fallback list — raise the ceiling to 30s.
        resolved = (
            os.getenv("COMMANDCODE_BASE_URL", "").strip()
            or base_url
            or self.base_url
        )
        return super().fetch_models(api_key=api_key, base_url=resolved, timeout=timeout)


commandcode = CommandCodeProfile(
    name="commandcode",
    aliases=("cc",),
    display_name="Command Code",
    description="Command Code — OpenAI-compatible Provider API (pay at cost)",
    signup_url="https://commandcode.ai",
    env_vars=("COMMANDCODE_API_KEY", "COMMANDCODE_BASE_URL"),
    base_url=DEFAULT_COMMANDCODE_BASE_URL,
    auth_type="api_key",
    api_mode="chat_completions",
    # Curated agentic subset shown when the live /models fetch fails.
    # Exact ids from the live catalog (2026-08-29); all support tool calling.
    fallback_models=(
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-pro",
        "zai-org/GLM-5.3",
        "z-ai/glm-5.3-flash",
        "moonshotai/Kimi-K3",
        "moonshotai/Kimi-K2.7-Code",
        "claude-sonnet-5",
        "claude-opus-5",
        "gpt-5.6-sol",
        "gpt-5.5",
        "xai/grok-4.6",
        "Qwen/Qwen3.8-Max",
    ),
)

register_provider(commandcode)


def register(ctx) -> None:
    """No-op for the general-plugin loader contract.

    Provider registration is the module-level ``register_provider(commandcode)``
    above. This stub keeps ``hermes plugins list`` clean when the plugin is
    installed via ``hermes plugins install`` (which lands it in the general
    plugins dir, where the loader expects a ``register(ctx)`` callable).
    """
    return None
