"""OmniRoute model provider plugin for Hermes.

OmniRoute is a free, OpenAI-compatible AI gateway that fronts many upstream
LLM providers/models behind one endpoint, with quota-aware auto-fallback and
cross-protocol translation. It speaks the OpenAI Chat Completions surface on
``/v1``, so the stock ``chat_completions`` transport applies unchanged — no
custom adapter needed.

This plugin lets Hermes treat an OmniRoute instance like any other keyed
aggregator:

  - API key:   read from ``OMNIROUTE_API_KEY`` in ``~/.hermes/.env``.
  - Base URL:  read from ``OMNIROUTE_BASE_URL`` in ``~/.hermes/.env``
               (required). Hermes honours the trailing ``_BASE_URL`` env var
               automatically via the profile's ``env_vars``.
               Point it at your instance: e.g.
               ``OMNIROUTE_BASE_URL=https://omniroute.bacnh.com/v1`` for a
               remote install or ``http://localhost:20128/v1`` locally.

Install: ``hermes plugins install bacnh85/hermes-plugins/omniroute``.

Discovery: the module calls ``register_provider()`` at import time. Under
``hermes plugins install`` + ``hermes plugins enable omniroute`` the general
plugin manager imports the file (plugin manifest ``kind: standalone``) and the
profile lands in ``providers.registry``. The directory-drop path
(``$HERMES_HOME/plugins/model-providers/omniroute/``) and the pip entry-point
path (pyproject ``hermes_agent.plugins`` group) keep working as before.
"""

from __future__ import annotations

import logging
import os

from providers import register_provider
from providers.base import ProviderProfile

logger = logging.getLogger(__name__)

# Self-hosted default (the OpenAI-compatible /v1 surface of a local 9router /
# OmniRoute install). A real deployment always sets OMNIROUTE_BASE_URL in
# ~/.hermes/.env — remote instances like https://omniroute.bacnh.com/v1 or a
# local http://localhost:20128/v1.
DEFAULT_OMNIROUTE_BASE_URL = "http://localhost:20128/v1"


class OmniRouteProfile(ProviderProfile):
    """OmniRoute — OpenAI-compatible multi-provider AI gateway."""

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> list[str] | None:
        # Honour a remote/custom instance set via the env var before falling
        # back to the caller-supplied base or the profile default. The built-in
        # implementation appends "/models" to whatever base we hand it.
        #
        # Timeout: the default fetch_models timeout is 8s, but OmniRoute
        # instances (especially self-hosted or reverse-proxied ones) can take
        # ~10s+ to answer /v1/models on a cold start. At the default timeout
        # the probe times out and the picker falls back to the static
        # auto/* fallback models — "can't select models". Raise the ceiling
        # to 30s so the live catalog actually loads.
        resolved = (
            os.getenv("OMNIROUTE_BASE_URL", "").strip()
            or base_url
            or self.base_url
        )
        return super().fetch_models(api_key=api_key, base_url=resolved, timeout=timeout)


omniroute = OmniRouteProfile(
    name="omniroute",
    aliases=("omni",),
    display_name="OmniRoute",
    description="OmniRoute — free OpenAI-compatible multi-provider AI gateway",
    signup_url="https://omniroute.online/",
    env_vars=("OMNIROUTE_API_KEY", "OMNIROUTE_BASE_URL"),
    base_url=DEFAULT_OMNIROUTE_BASE_URL,
    auth_type="api_key",
    api_mode="chat_completions",
    fallback_models=(
        "auto/best-chat",
        "auto/best-fast",
        "auto/best-reasoning",
        "auto/best-vision",
    ),
)

register_provider(omniroute)


def register(ctx) -> None:
    """No-op for the general-plugin loader contract.

    Provider registration is the module-level ``register_provider(omniroute)``
    above. This stub keeps ``hermes plugins list`` clean when the plugin is
    installed via ``hermes plugins install`` (which lands it in the general
    plugins dir, where the loader expects a ``register(ctx)`` callable).
    """
    return None