# zai-anthropic (Hermes provider plugin)

Z.AI **GLM Coding Plan** via the **Anthropic Messages API** — the same surface
the ZCode desktop client uses (`https://api.z.ai/api/anthropic`), not the
OpenAI-compatible `/api/coding/paas/v4` that Hermes' bundled `zai` provider uses.

## Install

```bash
# model-provider kind: use the repo installer (routes into plugins/model-providers/)
python3 install_plugins.py zai-anthropic

# optional companion (recommended): ZCode signing + fast mode parity
hermes plugins install bacnh85/hermes-plugins/zai-anthropic-zcode --enable
```

Set `ZAI_ANTHROPIC_API_KEY` in `~/.hermes/.env` (Z.ai Coding Plan key).
Optional: `ZAI_ANTHROPIC_BASE_URL` to switch plan endpoints:

- `https://api.z.ai/api/anthropic` (default — Z.ai Coding Plan)
- `https://open.bigmodel.cn/api/anthropic` (BigModel Coding Plan)
- `https://zcode.z.ai/api/v1/ultra-zai/anthropic` (ZCode ultra route)

## Use

```bash
hermes --provider zai-anthropic --model glm-5.3-flash -q "hello"
# or in-session: /model → zai-anthropic
```

Models: live catalog from `GET /v1/models` (glm-5.3, glm-5.3-flash, glm-5.2,
glm-5.1, glm-5-turbo, glm-4.7, …). `glm-5.3-flash` is vision-capable.

## Companion plugin

`zai-anthropic-zcode` adds ZCode desktop-client request parity via Hermes
`llm_request` middleware (see that plugin's README). Both default ON, opt out
with env vars — the provider itself works fine without it.
