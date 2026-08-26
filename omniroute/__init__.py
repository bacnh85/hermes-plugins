"""OmniRoute model provider plugin for Hermes.

OmniRoute is a free, OpenAI-compatible AI gateway that fronts many upstream
LLM providers/models behind one endpoint, with quota-aware auto-fallback and
cross-protocol translation. It speaks the OpenAI Chat Completions surface on
``/v1``, so the stock ``chat_completions`` transport applies unchanged — no
custom adapter needed.

This plugin lets Hermes treat an OmniRoute instance like any other keyed
aggregator:

  - API key:   read from ``OMNIROUTE_API_KEY`` in ``~/.hermes/.env``.
  - Base URL:  read from ``OMNIROUTE_BASE_URL`` when set (the runtime
               auto-wires a trailing ``_BASE_URL`` env var from the
               profile's ``env_vars``); falls back to the hosted default
               ``https://omniroute.online/v1``. For a self-hosted OmniRoute
               instance, set ``OMNIROUTE_BASE_URL=http://localhost:20128/v1``.

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

# Hosted OmniRoute API base (the OpenAI-compatible /v1 surface).
# Override via OMNIROUTE_BASE_URL in ~/.hermes/.env for self-hosted or remote
# instances — e.g. http://localhost:20128/v1 for a local install.
DEFAULT_OMNIROUTE_BASE_URL = "https://omniroute.online/v1"


class OmniRouteProfile(ProviderProfile):
    """OmniRoute — OpenAI-compatible multi-provider AI gateway."""

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        # Honour a remote/custom instance set via the env var before falling
        # back to the caller-supplied base or the profile default. The built-in
        # implementation appends "/models" to whatever base we hand it.
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