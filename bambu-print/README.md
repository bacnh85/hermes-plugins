# bambu-print (Hermes plugin)

Control a **Bambu Lab 3D printer in LAN-only mode** from Hermes — discover it
on the network, read live status, upload sliced jobs, start/stop prints.
**No Bambu cloud account, no Bambu Studio, no serial bridge.** Works with
A1 Mini / A1 / P1 / X1 series that expose LAN mode.

Live-verified 2026-09-08 against an **A1 Mini** (serial `0309AA461500125`,
LAN-only, access-code auth).

## What you can do

| Tool / command | What it does |
|---|---|
| `bambu_discover` | Find Bambu printer(s) on the LAN. Probes MQTT TLS `:8883` in parallel, reads the **serial from the TLS certificate CN** — no need to type it. |
| `bambu_status` | Live telemetry: nozzle/bed temps (+targets), print stage (`idle`/`printing`/…), progress %, remaining minutes. |
| `bambu_upload` | Push a sliced `.gcode.3mf` to the printer SD card over FTPS (`:990`, implicit TLS). Returns the SD path (`1:/…`). |
| `bambu_print` | Start a print (MQTT `print.project_file`). Watch the printer screen for a possible confirm dialog. |
| `bambu_stop` | Stop the running job. |
| `bambu_light` | Chamber light on/off/flashing — the **harmless write test** that proves MQTT control before any real print. |

In-session: `/bambu status`, `/bambu discover`, `/bambu upload <file>`, `/bambu print <sd_path>`, `/bambu stop`, `/bambu light`
Terminal: `hermes bambu <action> [arg]` (same actions)

## Install

```bash
hermes plugins install bacnh85/hermes-plugins/bambu-print --enable
hermes gateway restart
```

Add to `~/.hermes/.env`:

```bash
BAMBU_ACCESS_CODE=34415382        # printer screen: Settings → LAN → Access code (REQUIRED)
BAMBU_HOST=172.30.60.20           # optional — auto-discovered if unset
BAMBU_SERIAL=0309AA461500125      # optional — read from the TLS cert if unset
```

Dependency: `paho-mqtt` (Python) — install with
`pip install paho-mqtt` into the Hermes venv if `bambu_status` reports it missing.

## End-to-end print workflow

1. **Slice** the model for the A1 Mini in Bambu Studio / OrcaSlicer
   (choose the A1 Mini profile + your filament), export a **`.gcode.3mf`**.
   Bambu's `project_file` command only accepts 3MF-wrapped gcode — a plain
   `.gcode` is rejected by design. (`bambu_test_print.py` does this for a
   tiny 20×20×1mm square — the end-to-end verification part.)
2. **Check filament first**: `hermes bambu filament` — confirms which AMS
   slots have material vs the external spool. **An A1 Mini with an AMS
   Lite will silently "print" from an EMPTY external spool** (job runs,
   heats, extrudes nothing) if you send `use_ams:false` — the plugin now
   defaults to AMS feeding and reports the empty-spool trap.
3. **Upload** it: `hermes bambu upload /path/to/plate_1.gcode.3mf`
   → prints `Uploaded OK → SD 1:/plate_1.gcode.3mf`
4. **Start**: `hermes bambu print '1:/plate_1.gcode.3mf'` (uses AMS slot 0
   by default; pass `--arg ams_slot=N` to pick another tray). The AMS
   feeds automatically — no manual filament pushing needed.
5. **Monitor / stop** as needed: `hermes bambu status` (stage, progress %,
   temps), `hermes bambu stop`.

Live-verified 2026-09-08: 20×20×1mm red PLA square printed to 100% via AMS
slot 0 on an A1 Mini — first attempt (external spool, no filament) ran
empty; adding `use_ams:true` + `ams_mapping` fixed it.

## For other Hermes agents (integration notes)

- The plugin registers real **agent tools** (toolset `bambu`), so a Hermes
  agent can discover/status/upload/print by itself — no shelling out.
- **Order matters**: `bambu_upload` first (returns the `sd_path`), then
  `bambu_print` with that exact path. `bambu_print` without an upload is a
  no-op error.
- **Always `bambu_status` first** to confirm the printer is idle
  (`stage_label: idle`) before starting a job, and gate any real print
  behind user confirmation — it moves a hot machine.
- Use `bambu_light on` as the write-control smoke test before the first
  real print on a new printer.
- If `BAMBU_HOST` is unset, tool calls auto-run discovery (a few seconds);
  setting it in `.env` removes that latency.

## How it talks to the printer (protocol summary)

```
MQTT   ssl://<host>:8883   user: bblp   pass: <LAN access code>
       reports → device/<serial>/report   (JSON, full printer state)
       commands → device/<serial>/request
FTPS   ftps://<host>:990   implicit TLS, user bblp / access code
       uploads land on the SD card root (1:/ on A1 series)
Start  {"print":{"command":"project_file","param":"Metadata/plate_1.gcode",
        "url":"file://1:/<file>", ...}}
```

The TLS certificate is self-signed per printer with `CN=<serial>` (issuer
`BBL Technologies`) — that is how discovery learns the serial, and why the
client skips cert verification (identity = access code + serial pairing).
Full wire details live in `bambu.py`'s docstring; upstream reference:
[Doridian/OpenBambuAPI](https://github.com/Doridian/OpenBambuAPI).

## Safety notes

- Uploading/starting/stoppping **moves real hardware** — always confirm with
  the user before `bambu_print`, and make sure filament is loaded and the
  bed is clear.
- `bambu_status` / `bambu_discover` / `bambu_light` are safe to run anytime.
- The printer keeps its own thermal protection; this plugin only sends the
  same commands Bambu Studio would.
