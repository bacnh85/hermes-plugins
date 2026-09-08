"""Bambu Lab LAN-mode 3D printing plugin for Hermes.

Gives any Hermes agent eyes and hands on a Bambu Lab printer (A1 Mini,
A1, P1/X1 series) that is running in **LAN-only mode** — no Bambu cloud
account, no Bambu Studio needed:

* ``bambu_discover``   — find the printer(s) on the LAN (TLS-cert CN read
  gives the serial for free).
* ``bambu_status``     — live telemetry: nozzle/bed temps, print stage,
  progress %, remaining time.
* ``bambu_upload``     — push a sliced ``.gcode.3mf`` to the SD card (FTPS).
* ``bambu_print``      — start a print (MQTT ``project_file``).
* ``bambu_stop``       — stop the running job.
* ``bambu_light``      — harmless write-test (chamber light) used to prove
  control before any real print.

Environment (``~/.hermes/.env``):

* ``BAMBU_ACCESS_CODE`` — printer LAN access code (required).
* ``BAMBU_HOST``        — printer IP (optional; auto-discovered).
* ``BAMBU_SERIAL``      — printer serial (optional; read from TLS CN).

In-session: ``/bambu status`` (and ``discover|upload|print|stop|light``).
Terminal: ``hermes bambu <subcommand>``.

Protocol: MQTT TLS 8883 (``bblp`` + access code) for telemetry/commands;
FTPS implicit-TLS 990 for file upload. Live-verified 2026-09-07 against an
A1 Mini in LAN mode (serial 0309AA461500125). See ``bambu.py`` docstring
for the wire-level details and README.md for the full workflow.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import bambu as B


def _config() -> dict[str, str]:
    return B.printer_config()


def _resolve_printer() -> dict[str, str]:
    """Return {host, serial, access_code}; discover host/serial if unset."""
    cfg = _config()
    host, serial = cfg["host"], cfg["serial"]
    if not host or not serial:
        found = B.discover()
        if not found:
            raise B.BambuError(
                "no Bambu printer found on the LAN — set BAMBU_HOST (+ BAMBU_SERIAL) in ~/.hermes/.env"
            )
        host = host or found[0]["host"]
        serial = serial or found[0]["serial"]
    cfg["host"], cfg["serial"] = host, serial
    return cfg


def _blocking(fn, *args, **kwargs):
    """Run blocking I/O off the event loop (gateway-safe)."""
    try:
        import asyncio

        asyncio.get_running_loop()
    except RuntimeError:
        return fn(*args, **kwargs)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn, *args, **kwargs).result(timeout=90)


# ── tool handlers ──────────────────────────────────────────────────────

def _t_discover(args: dict[str, Any]) -> str:
    cidr = (args or {}).get("subnet") or None
    found = B.discover(cidr)
    if not found:
        return "No Bambu printer found on the LAN (probed MQTT TLS :8883)."
    lines = ["Bambu printer(s) found:"]
    for f in found:
        lines.append(f"  host={f['host']}  serial={f['serial']}")
    lines.append("Tip: set BAMBU_HOST / BAMBU_SERIAL in ~/.hermes/.env to skip discovery.")
    return "\n".join(lines)


def _t_status(args: dict[str, Any]) -> str:
    p = _resolve_printer()
    st = _blocking(_status_with, p)
    return _fmt_status(st)


def _status_with(p: dict[str, str]) -> dict[str, Any]:
    with B.BambuMQTT(p["host"], p["serial"], p["access_code"]) as prn:
        return prn.status()


def _fmt_status(st: dict[str, Any]) -> str:
    if not st:
        return "No status received (printer offline?)."
    if not st.get("connected"):
        return "Printer not connected."
    return "\n".join(
        [
            f"Host:     {st.get('host')}  (serial {st.get('serial')})",
            f"State:    {st.get('stage_label')}",
            f"Nozzle:   {st.get('nozzle_c')}°C (target {st.get('nozzle_target_c')}°C)",
            f"Bed:      {st.get('bed_c')}°C (target {st.get('bed_target_c')}°C)",
            f"Progress: {st.get('progress_pct')}%  (remaining {st.get('remaining_min')} min)",
        ]
    )


def _t_upload(args: dict[str, Any]) -> str:
    p = _resolve_printer()
    path = (args or {}).get("file") or ""
    if not path:
        return "upload needs file=<path to .gcode.3mf>"
    if not os.path.exists(path):
        return f"file not found: {path}"
    sd = _blocking(B.upload, p["host"], p["access_code"], path)
    return f"Uploaded OK → SD {sd}\nNow run bambu_print with sd_path={sd}"


def _t_print(args: dict[str, Any]) -> str:
    p = _resolve_printer()
    sd = (args or {}).get("sd_path") or ""
    name = (args or {}).get("name") or ""
    if not sd:
        return "print needs sd_path=<1:/file.gcode.3mf> (from upload)"
    res = _blocking(
        B.start, p["host"], p["serial"], p["access_code"], sd, subtask_name=name
    )
    if res.get("started"):
        return f"Print started (stage {res.get('stage')}, {res.get('progress_pct')}%)."
    return f"Print command sent but no stage change: {res.get('note', '')}"


def _t_stop(args: dict[str, Any]) -> str:
    p = _resolve_printer()
    with B.BambuMQTT(p["host"], p["serial"], p["access_code"]) as prn:
        prn.stop()
    return "Stop command sent."


def _t_light(args: dict[str, Any]) -> str:
    p = _resolve_printer()
    mode = (args or {}).get("mode") or "on"
    with B.BambuMQTT(p["host"], p["serial"], p["access_code"]) as prn:
        res = prn.light(mode)
    return f"Light command sent ({res.get('sent')})."


# ── tool schemas (TypeBox style dicts — Hermes registry format) ────────

def _tools() -> list[dict[str, Any]]:
    def tool(name, desc, schema, handler):
        return {
            "name": name,
            "toolset": "bambu",
            "description": desc,
            "schema": schema,
            "handler": handler,
        }

    str_opt = {"type": "string"}
    return [
        tool(
            "bambu_discover",
            "Find Bambu Lab printers on the local LAN (LAN mode). Probes MQTT TLS :8883 and reads the serial from the TLS certificate. Returns host + serial per printer.",
            {
                "type": "object",
                "properties": {"subnet": {**str_opt, "description": "CIDR to scan (default: auto /24)"}},
                "additionalProperties": False,
            },
            _t_discover,
        ),
        tool(
            "bambu_status",
            "Read live status from a Bambu Lab printer (LAN mode): nozzle/bed temps, print stage (idle/printing/paused), progress %, remaining minutes. Requires BAMBU_ACCESS_CODE env.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            _t_status,
        ),
        tool(
            "bambu_upload",
            "Upload a sliced .gcode.3mf to a Bambu printer SD card over FTPS (LAN mode). Returns the SD path to pass to bambu_print.",
            {
                "type": "object",
                "properties": {"file": {**str_opt, "description": "Absolute path to the .gcode.3mf file"}},
                "required": ["file"],
                "additionalProperties": False,
            },
            _t_upload,
        ),
        tool(
            "bambu_print",
            "Start a print job on a Bambu printer (LAN mode, MQTT project_file). sd_path is the 1:/... value returned by bambu_upload. The A1 Mini may show a confirm dialog on its screen first.",
            {
                "type": "object",
                "properties": {
                    "sd_path": {**str_opt, "description": "SD path e.g. 1:/plate_1.gcode.3mf"},
                    "name": {**str_opt, "description": "Optional human job name"},
                },
                "required": ["sd_path"],
                "additionalProperties": False,
            },
            _t_print,
        ),
        tool(
            "bambu_stop",
            "Stop the running print job on a Bambu printer (LAN mode).",
            {"type": "object", "properties": {}, "additionalProperties": False},
            _t_stop,
        ),
        tool(
            "bambu_light",
            "Toggle the Bambu chamber light (on|off|flashing) — a harmless write test that proves MQTT control before printing.",
            {
                "type": "object",
                "properties": {"mode": {**str_opt, "description": "on|off|flashing (default on)"}},
                "additionalProperties": False,
            },
            _t_light,
        ),
    ]


# ── slash command ──────────────────────────────────────────────────────

def slash_bambu(args: str = "") -> str:
    """/bambu <action> — discover|status|upload|print|stop|light."""
    parts = (args or "").strip().split()
    action = parts[0].lower() if parts else "status"
    rest = " ".join(parts[1:])
    try:
        if action in ("status", "state", ""):
            return _fmt_status(_blocking(_status_with, _resolve_printer()))
        if action == "discover":
            return _t_discover({})
        if action == "upload":
            return _t_upload({"file": rest})
        if action == "print":
            return _t_print({"sd_path": rest.split()[0] if rest else "", "name": rest})
        if action == "stop":
            return _t_stop({})
        if action in ("light", "led"):
            return _t_light({"mode": rest or "on"})
        return f"Unknown action {action!r}. Use: status | discover | upload <file> | print <sd_path> | stop | light"
    except Exception as exc:  # never raise into the session
        return f"bambu: {exc}"


# ── CLI subcommand ─────────────────────────────────────────────────────

def register_cli_bambu(parser) -> None:
    parser.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=["status", "discover", "upload", "print", "stop", "light"],
        help="what to do (default: status)",
    )
    parser.add_argument("arg", nargs="?", default="", help="action argument (file path / sd_path / light mode)")


def _cli_handler(args) -> None:
    print(slash_bambu(f"{args.action} {args.arg}"))


# ── registration ───────────────────────────────────────────────────────

def register(ctx) -> None:
    """Register bambu tools, the /bambu slash command, and `hermes bambu` CLI."""
    for t in _tools():
        ctx.register_tool(
            name=t["name"],
            toolset=t["toolset"],
            schema=t["schema"],
            handler=t["handler"],
            description=t["description"],
            requires_env=["BAMBU_ACCESS_CODE"],
        )
    ctx.register_command(
        "bambu",
        handler=slash_bambu,
        description="Bambu Lab LAN-mode printer: status/discover/upload/print/stop/light",
        args_hint="[status|discover|upload <file>|print <sd_path>|stop|light]",
    )
    ctx.register_cli_command(
        "bambu",
        help="Bambu Lab LAN-mode printer control (status/discover/upload/print/stop/light)",
        setup_fn=register_cli_bambu,
        handler_fn=_cli_handler,
        description="Bambu Lab LAN-mode printer control",
    )
