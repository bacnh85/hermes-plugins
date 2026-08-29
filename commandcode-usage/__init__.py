"""Command Code usage plugin for Hermes.

Shows Command Code (commandcode.ai) subscription usage — the 5-hour and
weekly rolling USD windows plus the monthly credit balance — from
``GET https://api.commandcode.ai/alpha/billing/credits``, authenticated
with the same Provider API key used for ``/provider/v1`` model calls
(``COMMANDCODE_API_KEY`` in ``~/.hermes/.env``).

Surfaces:
  - ``/commandcode`` slash command (in-session; handler returns a string)
  - ``hermes commandcode-usage`` CLI subcommand (terminal)

Live-verified 2026-08-09 against api.commandcode.ai (pi-sub used the same
response shape):
  credits:      { monthlyCredits, purchasedCredits, freeCredits, belowThreshold, ... }
  windowLimits: { limited, exceeded, fiveHour: {used, cap, exceeded, resetAt},
                  weekly: {used, cap, exceeded, resetAt} }
  resetAt is epoch MILLISECONDS; used/cap are USD.

This is a separate ``kind: standalone`` plugin because Hermes' general
plugin loader never imports ``kind: model-provider`` modules — a slash
command cannot live in the provider plugin.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

USAGE_URL = "https://api.commandcode.ai/alpha/billing/credits"
REQUEST_TIMEOUT_S = 15
# Cache successful fetches briefly so rapid /commandcode invocations don't
# hammer the billing endpoint (same pattern as pi-sub's debounced refresh).
CACHE_TTL_S = 60.0

_cached: dict[str, Any] = {"at": 0.0, "data": None}


# --------------------------------------------------------------------------- #
# API access
# --------------------------------------------------------------------------- #
def _api_key() -> str:
    import os
    return (os.getenv("COMMANDCODE_API_KEY") or "").strip()


def _fetch_usage(api_key: str, force: bool = False) -> dict[str, Any]:
    """Return the parsed /alpha/billing/credits body (60s memo)."""
    now = time.monotonic()
    if not force and _cached["data"] is not None and (now - _cached["at"]) < CACHE_TTL_S:
        return _cached["data"]

    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "hermes-cli/commandcode-usage",
        },
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        data = json.loads(resp.read().decode())
    _cached["at"] = now
    _cached["data"] = data
    return data


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def _fmt_window(name: str, window: dict[str, Any] | None) -> str:
    if not isinstance(window, dict):
        return f"  {name:8s}: no data"
    used, cap = window.get("used"), window.get("cap")
    if not isinstance(used, (int, float)) or not isinstance(cap, (int, float)) or cap <= 0:
        return f"  {name:8s}: no data"
    pct = min(100.0, used / cap * 100.0)
    line = f"  {name:8s}: {pct:4.0f}% of ${cap:,.2f} used (${used:,.2f})"
    reset_ms = window.get("resetAt")
    if isinstance(reset_ms, (int, float)) and reset_ms > 0:
        reset_dt = datetime.fromtimestamp(reset_ms / 1000.0, tz=timezone.utc).astimezone()
        delta = reset_dt - datetime.now().astimezone()
        if delta.total_seconds() > 0:
            hours, rem = divmod(int(delta.total_seconds()), 3600)
            mins = rem // 60
            line += f" — resets in {hours}H {mins:02d}M"
        else:
            line += f" — reset due now"
    return line


def _key_label() -> str:
    """Non-secret fingerprint of the configured key, pi-sub style."""
    key = _api_key()
    if not key:
        return "no COMMANDCODE_API_KEY set"
    return f"key#{hashlib.sha256(key.encode()).hexdigest()[:8]}"


def format_usage(data: dict[str, Any]) -> str:
    credits = data.get("credits") or {}
    windows = data.get("windowLimits") or {}

    lines = [f"Command Code usage ({_key_label()})"]
    lines.append(_fmt_window("5-hour", windows.get("fiveHour")))
    lines.append(_fmt_window("Weekly", windows.get("weekly")))

    monthly = credits.get("monthlyCredits")
    if isinstance(monthly, (int, float)):
        lines.append(f"  Monthly : ${monthly:,.2f} remaining")
    purchased = credits.get("purchasedCredits")
    if isinstance(purchased, (int, float)) and purchased > 0:
        lines.append(f"  Top-ups : ${purchased:,.2f} purchased credits")
    if windows.get("limited"):
        lines.append("  ⚠ rate limited — wait for a window reset")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #
def _build_report(force: bool = False) -> str:
    api_key = _api_key()
    if not api_key:
        return (
            "Command Code usage: COMMANDCODE_API_KEY not set.\n"
            "Add it to ~/.hermes/.env (Provider API key from "
            "https://commandcode.ai/studio), then run /reload."
        )
    try:
        return format_usage(_fetch_usage(api_key, force=force))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode()[:200]
        except Exception:
            pass
        return f"Command Code usage: HTTP {exc.code} from billing endpoint {detail}".rstrip()
    except Exception as exc:
        return f"Command Code usage: fetch failed: {exc}"


def slash_commandcode(raw_args: str) -> str:
    """``/commandcode [refresh]`` — show Command Code usage windows."""
    sub = (raw_args or "").strip().lower()
    return _build_report(force=(sub == "refresh"))


def register_cli_commandcode(subparsers) -> None:
    """argparse setup for ``hermes commandcode-usage``."""
    parser = subparsers.add_parser(
        "commandcode-usage",
        help="Show Command Code 5-hour/weekly usage windows and monthly credits",
    )
    parser.add_argument("--refresh", action="store_true", help="skip the 60s result cache")
    parser.set_defaults(func=lambda args: print(_build_report(force=args.refresh)))


def register(ctx) -> None:
    """Register the /commandcode slash command + hermes commandcode-usage CLI."""
    ctx.register_command(
        "commandcode",
        handler=slash_commandcode,
        description="Show Command Code usage (5h/weekly windows, monthly credits)",
        args_hint="[refresh]",
    )
    ctx.register_cli_command(
        "commandcode-usage",
        help="Show Command Code usage windows and monthly credits",
        setup_fn=register_cli_commandcode,
        description="Command Code subscription usage",
    )
