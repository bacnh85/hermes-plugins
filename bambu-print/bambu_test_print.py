#!/usr/bin/env python3
"""Bambu verification print — one-shot end-to-end test.

Slices a tiny 20x20x1mm PLA square for a Bambu A1 Mini (OrcaSlicer),
uploads it over FTPS, and starts the print. Proves the whole chain:
slice -> upload -> MQTT start -> physical print.

Verified live 2026-09-08: print completed 100% on an A1 Mini (serial
0309AA461500125). Requires:
  - OrcaSlicer in /Applications (brew install --cask orcaslicer)
  - BAMBU_* env vars (access code at minimum)
  - Printer Developer Mode enabled (Settings -> LAN Only -> Developer Mode)
  - Filament loaded

Usage:  python3 bambu_test_print.py            # full: slice+upload+start
        python3 bambu_test_print.py --status   # just poll status
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bambu as B  # noqa: E402

ORCA = "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer"
ORCA_DATADIR = os.path.expanduser("~/Library/Application Support/OrcaSlicer")
BBL = os.path.join(ORCA_DATADIR, "system/BBL")

# A1 Mini presets (bundled with OrcaSlicer; must exist after first GUI run).
MACHINE = "Bambu Lab A1 mini 0.4 nozzle"
PROCESS = "0.20mm Standard @BBL A1M"
FILAMENT = "Bambu PLA Basic @BBL A1M"


def env() -> dict[str, str]:
    return {
        "host": os.getenv("BAMBU_HOST", ""),
        "serial": os.getenv("BAMBU_SERIAL", ""),
        "code": os.getenv("BAMBU_ACCESS_CODE", ""),
    }


def make_stl(path: str) -> None:
    """20x20x1mm square STL (minimal, no deps)."""
    import struct

    os.makedirs(os.path.dirname(path), exist_ok=True)

    def tri(f, a, b, c):
        f.write(struct.pack("<3f", 0, 0, 0))
        for v in (a, b, c):
            f.write(struct.pack("<3f", *v))
        f.write(struct.pack("<H", 0))

    s, h = 20.0, 1.0
    V = {
        "a": (0, 0, 0), "b": (s, 0, 0), "c": (s, s, 0), "d": (0, s, 0),
        "A": (0, 0, h), "B": (s, 0, h), "C": (s, s, h), "D": (0, s, h),
    }
    faces = [
        ("a", "b", "c"), ("a", "c", "d"), ("A", "C", "B"), ("A", "D", "C"),
        ("a", "B", "b"), ("a", "A", "B"), ("b", "C", "c"), ("b", "B", "C"),
        ("c", "D", "d"), ("c", "C", "D"), ("d", "A", "a"), ("d", "D", "A"),
    ]
    with open(path, "wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", len(faces)))
        for fc in faces:
            tri(f, *[V[v] for v in fc])


def slice_for_a1mini(stl: str, out_3mf: str) -> None:
    """Slice via OrcaSlicer CLI with the A1 Mini presets."""
    if not os.path.exists(ORCA):
        raise SystemExit(f"OrcaSlicer not found at {ORCA} — brew install --cask orcaslicer")
    for p in (MACHINE, PROCESS, FILAMENT):
        base = os.path.join(BBL, "machine" if "nozzle" in p else ("process" if "mm " in p or "Standard" in p else "filament"))
        # simpler: just rely on load flags below (filenames derived from presets)
    machine_json = os.path.join(BBL, "machine", MACHINE + ".json")
    process_json = os.path.join(BBL, "process", PROCESS + ".json")
    filament_json = os.path.join(BBL, "filament", FILAMENT + ".json")
    for p in (machine_json, process_json, filament_json):
        if not os.path.exists(p):
            raise SystemExit(f"preset not found: {p} — run OrcaSlicer GUI once to import vendor presets")
    cmd = [
        ORCA, "--slice", "0", "--export-3mf", out_3mf,
        "--load-settings", f"{machine_json};{process_json}",
        "--load-filaments", filament_json,
        stl,
    ]
    print("Slicing:", " ".join(cmd[:4]), "...")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if not os.path.exists(out_3mf):
        print(proc.stdout[-800:] or proc.stderr[-800:])
        raise SystemExit("slice failed — no .3mf produced")
    print(f"Sliced OK -> {out_3mf} ({os.path.getsize(out_3mf)} bytes)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Bambu verification print")
    ap.add_argument("--status", action="store_true", help="poll status only")
    ap.add_argument("--upload-only", action="store_true", help="slice + upload, no print")
    ap.add_argument("--remote-name", default="hermes_test.gcode.3mf")
    ap.add_argument("--workdir", default="/tmp/bambu_test")
    args = ap.parse_args()

    e = env()
    if not e["code"]:
        raise SystemExit("BAMBU_ACCESS_CODE not set")
    if not e["host"] or not e["serial"]:
        found = B.discover()
        if not found:
            raise SystemExit("printer not found on LAN; set BAMBU_HOST/BAMBU_SERIAL")
        e["host"], e["serial"] = found[0]["host"], found[0]["serial"]
        print(f"Discovered printer: {e['host']} / {e['serial']}")

    if args.status:
        with B.BambuMQTT(e["host"], e["serial"], e["code"]) as p:
            st = p.status()
        print(f"stage={st.get('stage_label')} progress={st.get('progress_pct')}% "
              f"nozzle={st.get('nozzle_c')}C remaining={st.get('remaining_min')}min")
        return 0

    stl = os.path.join(args.workdir, "cal_square.stl")
    out = os.path.join(args.workdir, "out.gcode.3mf")
    make_stl(stl)
    slice_for_a1mini(stl, out)

    print("Uploading over FTPS ...")
    sd = B.upload(e["host"], e["code"], out, remote_name=args.remote_name)
    print(f"Uploaded -> {sd}")
    if args.upload_only:
        return 0

    print("Starting print ...")
    res = B.start(e["host"], e["serial"], e["code"], sd, subtask_name="hermes_test")
    print("Start result:", res)
    if not res.get("started"):
        print("NOTE: printer may be waiting on screen confirmation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
