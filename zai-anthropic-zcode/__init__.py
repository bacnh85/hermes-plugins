"""ZCode desktop-client request parity for the ``zai-anthropic`` provider.

Ported to Python from the Pi extension ``pi-model-tools`` (TypeScript), which
itself embeds TriDefender/zcode-api (MIT) — ZCode's Client Request Signing V4:
identity headers + ``X-Session-Id`` + per-request Ed25519 signatures + PoW,
gated server-side by agent/configs ``codingPlanSignature.enable``.

Why a companion plugin: Hermes' general plugin loader never imports
``kind: model-provider`` modules, so the provider plugin cannot register
middleware. This ``kind: standalone`` plugin registers ``llm_request``
middleware that rewrites the effective Anthropic Messages kwargs right before
dispatch (agent/conversation_loop.py), plus an ``api_request_error`` hook for
the 401-bypass ladder.

Defaults (owner decision 2026-09-06, ZCode parity):
  - signing ON — opt out with ``ZAI_ANTHROPIC_SIGNING=0``
  - fast mode ON — opt out with ``ZAI_ANTHROPIC_SPEED=standard``

Fail-open everywhere, matching the real client: gate off/unreachable →
unsigned; handshake failure → unsigned; credential without the two-part
``{apiKeyId}.{apiKeySecret}`` form → unsigned; two consecutive 401s after
signed requests → permanent bypass for the process. The Z.ai endpoint
currently answers unsigned requests with HTTP 200 even with the gate enabled
(live-verified 2026-09-07), so fail-open degrades gracefully.

Credential-egress guard: the gate probe and the handshake both carry the
full API key to FIXED z.ai origins — signing is skipped entirely for any
other ``ZAI_ANTHROPIC_BASE_URL`` (open.bigmodel.cn, corporate proxies, …).

All crypto is ``cryptography`` (already a Hermes dependency) + stdlib.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import platform
import re
import threading
import time
import urllib.request
import uuid
from typing import Any

from hermes_cli.urllib_security import open_credentialed_url

logger = logging.getLogger(__name__)

PROVIDER_ID = "zai-anthropic"
DEFAULT_ORIGIN = "https://zcode.z.ai"
DEFAULT_BASE_URL = "https://api.z.ai/api/anthropic"
# Live-verified 2026-09-06: /api/paas/* 404s on zcode.z.ai; the get_sign_key
# endpoint answers on the api.z.ai origin.
HANDSHAKE_ORIGIN = "https://api.z.ai"
GATE_PATH = "/api/v1/agent/configs"
HANDSHAKE_PATH = "/api/paas/c1f3a7e2/v2/client"
APP_ID = "zcode"
APP_VERSION_FALLBACK = "3.10.2"  # matches the ZCode desktop app
FAST_MODE_BETA = "fast-mode-2026-02-01"
# Betas Hermes' Anthropic client sets client-level (agent/anthropic_adapter
# _COMMON_BETAS). Per-request extra_headers REPLACE the client header, so any
# anthropic-beta we emit must carry these too (review MAJOR-2).
COMMON_CLIENT_BETAS = (
    "interleaved-thinking-2025-05-14",
    "fine-grained-tool-streaming-2025-05-14",
)

GATE_TTL_S = 3_600.0
GATE_FAILURE_COOLDOWN_S = 60.0
GATE_UNAVAILABLE_COOLDOWN_S = 30.0
GATE_TIMEOUT_S = 15.0
HANDSHAKE_TIMEOUT_S = 10.0
HANDSHAKE_NEG_COOLDOWN_S = 60.0
BYPASS_AFTER_401S = 2

ZAI_ORIGINS = {"https://api.z.ai", "https://zcode.z.ai"}

# Paths the client never signs (decoded, trailing-slash-stripped).
UNSIGNED_PATHS = {
    "/api/v1/zcode-plan/anthropic/v1/messages",
    "/api/v1/zcode-plan/chat/completions",
    "/api/v1/off-peak/anthropic/v1/messages",
}

KDF_SALT = b"WD_CLIENT_SIGN_KDF_SALT"
KDF_INFO_HMAC = b"getSignKey_hmac"
KDF_INFO_ED25519 = b"ed25519_priv"

_SIGNING_HEADER_NAMES = (
    "x-client-ts",
    "x-client-version",
    "x-client-sig",
    "x-client-nonce",
    "x-app-id",
    "x-client-pow",
    "x-session-id",
)

_device_mid_cache: str | None = None
_lock = threading.Lock()
# Per-credential signing state (keyed by the credential string itself).
_states: dict[str, dict[str, Any]] = {}
_last_signed_key: str | None = None
_noted: set[str] = set()


# ── env gates ────────────────────────────────────────────────────────────────

def signing_enabled() -> bool:
    """ZCode signing ON by default; opt out with ZAI_ANTHROPIC_SIGNING=0."""
    return not re.match(r"^(0|false|no|off)$", (os.getenv("ZAI_ANTHROPIC_SIGNING") or "").strip(), re.I)


def fast_mode_enabled() -> bool:
    """Fast mode ON by default (ZCode parity); ZAI_ANTHROPIC_SPEED=standard disables."""
    return not re.match(r"^(standard|normal|slow)$", (os.getenv("ZAI_ANTHROPIC_SPEED") or "").strip(), re.I)


def resolved_base_url() -> str:
    raw = (os.getenv("ZAI_ANTHROPIC_BASE_URL") or "").strip()
    return raw.rstrip("/") if raw else DEFAULT_BASE_URL


# ── identity headers (port of identity.ts) ───────────────────────────────────

def _printable(raw: str | None) -> str | None:
    if not isinstance(raw, str):
        return None
    v = raw.strip()
    return v if v and all(0x20 <= ord(c) <= 0x7E for c in v) else None


def _os_category(p: str) -> str:
    # platform.system().lower() is "windows" on Windows; "win32" kept for
    # parity with the TS original (review MINOR-7).
    return {"darwin": "macos", "win32": "windows", "windows": "windows"}.get(p, "linux")


def resolve_device_mid() -> str:
    """ZCode's own telemetry id, else a probe-cache file, else a fresh UUID."""
    global _device_mid_cache
    if _device_mid_cache:
        return _device_mid_cache
    try:
        with open(os.path.expanduser("~/.zcode/v2/telemetry-state.json"), encoding="utf-8") as f:
            t = json.load(f)
        mid = t.get("deviceMid")
        if isinstance(mid, str) and mid.strip():
            _device_mid_cache = mid.strip()
            return _device_mid_cache
    except Exception:
        pass
    cache = os.path.expanduser("~/.zcode-probe-device-mid")
    try:
        with open(cache, encoding="utf-8") as f:
            cached = f.read().strip()
        if cached:
            _device_mid_cache = cached
            return _device_mid_cache
    except Exception:
        pass
    mid = str(uuid.uuid4())
    try:
        with open(cache, "w", encoding="utf-8") as f:
            f.write(mid)
    except Exception:
        pass
    _device_mid_cache = mid
    return mid


