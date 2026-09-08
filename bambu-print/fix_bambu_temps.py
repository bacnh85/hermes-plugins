#!/usr/bin/env python3
"""Fix Bambu .gcode.3mf bed/nozzle temps after CLI slicing.

Problem (live-found 2026-09-08): OrcaSlicer CLI slicing with
--load-settings merges the MACHINE preset but DROPS the filament
preset's temperatures — the emitted gcode runs the bed at 35°C
(PLA needs 60-65°C) and nozzle at 200°C (PLA Basic wants ~220°C).
Result: the print completes but plastic does not adhere/release
correctly. Bambu Studio output does not have this problem.

This script rewrites the temperature commands inside a .gcode.3mf:
  M140/M190 bed  35 -> 65  (textured PEI plate, PLA)
  M104/M109 print-nozzle 200 -> 220  (only in the executable region)

Usage: python3 fix_bambu_temps.py in.gcode.3mf [out.gcode.3mf]
Defaults to overwriting a copy named <in>.fixed.gcode.3mf.
"""
from __future__ import annotations

import shutil
import sys
import zipfile

BED_TARGET = 65     # textured PEI plate for PLA (hot plate would be 60)
BED_COLD = 35       # the wrong value CLI slicing emits
NOZ_PRINT = 220     # PLA Basic print temp
NOZ_COLD = 200      # wrong value emitted at the actual print layers


def fix_gcode_3mf(src: str, dst: str) -> None:
    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        gcode_names = [n for n in names if n.endswith(".gcode")]
        if not gcode_names:
            raise SystemExit(f"{src}: no .gcode entry inside 3mf")
        gcode_name = gcode_names[0]
        data = zin.read(gcode_name).decode("utf-8", errors="replace")

    lines = data.splitlines(keepends=True)
    changed: list[str] = []
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("M140 S") or s.startswith("M190 S"):
            # bed temp command: lift 35 -> 65 (skip the S0 turn-off)
            if "S35" in s:
                lines[i] = line.replace("S35", f"S{BED_TARGET}")
                changed.append(f"L{i} bed {s[:24]} -> S{BED_TARGET}")
        elif s.startswith("M104 S200") or s.startswith("M109 S200"):
            # nozzle at 200 during print layers -> 220. The pre-print
            # clean/flush phases use 250/240/180 and stay untouched.
            if "S200" in s and "H" not in s:
                lines[i] = line.replace("S200", f"S{NOZ_PRINT}")
                changed.append(f"L{i} nozzle {s[:24]} -> S{NOZ_PRINT}")

    if not changed:
        print(f"{src}: no temp commands matched (already correct?) — copied unchanged")
    else:
        print(f"{src}: {len(changed)} temp fixes:")
        for c in changed:
            print("  ", c)

    fixed = "".join(lines)
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            content = zin.read(item.filename)
            if item.filename == gcode_name:
                content = fixed.encode("utf-8")
            zout.writestr(item, content)
    print(f"wrote {dst} ({__import__('os').path.getsize(dst)} bytes)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else src.replace(".gcode.3mf", ".fixed.gcode.3mf")
    fix_gcode_3mf(src, dst)
