#!/usr/bin/env python
"""Sync OmnRoute per-model context-window overrides into Hermes config.

Why this exists
---------------
OmniRoute (= 9router fork) stamps its DEFAULT_CAPABILITIES pair
(context_length=200000, max_output_tokens=128000) onto upstream models whose
real window is much larger (GLM-5.3 / GLM-5.3-flash = 1M, etc.). Hermes trusts
this raw /models metadata for route-prefixed model ids (custom-endpoint step 2
in agent/model_metadata.py) and caps conversations/compression at 200K.

Same bug class pi-router 1.1.2 fixed; see ~/agents/pi-extensions
pi-router/extensions/lib/client.ts (floor-pair-poison gate).

How this fixes it for Hermes
-----------------------------
Hermes ships a supported override channel: ``model_overrides.<provider>.
<model_id>.context_window`` in config.yaml — resolved at step 0b, BEFORE the
endpoint metadata read. This tool derives the correct values from the live
catalog itself and writes that config section via hermes_cli.config.save_config
(no hand-editing, no core patch):

  1. Fetch <OMNIROUTE_BASE_URL>/models (retrying — the WAF intermittently
     serves the SPA HTML page instead of JSON).
  2. Compute each model family's true window = the max context_length any
     route/suffix of that family reports anywhere in the catalog (a single
     honest upstream copy exposes it, e.g. command-code/zai-org/GLM-5.3 = 1M).
  3. Any model id stamped AT/BELOW both floor values whose family truth is
     provably larger gets an override with the real window.
     Models reported ABOVE the floor are never touched (router truth).
  4. Only ids THIS TOOL wrote before (or unset ones) are updated; entries a
     user wrote by hand are preserved and skipped. Managed ids are tracked in
     ~/.hermes/omniroute_context_overrides.json.

Bare (unprefixed) model ids don't need this: Hermes' built-in family table
(agent/model_metadata.py DEFAULT_CONTEXT_LENGTHS) already carries verified
windows for glm-5.3/deepseek-v4/kimi-k3/minimax-m3 and is matched longest-first.

Usage:  ./sync_context_overrides.py [--dry-run]
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

# 9router DEFAULT_CAPABILITIES pair — the poison signature (not real metadata).
FLOOR_CTX = 200_000
FLOOR_MAX = 128_000

PROVIDER = "omniroute"
HERMES_HOME = os.path.expanduser("~/.hermes")
MARKER_PATH = os.path.join(HERMES_HOME, "omniroute_context_overrides.json")


def _credentials():
    from dotenv import load_dotenv

    load_dotenv(os.path.join(HERMES_HOME, ".env"), override=True)
    base = os.getenv("OMNIROUTE_BASE_URL", "").strip().rstrip("/")
    key = os.getenv("OMNIROUTE_API_KEY", "").strip()
    if not base or not key:
        sys.exit("OMNIROUTE_BASE_URL / OMNIROUTE_API_KEY missing in ~/.hermes/.env")
    return base, key


def fetch_catalog(base: str, key: str, attempts: int = 4) -> list[dict]:
    """GET <base>/models, retrying past WAF/SPA responses that aren't JSON."""
    url = f"{base}/models"
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {key}")
        req.add_header("Accept", "application/json")
        # omniroute sits behind a WAF that 403/challenges the Python-urllib UA.
        req.add_header("User-Agent", "OpenAI/Python 1.40.0")
        try:
            payload = json.loads(urllib.request.urlopen(req, timeout=60).read())
            items = payload.get("data", []) if isinstance(payload, dict) else payload
            if isinstance(items, list):
                return [m for m in items if isinstance(m, dict) and m.get("id")]
        except Exception as exc:  # noqa: BLE001 - report after final attempt
            last = exc
        if attempt < attempts:
            print(f"  attempt {attempt} failed ({last}); retrying in 15s…")
            time.sleep(15)
    sys.exit(f"could not fetch JSON catalog from {url}: {last}")


def _num(value) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _slug(name: str) -> str:
    """Normalize a slug so case differences don't split families."""
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"-\1", name).lower()


# models.dev-style org prefixes stripped before family grouping, so
# "glm-5.3" ≡ "z-ai/glm-5.3" ≡ "zai-org/GLM-5.3" land in one family.
VENDOR_PREFIXES = (
    "zai-org", "z-ai", "deepseek-ai", "deepseek", "moonshotai", "minimaxai",
    "minimax", "qwen", "openai", "anthropic", "google", "meta", "x-ai",
    "baidu", "tencent", "mistralai", "microsoft", "nvidia", "cohere",
    "amazon", "perplexity",
)


