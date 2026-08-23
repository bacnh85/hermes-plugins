"""OmniRoute model provider plugin for Hermes.

OmniRoute is a free, self-hostable, OpenAI-compatible AI gateway that fronts
many upstream LLM providers/models behind one endpoint, with quota-aware
auto-fallback and cross-protocol translation. It speaks the OpenAI Chat
Completions surface on /v1, so the stock chat_completions transport applies
unchanged — no custom adapter needed.

This plugin lets Hermes treat an OmniRoute instance like any other keyed
aggregator:

  - API key:     read from ``OMNIROUTE_API_KEY`` in ``~/.hermes/.env``.
  - Base URL:    read from ``OMNIROUTE_BASE_URL`` when set (the runtime
                 auto-wires a trailing ``_BASE_URL`` env var from the
                 profile's ``env_vars``); falls back to the documented
                 self-host default ``http://localhost:20128/v1`` for a local
                 install, or the ``base_url`` the caller resolves.

Discovery: ``providers/__init__.py`` loads this when the directory lives at
``$HERMES_HOME/plugins/model-providers/omniroute/``, or via the
``hermes_agent.plugins`` pip entry-point group (repo pyproject.toml).
"""

from __future__ import annotations

import logging
import os

from providers import register_provider
from providers.base import ProviderProfile

logger = logging.getLogger(__name__)

# OmniRoute's documented self-hosted API endpoint (default port).
DEFAULT_OMNIROUTE_BASE_URL = "http://localhost:20128/v1"


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
        # back to the caller-supplied base or the local default. The built-in
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