def resolve_identity() -> dict[str, Any]:
    return {
        "app_version": _printable(os.getenv("ZCODE_IDENTITY_APP_VERSION")) or APP_VERSION_FALLBACK,
        "device_mid": resolve_device_mid(),
    }


def build_identity_headers(identity: dict[str, Any]) -> dict[str, str]:
    plat = _printable(os.getenv("ZCODE_IDENTITY_PLATFORM")) or platform.system().lower()
    arch = _printable(os.getenv("ZCODE_IDENTITY_ARCH")) or platform.machine()
    release = _printable(os.getenv("ZCODE_IDENTITY_RELEASE")) or platform.release()
    channel = _printable(os.getenv("ZCODE_IDENTITY_RELEASE_CHANNEL")) or "production"
    try:
        lang = _printable(os.getenv("ZCODE_IDENTITY_CLIENT_LANGUAGE")) or (
            locale.replace("_", "-") if (locale := os.environ.get("LANG", "en_US").split(".")[0]) else None
        )
    except Exception:
        lang = None
    try:
        from datetime import datetime

        tz = _printable(os.getenv("ZCODE_IDENTITY_CLIENT_TIMEZONE")) or str(datetime.now().astimezone().tzinfo)
    except Exception:
        tz = None
    headers = {
        "HTTP-Referer": DEFAULT_ORIGIN,
        "User-Agent": f"ZCode/{identity['app_version']}",
        "X-ZCode-App-Version": identity["app_version"],
        "X-Title": "Z Code@electron",
        "X-ZCode-Agent": "glm",
        "X-Platform": f"{plat}-{arch}",
        "X-Release-Channel": channel,
        "X-Os-Category": _os_category(plat),
        "X-Os-Version": release,
    }
    if lang:
        headers["X-Client-Language"] = lang
    if tz:
        headers["X-Client-Timezone"] = tz
    if identity.get("device_mid"):
        headers["X-Device-Mid"] = identity["device_mid"]
    return headers


