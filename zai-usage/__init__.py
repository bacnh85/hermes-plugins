"""Z.ai Coding Plan usage plugin for Hermes.

Shows Z.ai Coding Plan quota — the 5h rolling token window (percentage used,
reset time) plus per-model token totals over a sliding window — from the
billing plane:

    GET https://api.z.ai/api/monitor/usage/quota/limit
    GET https://api.z.ai/api/monitor/usage/model-usage?startTime=...&endTime=...

Both authenticate with ``Authorization: Bearer <ZAI_ANTHROPIC_API_KEY>`` —
the same coding-plan key the ``zai-anthropic`` provider uses, but these are
billing-plane queries: they consume ZERO model tokens. Response shapes
live-verified 2026-09-07:

    quota/limit:  {code:0, data:{limits:[{type:"TOKENS_LIMIT", percentage:int,
                  nextResetTime:epoch-ms}, ...]}}
    model-usage:  {code:0, data:{totalUsage:{modelSummaryList:[
                  {modelName:"GLM-5.3-Flash", totalTokens:int}, ...]}}}
                  (startTime/endTime as "YYYY-MM-DD HH:MM:SS", URL-encoded)

Surfaces:
  - ``/zai`` slash command (in-session; handler returns a string)
  - ``hermes zai-usage`` CLI subcommand (terminal)

Successful fetches are memoized for 60s so rapid repeated invocations don't
hammer the billing endpoint. A failed fetch (network/401) returns the last
cached report with a staleness marker rather than erroring out.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

QUOTA_PATH = "/api/monitor/usage/quota/limit"
MODEL_USAGE_PATH = "/api/monitor/usage/model-usage"
REQUEST_TIMEOUT_S = 15
CACHE_TTL_S = 60.0
USER_AGENT = "hermes-cli/zai-usage"

_cached: dict[str, Any] = {"at": 0.0, "report": None}


def _api_key() -> str:
    return (os.getenv("ZAI_ANTHROPIC_API_KEY") or "").strip()


def _fmt_window(dt: datetime) -> str:
    """Z.ai expects 'YYYY-MM-DD HH:MM:SS' (UTC verified 2026-09-07)."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _get(path: str, qs: str = "") -> dict[str, Any]:
    req = urllib.request.Request(
        f"https://api.z.ai{path}{qs}",
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        data = json.loads(resp.read().decode())
    if data.get("success") is False or (data.get("code") not in (0, 200, None)):
        raise RuntimeError(f"api error {data.get('code')}: {data.get('msg')}")
    return data


def _fetch_quota() -> dict[str, Any]:
    data = _get(QUOTA_PATH)
    limits = (data.get("data") or {}).get("limits") or []
    windows = []
    for lim in limits:
        if lim.get("type") != "TOKENS_LIMIT":
            continue
        pct = lim.get("percentage")
        reset_ms = lim.get("nextResetTime")
        reset = ""
        if isinstance(reset_ms, (int, float)) and reset_ms > 0:
            # epoch-ms; render in local time with an explicit offset label
            dt = datetime.fromtimestamp(reset_ms / 1000.0, tz=timezone.utc).astimezone()
            reset = dt.strftime("%H:%M")
        windows.append({"pct": pct, "reset": reset})
    return {"windows": windows}


def _fetch_model_usage(hours: float) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    qs = (
        "?startTime="
        + urllib.parse.quote(_fmt_window(now - timedelta(hours=hours)))
        + "&endTime="
        + urllib.parse.quote(_fmt_window(now))
    )
    data = _get(MODEL_USAGE_PATH, qs)
    summaries = (
        ((data.get("data") or {}).get("totalUsage") or {}).get("modelSummaryList")
        or []
    )
    by_model = {
        str(m.get("modelName")): int(m.get("totalTokens") or 0)
        for m in summaries
        if m.get("modelName")
    }
    return {"by_model": by_model}


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _build_report(force: bool = False) -> str:
    now = time.monotonic()
    if (
        not force
        and _cached["report"] is not None
        and (now - _cached["at"]) < CACHE_TTL_S
    ):
        return _cached["report"] + " (cached)"

    errors: list[str] = []
    quota: dict[str, Any] = {}
    usage24: dict[str, Any] = {}
    usage48: dict[str, Any] = {}
    try:
        quota = _fetch_quota()
    except Exception as exc:
        errors.append(f"quota: {exc}")
    try:
        usage24 = _fetch_model_usage(24)
    except Exception as exc:
        errors.append(f"24h usage: {exc}")
    try:
        usage48 = _fetch_model_usage(48)
    except Exception as exc:
        errors.append(f"48h usage: {exc}")

    if not errors:
        report = _render(quota, usage24, usage48)
        _cached["at"] = now
        _cached["report"] = report
        return report
    # Partial/total failure: fall back to the last good report if we have one.
    if _cached["report"]:
        return _cached["report"] + "\n(stale — last successful fetch)"
    return (
        "zai-usage: failed to reach api.z.ai billing plane:\n  "
        + "\n  ".join(errors)
    )


def _render(quota: dict[str, Any], usage24: dict[str, Any], usage48: dict[str, Any]) -> str:
    lines: list[str] = []
    for w in quota.get("windows", []):
        reset = f", resets {w['reset']}" if w.get("reset") else ""
        lines.append(f"5h window: {w.get('pct', '?')}% used{reset}")
    if not quota.get("windows"):
        lines.append("5h window: no TOKENS_LIMIT data")
    by24 = usage24.get("by_model", {})
    by48 = usage48.get("by_model", {})
    if by24:
        lines.append("24h usage:")
        for model, tokens in sorted(by24.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {model}: {_fmt_tokens(tokens)}")
    if by48:
        lines.append("48h usage:")
        for model, tokens in sorted(by48.items(), key=lambda kv: -kv[1]):
            delta = tokens - by24.get(model, 0)
            lines.append(f"  {model}: {_fmt_tokens(tokens)} (prev 24h: {_fmt_tokens(delta)})")
    return "\n".join(lines)


def slash_zai(args: str = "") -> str:
    """In-session /zai handler — returns the report as a string."""
    try:
        return _build_report(force=(args or "").strip().lower() in {"refresh", "-r", "--refresh"})
    except Exception as exc:  # never raise into the session
        return f"zai-usage: {exc}"


def _cli_handler(args) -> None:
    print(_build_report(force=getattr(args, "refresh", False)))


def register_cli_zai_usage(parser) -> None:
    """argparse setup for ``hermes zai-usage``.

    Hermes' CLI wiring creates the subparser and calls ``setup_fn(parser)`` —
    setup_fn receives the command parser itself, NOT subparsers (calling
    add_parser there throws and discovery swallows it). handler_fn is wired
    separately via set_defaults inside register_cli_command.
    """
    parser.add_argument("--refresh", action="store_true", help="skip the 60s result cache")


def register(ctx) -> None:
    """Register the /zai slash command + hermes zai-usage CLI."""
    ctx.register_command(
        "zai",
        handler=slash_zai,
        description="Show Z.ai Coding Plan usage (5h token window, per-model totals)",
        args_hint="[refresh]",
    )
    ctx.register_cli_command(
        "zai-usage",
        help="Show Z.ai Coding Plan usage (5h window, 24h/48h per-model tokens)",
        setup_fn=register_cli_zai_usage,
        handler_fn=_cli_handler,
        description="Z.ai Coding Plan usage",
    )
