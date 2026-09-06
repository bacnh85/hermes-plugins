# zai-anthropic-zcode (Hermes companion plugin)

ZCode desktop-client **request parity** for the `zai-anthropic` provider
(Hermes port of the Pi extension `pi-model-tools`, which embeds
TriDefender/zcode-api, MIT): identity headers, per-request Ed25519 + PoW
signing (Client Signing V4), fast mode, and a 401-bypass ladder.

## Why a separate plugin

Hermes' general plugin loader never imports `kind: model-provider` modules,
so a provider plugin cannot register middleware. This `kind: standalone`
companion registers:

- `llm_request` middleware — rewrites the effective Anthropic Messages kwargs
  right before dispatch (adds identity headers, `speed: "fast"`, the
  `fast-mode-2026-02-01` beta, and V4 signing headers)
- `api_request_error` hook — feeds 401s into the bypass ladder

## Behavior

- Signing **ON** by default (the server-side gate `codingPlanSignature.enable`
  is live on coding-plan keys). Opt out: `ZAI_ANTHROPIC_SIGNING=0`
- Fast mode **ON** by default. Opt out: `ZAI_ANTHROPIC_SPEED=standard`
- Fail-open everywhere (gate off/unreachable, handshake failure, key not in
  `{apiKeyId}.{apiKeySecret}` form, foreign base URL → request goes unsigned;
  Z.ai currently answers unsigned requests with HTTP 200 regardless)
- Two consecutive 401s after signed requests → signing bypassed for the
  process lifetime (restart to retry)
- Credential-egress guard: the gate probe and handshake carry your full key
  to fixed z.ai origins only — any other `ZAI_ANTHROPIC_BASE_URL` is never
  signed and never probed

## Install

```bash
hermes plugins install bacnh85/hermes-plugins/zai-anthropic-zcode --enable
hermes gateway restart
```

Requires the `zai-anthropic` provider plugin and `ZAI_ANTHROPIC_API_KEY`.