# ── crypto core ──────────────────────────────────────────────────────────────

def parse_signing_credential(credential: str) -> tuple[str, str] | None:
    """Keys sign only in two-part ``{apiKeyId}.{apiKeySecret}`` form."""
    dot = credential.find(".")
    if dot <= 0 or dot != credential.rfind("."):
        return None
    key_id, secret = credential[:dot], credential[dot + 1:]
    if not key_id.strip() or not secret.strip():
        return None
    return key_id, secret


def _hkdf_bits(secret: str, info: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    return HKDF(algorithm=hashes.SHA256(), length=32, salt=KDF_SALT, info=info).derive(secret.encode())


def _b64decode_strict(value: str) -> bytes:
    if not value or len(value) % 4 != 0 or not re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", value):
        raise ValueError("invalid base64")
    return base64.b64decode(value)


def handshake_signature(secret: str, message: str) -> str:
    bits = _hkdf_bits(secret, KDF_INFO_HMAC)
    mac = hmac.new(bits, message.encode(), hashlib.sha256).digest()
    return base64.b64encode(mac).decode()


def sign_business_message(private_key, message: str) -> str:
    sig = private_key.sign(message.encode())  # raw 64-byte Ed25519 (r||s)
    return base64.b64encode(sig).decode()


def _has_leading_zero_bits(digest: bytes, bits: int) -> bool:
    full, rem = divmod(bits, 8)
    if any(digest[i] for i in range(full)):
        return False
    if rem == 0:
        return True
    mask = (0xFF << (8 - rem)) & 0xFF
    return (digest[full] & mask) == 0


def create_proof_of_work(key_id: str, session_id: str, ts: str) -> str:
    """8-bit PoW ≈ 256 SHA-256 iterations — sub-5ms."""
    seed = hashlib.sha256(f"{key_id}\n{APP_ID}\n{session_id}\n{ts}".encode()).hexdigest()[:32]
    nonce = uuid.uuid4().hex[:24]  # 12 random bytes, hex
    for counter in range(0, 2**32):
        candidate = f"{nonce}{counter:08x}"
        digest = hashlib.sha256(f"{seed}\n{candidate}".encode()).digest()
        if _has_leading_zero_bits(digest, 8):
            return candidate
    raise RuntimeError("unable to solve client request proof of work")


# ── gate + handshake (network) ───────────────────────────────────────────────

def _note_once(key: str, message: str) -> None:
    marker = f"{key}#{message}"
    if marker in _noted:
        return
    _noted.add(marker)
    logger.info("zai-anthropic-zcode: %s", message)


def _fetch_gate(identity: dict[str, Any], credential: str) -> str:
    """'enabled' | 'disabled' | 'unavailable'. Identity headers WITHOUT
    X-ZCode-Agent / X-Device-Mid, plus x-api-key (matches the real client)."""
    headers = build_identity_headers(identity)
    headers.pop("X-ZCode-Agent", None)
    headers.pop("X-Device-Mid", None)
    headers["x-api-key"] = credential
    req = urllib.request.Request(f"{DEFAULT_ORIGIN}{GATE_PATH}", headers=headers)
    # open_credentialed_url strips auth headers on cross-origin redirects
    # (review MAJOR-1) — the gate carries the full credential in x-api-key.
    with open_credentialed_url(req, timeout=GATE_TIMEOUT_S) as resp:
        parsed = json.loads(resp.read().decode())
    if not parsed or parsed.get("code") != 0:
        return "unavailable"
    data = parsed.get("data")
    if not isinstance(data, dict) or "codingPlanSignature" not in data:
        return "disabled"
    signature = data.get("codingPlanSignature")
    return "enabled" if isinstance(signature, dict) and signature.get("enable") is True else "disabled"


def _perform_handshake(key_id: str, secret: str) -> Any:
    from cryptography.hazmat.primitives.serialization import load_der_private_key

    ts = str(int(time.time() * 1000))
    nonce = uuid.uuid4().hex
    sig = handshake_signature(secret, f"get_sign_key\n{key_id}\n{ts}\n{nonce}")
    body = json.dumps({"apiKey": f"{key_id}.{secret}", "nonce": nonce, "sig": sig, "ts": ts}).encode()
    req = urllib.request.Request(
        f"{HANDSHAKE_ORIGIN}{HANDSHAKE_PATH}",
        data=body,
        method="POST",
        headers={"Authorization": f"{key_id}.{secret}", "Content-Type": "application/json"},
    )
    # open_credentialed_url strips auth headers on cross-origin redirects
    # (review MAJOR-1) — the handshake carries keyId.secret in Authorization.
    with open_credentialed_url(req, timeout=HANDSHAKE_TIMEOUT_S) as resp:
        envelope = json.loads(resp.read().decode())
    code = envelope.get("code")
    if code == 500:
        raise RuntimeError("handshake_server_500")
    if code != 200:
        raise RuntimeError(f"handshake_rejected: {envelope.get('msg')}")
    cipher = (envelope.get("data") or {}).get("privateCipher")
    if not isinstance(cipher, str) or not cipher:
        raise RuntimeError("handshake_omitted_privateCipher")
    pkcs8_text = _unwrap_private_cipher(key_id, secret, cipher)
    return load_der_private_key(_b64decode_strict(pkcs8_text), password=None)


def _unwrap_private_cipher(key_id: str, secret: str, cipher: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = _b64decode_strict(cipher)
    if len(raw) <= 12 + 16:
        raise ValueError("privateCipher is too short")
    aes_key = _hkdf_bits(secret, KDF_INFO_ED25519)
    plain = AESGCM(aes_key).decrypt(raw[:12], raw[12:], key_id.encode())
    text = plain.decode("utf-8")  # the plaintext is a BASE64 STRING of the PKCS8 key
    if not re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", text):
        raise ValueError("privateCipher payload is not base64 text")
    return text


def _ensure_private_key(state: dict[str, Any], key_id: str, secret: str):
    if state.get("priv") is not None:
        return state["priv"]
    state["priv"] = _perform_handshake(key_id, secret)
    return state["priv"]


# ── the signing path ─────────────────────────────────────────────────────────

def _state_for(key: str) -> dict[str, Any]:
    state = _states.get(key)
    if state is None:
        state = {
            "gate_enabled": False, "gate_expires": 0.0, "gate_neg_until": 0.0,
            "handshake_neg_until": 0.0, "priv": None, "bypass": False,
            "consecutive_401s": 0,
        }
        _states[key] = state
    return state


def _gate_enabled(state: dict[str, Any], identity: dict[str, Any], credential: str) -> bool:
    now = time.monotonic()
    if state["gate_expires"] > now:
        return state["gate_enabled"]
    if state["gate_neg_until"] > now:
        return False
    try:
        outcome = _fetch_gate(identity, credential)
    except Exception:
        state["gate_neg_until"] = now + GATE_FAILURE_COOLDOWN_S
        return False
    state["gate_enabled"] = outcome == "enabled"
    if outcome == "unavailable":
        state["gate_neg_until"] = now + GATE_UNAVAILABLE_COOLDOWN_S
    else:
        state["gate_expires"] = now + GATE_TTL_S
        state["gate_neg_until"] = 0.0
    if state["gate_enabled"]:
        logger.info("zai-anthropic-zcode: server enabled codingPlanSignature — signing requests")
    return state["gate_enabled"]


def sign_request_headers(
    headers: dict[str, Any],
    *,
    base_url: str,
    session_id: str | None,
    credential: str,
) -> bool:
    """Add V4 signing headers into ``headers`` in place. Returns True only when
    the request was actually signed. Never raises; every ineligible path leaves
    the headers untouched (fail-open, matching the ZCode client).

    Note (review MINOR-9): the gate probe + handshake run inside the lock, so
    the first request of a TTL window can hold the lock up to ~25s
    (15s gate + 10s handshake timeouts). Cooldowns bound recurrence; callers
    accept this by design — subsequent requests within the TTL are lock-fast.
    """
    global _last_signed_key

    if not credential:
        return False
    parsed_cred = parse_signing_credential(credential)
    if not parsed_cred:
        _note_once("cred", "credential has no {apiKeyId}.{apiKeySecret} separator — signing skipped")
        return False

    from urllib.parse import urlparse

    origin = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    if origin not in ZAI_ORIGINS:
        _note_once(f"origin:{origin}", f"base URL {origin} is not a z.ai origin — signing skipped (credential egress guard)")
        return False
    # Paths the client never signs: check both the bare base path and the
    # messages endpoint form (review MINOR-6 — the old code compared only the
    # base path, so "/api/v1/zcode-plan/anthropic" never matched).
    path = urlparse(base_url).path.rstrip("/")
    if path in UNSIGNED_PATHS or f"{path}/v1/messages" in UNSIGNED_PATHS:
        return False

    state_key = f"{origin}\n{credential}"
    with _lock:
        state = _state_for(state_key)
        if state["bypass"]:
            return False
        identity = resolve_identity()
        if not _gate_enabled(state, identity, credential):
            return False
        if state["handshake_neg_until"] > time.monotonic():
            return False
        try:
            private_key = _ensure_private_key(state, *parsed_cred)
        except Exception as exc:
            _note_once(state_key, f"signing handshake failed ({exc}) — sending unsigned")
            state["priv"] = None
            state["handshake_neg_until"] = time.monotonic() + HANDSHAKE_NEG_COOLDOWN_S
            return False

        if not session_id:
            _note_once(state_key, "no session id available — signing skipped")
            return False
        headers["x-session-id"] = session_id

        ts = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        try:
            pow_value = create_proof_of_work(parsed_cred[0], session_id, ts)
            sig = sign_business_message(private_key, f"{parsed_cred[0]}\n{ts}\n{identity['app_version']}\n{session_id}\n{nonce}")
        except Exception as exc:
            _note_once(state_key, f"signing failed ({exc}) — sending unsigned")
            return False

        for name in _SIGNING_HEADER_NAMES:
            for existing in [k for k in headers if k.lower() == name]:
                del headers[existing]
        headers.update({
            "X-Client-Ts": ts,
            "X-Client-Version": identity["app_version"],
            "X-Client-Sig": sig,
            "X-Session-Id": session_id,
            "X-Client-Nonce": nonce,
            "X-App-Id": APP_ID,
            "X-Client-Pow": pow_value,
        })
        _last_signed_key = state_key
        return True


def note_response_401() -> None:
    """401 after a signed request: invalidate the handshake key; two in a row
    (no success between) → permanent bypass for the process."""
    global _last_signed_key
    if not _last_signed_key:
        return
    with _lock:
        state = _states.get(_last_signed_key)
        _last_signed_key = None
        if not state:
            return
        state["consecutive_401s"] += 1
        state["priv"] = None
        state["gate_expires"] = 0.0
        if state["consecutive_401s"] >= BYPASS_AFTER_401S:
            state["bypass"] = True
            logger.warning(
                "zai-anthropic-zcode: repeated 401 after signed requests — "
                "bypassing signing for this credential (restart to retry)"
            )


def note_response_ok() -> None:
    global _last_signed_key
    if not _last_signed_key:
        return
    with _lock:
        state = _states.get(_last_signed_key)
        if state:
            state["consecutive_401s"] = 0


# ── Hermes wiring ────────────────────────────────────────────────────────────

def _resolve_credential() -> str:
    from_env = (os.getenv("ZAI_ANTHROPIC_API_KEY") or "").strip()
    if from_env:
        return from_env
    try:
        from hermes_cli.auth import resolve_api_key_provider_credentials

        return (resolve_api_key_provider_credentials(PROVIDER_ID).get("api_key") or "").strip()
    except Exception:
        return ""


def _apply_fast_mode(request: dict[str, Any]) -> bool:
    """Top-level ``speed`` body field + fast-mode beta header (ZCode parity).

    The beta header must UNION with the betas Hermes' client already sends
    (interleaved-thinking, fine-grained-tool-streaming — see
    agent/anthropic_adapter.py _COMMON_BETAS): per-request extra_headers
    REPLACE the client-level anthropic-beta, so a bare fast-mode value would
    silently drop them (review MAJOR-2).
    """
    extra_body = request.get("extra_body")
    if not isinstance(extra_body, dict):
        extra_body = {}
    extra_body["speed"] = "fast"
    request["extra_body"] = extra_body

    extra_headers = request.get("extra_headers")
    if not isinstance(extra_headers, dict):
        extra_headers = {}
    existing = extra_headers.get("anthropic-beta") or ""
    parts = [p.strip() for p in existing.split(",") if p.strip()]
    for beta in COMMON_CLIENT_BETAS:
        if beta not in parts:
            parts.append(beta)
    if FAST_MODE_BETA not in parts:
        parts.append(FAST_MODE_BETA)
    extra_headers["anthropic-beta"] = ",".join(parts)
    request["extra_headers"] = extra_headers
    return True


def _apply_signing(request: dict[str, Any], context: dict[str, Any]) -> bool:
    """Identity headers + V4 signing for zai-anthropic requests.

    Egress guard (review MAJOR-4): evaluate against the REQUEST's actual
    destination — Hermes passes it as context["base_url"] — falling back to
    the env/default only when the context lacks it. The old code checked the
    env-resolved base, which diverges when the user points model.base_url at
    a proxy/bigmodel while the env var still names api.z.ai.
    """
    extra_headers = request.get("extra_headers")
    if not isinstance(extra_headers, dict):
        extra_headers = {}
        request["extra_headers"] = extra_headers
    if any(k.lower() == "x-client-sig" for k in extra_headers):
        return False  # already signed upstream
    destination = (context.get("base_url") or "").strip() or resolved_base_url()
    from urllib.parse import urlparse

    origin = f"{urlparse(destination).scheme}://{urlparse(destination).netloc}"
    if origin not in ZAI_ORIGINS:
        # Review MINOR-10: the ZCode identity fingerprint is also
        # origin-gated — never sent to non-z.ai destinations.
        return False
    session_id = str(context.get("session_id") or "") or None
    credential = _resolve_credential()
    extra_headers.update(build_identity_headers(resolve_identity()))
    request["extra_headers"] = extra_headers
    if not credential:
        return False
    return sign_request_headers(
        extra_headers,
        base_url=destination,
        session_id=session_id,
        credential=credential,
    )


def llm_request_middleware(request: dict[str, Any] | None = None, **context: Any):
    """Rewrite the effective Anthropic Messages kwargs for zai-anthropic.

    Contract (hermes_cli/middleware.py): return ``{"request": {...}}`` to
    replace the payload, or None to leave it unchanged. Never raises.
    """
    try:
        if (context.get("provider") or "").lower() != PROVIDER_ID:
            return None
        if not isinstance(request, dict):
            return None
        changed = False
        if fast_mode_enabled():
            changed = _apply_fast_mode(request) or changed
        if signing_enabled():
            changed = _apply_signing(request, context) or changed
        if changed:
            return {"request": request, "source": "zai-anthropic-zcode"}
        return None
    except Exception as exc:
        logger.debug("zai-anthropic-zcode middleware skipped: %s", exc)
        return None


def on_api_request_error(**context: Any) -> None:
    """401-bypass ladder — observer hook, never raises."""
    try:
        if (context.get("provider") or "").lower() != PROVIDER_ID:
            return
        if context.get("status_code") == 401:
            note_response_401()
        else:
            note_response_ok()
    except Exception as exc:
        logger.debug("zai-anthropic-zcode error hook skipped: %s", exc)


def on_post_api_request(**context: Any) -> None:
    """Successful request → reset the consecutive-401 counter (review MAJOR-3).

    api_request_error only fires on FAILURES, so without this hook the
    'consecutive' 401 counter never observed successes and two unrelated 401s
    months apart on a long-lived gateway would permanently bypass signing.
    post_api_request fires on every successful call (provider-gated here).
    """
    try:
        if (context.get("provider") or "").lower() != PROVIDER_ID:
            return
        note_response_ok()
    except Exception as exc:
        logger.debug("zai-anthropic-zcode post hook skipped: %s", exc)


def register(ctx) -> None:
    ctx.register_middleware("llm_request", llm_request_middleware)
    ctx.register_hook("api_request_error", on_api_request_error)
    ctx.register_hook("post_api_request", on_post_api_request)
