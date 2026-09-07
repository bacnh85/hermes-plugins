"""Z.AI Coding Plan (Anthropic) provider profile.

Registers the ``zai-anthropic`` provider: Z.ai's GLM Coding Plan reached
through the **Anthropic Messages API** surface (``https://api.z.ai/api/anthropic``)
— the same surface the ZCode desktop client uses, instead of the
OpenAI-compatible ``/api/coding/paas/v4`` the bundled ``zai`` provider uses.

Live-verified 2026-09-07 against api.z.ai with a real coding-plan key:

- plain ``/v1/messages`` call → HTTP 200 (thinking blocks ON by default)
- manual thinking ``{"type": "enabled", "budget_tokens": N}`` → HTTP 200
  (this is what Hermes sends non-Claude models through
  ``agent/anthropic_adapter.py``, and the endpoint accepts it)
- adaptive ``{"type": "adaptive"}`` + ``output_config.effort`` → HTTP 200
- ``speed: "fast"`` + ``anthropic-beta: fast-mode-2026-02-01`` → HTTP 200
- ``GET /v1/models`` → 10 GLM model ids
- streaming (``stream: true``) + ``thinking: {"type": "disabled"}`` +
  ``interleaved-thinking``/``fine-grained-tool-streaming``/``fast-mode``
  betas → HTTP 200 SSE

Companion plugin ``zai-anthropic-zcode`` (kind: standalone, same repo) adds
the ZCode desktop-client request parity — identity headers + Ed25519/PoW
request signing + fast mode — via ``llm_request`` middleware, opt-out with
``ZAI_ANTHROPIC_SIGNING=0`` / ``ZAI_ANTHROPIC_SPEED=standard``.

Env wiring (``hermes_cli/auth.py`` auto-extends PROVIDER_REGISTRY from
``env_vars``): ``ZAI_ANTHROPIC_API_KEY`` → key, ``ZAI_ANTHROPIC_BASE_URL``
→ base URL override.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request

from providers import register_provider
from providers.base import ProviderProfile, _profile_user_agent

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.z.ai/api/anthropic/v1"
# NOTE the /v1 suffix: Hermes' Anthropic client strips a trailing /v1 before
# building the Messages URL, while hermes_cli.models.probe_api_models (used by
# the `hermes model` setup flow) does NOT — it probes {base}/models first, and
# api.z.ai answers that path with HTTP 200 and an EMPTY list (the real catalog
# lives at /v1/models). With /v1 in the base the probe hits /v1/models directly
# and the picker lists all 10 models (verified 2026-09-07).

_ENV = ("ZAI_ANTHROPIC_API_KEY", "ZAI_ANTHROPIC_BASE_URL")

# Curated from the live catalog (GET /v1/models on 2026-09-07 returned
# glm-4.5, glm-4.5-air, glm-4.6, glm-4.7, glm-5, glm-5-turbo, glm-5.1,
# glm-5.2, glm-5.3, glm-5.3-flash). Agentic/tool-calling subset only.
FALLBACK_MODELS = (
    "glm-5.3",
    "glm-5.3-flash",
    "glm-5.2",
    "glm-5.1",
    "glm-5-turbo",
    "glm-4.7",
)

# GLM-5.2/5.3 ship a 1M context window (Z.ai docs, verified 2026-08-14);
# older GLM models are ~200K. Hermes' model_metadata already knows these
# ids — this only matters if upstream metadata ever rots.


def _resolved_base_url() -> str:
    """Env override, else the /v1-suffixed default (see DEFAULT_BASE_URL).

    A user-set ZAI_ANTHROPIC_BASE_URL without /v1 still works everywhere:
    the profile's own fetch_models and the runtime client handle it; only the
    setup-flow's raw probe prefers the /v1 form.
    """
    raw = (os.getenv("ZAI_ANTHROPIC_BASE_URL") or "").strip()
    return raw.rstrip("/") if raw else DEFAULT_BASE_URL


def _models_endpoint(base_url: str | None) -> str:
    """Catalog URL for any base form: {base}/v1/models, {base}/models for a
    base already ending in /v1."""
    base = (base_url or "").strip().rstrip("/") or _resolved_base_url()
    if base.endswith("/v1"):
        return f"{base}/models"
    return f"{base}/v1/models"


def _fetch_models(
    timeout: float = 30.0,
    base_url: str | None = None,
    api_key: str | None = None,
) -> list[str] | None:
    """Fetch the live catalog from ``{base}/v1/models`` (x-api-key auth).

    The Anthropic-style models endpoint is NOT OpenAI-shaped: it answers
    ``{"data": [{"id": ...}], "hasMore": ...}`` (camelCase pagination), which
    the base-class parser would still read fine, but auth differs
    (``x-api-key``, not Bearer) and the 30s timeout matters for cold
    CDN-fronted endpoints — hence the override.
    """
    url = _models_endpoint(base_url)
    api_key = (api_key or os.getenv("ZAI_ANTHROPIC_API_KEY") or "").strip()
    try:
        req = urllib.request.Request(url)
        if api_key:
            req.add_header("x-api-key", api_key)
        req.add_header("Accept", "application/json")
        req.add_header("anthropic-version", "2023-06-01")
        req.add_header("User-Agent", _profile_user_agent())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        items = data.get("data", []) if isinstance(data, dict) else data
        return [m["id"] for m in items if isinstance(m, dict) and "id" in m]
    except Exception as exc:
        logger.debug("fetch_models(zai-anthropic): %s", exc)
        return None


class ZaiAnthropicProfile(ProviderProfile):
    """Z.AI Coding Plan — GLM via the Anthropic Messages surface."""

    def fetch_models(
        self,
        *,
        api_key=None,
        base_url=None,
        timeout=30.0,
    ):
        return _fetch_models(timeout=timeout, base_url=base_url, api_key=api_key)


zai_anthropic = ZaiAnthropicProfile(
    name="zai-anthropic",
    aliases=("zai-anthropic-chat",),
    api_mode="anthropic_messages",
    env_vars=_ENV,
    display_name="Z.AI GLM (Anthropic)",
    description="Z.AI GLM Coding Plan (Anthropic Messages — the ZCode surface; use ZAI_ANTHROPIC_API_KEY)",
    signup_url="https://z.ai/",
    base_url=DEFAULT_BASE_URL,
    models_url=f"{DEFAULT_BASE_URL}/models",  # real catalog: /api/anthropic/v1/models
    fallback_models=FALLBACK_MODELS,
    default_aux_model="glm-5.3-flash",
    supports_vision=True,  # glm-5.3-flash is vision-capable
)

register_provider(zai_anthropic)