def _family_key(root: str) -> str:
    """Family stem for a root slug (last segment, vendor prefix dropped)."""
    seg = _slug((root or "").split("/")[-1])
    for p in VENDOR_PREFIXES:
        if seg.startswith(p + "-"):
            return seg[len(p) + 1:]
    return seg


def compute_overrides(items: list[dict]) -> dict[str, int]:
    """Return {model_id: context_window} for floor-poisoned ids.

    Poison fingerprint = BOTH top-level fields equal the 9router
    DEFAULT_CAPABILITIES constants exactly (200000/128000). A route whose
    upstream carried real metadata reports its own numbers instead (e.g.
    openrouter's gpt-oss = 131072 — truthful, never touched). Truth for a
    poisoned id comes from any sibling in its vendor-normalized family that
    reports a window ABOVE the floor (one honest copy exposes it).
    """
    family_truth: dict[str, int] = {}
    for m in items:
        ctx = _num(m.get("context_length"))
        if ctx and ctx > FLOOR_CTX:
            key = _family_key(m.get("root") or m["id"])
            family_truth[key] = max(family_truth.get(key, 0), ctx)

    overrides: dict[str, int] = {}
    for m in items:
        if _num(m.get("context_length")) != FLOOR_CTX:
            continue
        if _num(m.get("max_output_tokens")) != FLOOR_MAX:
            continue
        truth = family_truth.get(_family_key(m.get("root") or m["id"]), 0)
        if truth:
            overrides[m["id"]] = truth
    return overrides


def _load_managed() -> set[str]:
    try:
        with open(MARKER_PATH) as f:
            return set(json.load(f).get("ids", []))
    except (OSError, ValueError):
        return set()


def apply(overrides: dict[str, int], dry_run: bool) -> int:
    """Merge overrides into config.yaml, preserving user-written entries."""
    from hermes_cli.config import load_config_readonly, save_config

    section = dict(
        (load_config_readonly().get("model_overrides") or {}).get(PROVIDER) or {}
    )
    managed = _load_managed()
    changed: dict[str, dict] = {}
    for model_id, ctx in sorted(overrides.items()):
        current = section.get(model_id)
        cur_ctx = current.get("context_window") if isinstance(current, dict) else None
        if cur_ctx == ctx:
            continue  # already correct
        if cur_ctx is not None and model_id not in managed:
            print(f"  SKIP {model_id}: user-set {cur_ctx:,} ≠ computed {ctx:,}")
            continue
        section[model_id] = {"context_window": ctx}
        changed[model_id] = {"from": cur_ctx, "to": ctx}

    if dry_run:
        for model_id, ch in sorted(changed.items()):
            print(f"  would set {model_id}: "
                  f"{ch['from'] if ch['from'] is None else format(ch['from'], ',')} "
                  f"→ {ch['to']:,}")
        return len(changed)
    if not changed:
        print(f"{len(section)} override(s) already in sync.")
        return 0

    cfg = load_config_readonly()
    cfg.setdefault("model_overrides", {})[PROVIDER] = section
    save_config(cfg, merge_existing=True)

    with open(MARKER_PATH, "w") as f:
        json.dump({"ids": sorted(section)}, f, indent=1)
    for model_id, ch in sorted(changed.items()):
        print(f"  {model_id}: {ch['from']} → {ch['to']:,}")
    return len(changed)


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    base, key = _credentials()
    items = fetch_catalog(base, key)
    print(f"fetched {len(items)} models from {base}/models")

    overrides = compute_overrides(items)
    id_to_root = {m["id"]: (m.get("root") or m["id"]) for m in items}
    families: dict[str, set[int]] = {}
    for model_id, ctx in overrides.items():
        fam = _family_key(id_to_root.get(model_id, model_id))
        families.setdefault(fam, set()).add(ctx)
    print(f"{len(overrides)} floor-poisoned model id(s) across "
          f"{len(families)} familie(s):")
    for fam, targets in sorted(families.items()):
        print(f"  {fam} → {'/'.join(sorted(f'{c // 1000}k' for c in targets))}")

    n = apply(overrides, dry_run)
    print(f"\n{'dry-run' if dry_run else 'applied'}: {n} override(s) written")


if __name__ == "__main__":
    main()
