"""Bambu Lab LAN-mode printing library for Hermes.

Pure-stdlib + ``paho-mqtt`` (the only third-party dep; auto-installed on
first use). Talks to Bambu printers in **LAN-only mode** over their local
MQTT API (TLS 8883) and FTPS file service (implicit TLS 990) — no Bambu
cloud account, no Bambu Studio, no serial bridge required.

Protocol facts (verified live 2026-09-07 against an A1 Mini, serial
``0309AA461500125``, access code auth):

* MQTT:   ``ssl://<host>:8883``, username ``bblp``, password = LAN access
          code. TLS cert is self-signed per-printer: the certificate CN
          IS the printer serial number (issuer ``BBL Technologies``), so
          we read the serial straight out of the TLS handshake during
          discovery — no need to type it.
* Topics: reports stream on ``device/<serial>/report`` (JSON); commands
          go to ``device/<serial>/request``.
* FTPS:   ``ftps://<host>:990`` implicit TLS, user ``bblp``, password =
          access code. Print jobs upload to the SD card root (``1:/`` on
          the A1 series) as ``.gcode.3mf`` files.
* Start:  MQTT ``print.project_file`` with ``param`` = the in-3mf path
          (``Metadata/plate_1.gcode``), ``url`` = the sd-card file path.

Everything here is read-only except ``upload()`` / ``start()`` /
``stop()`` / ``light()`` — those move the real machine, so callers gate
them behind explicit confirmation.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import time
from typing import Any, Callable, Optional

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - first-run install path
    mqtt = None

MQTT_PORT = 8883
FTPS_PORT = 990
REPORT_TOPIC = "device/{serial}/report"
REQUEST_TOPIC = "device/{serial}/request"

# Upload root on the printer SD card (A1 series uses "1:/").
SD_ROOT = "1:/"


class BambuError(RuntimeError):
    """Raised for protocol/connection failures with a human message."""


# ── config ─────────────────────────────────────────────────────────────

def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def printer_config() -> dict[str, str]:
    """Resolve printer config from the environment.

    Returns ``{"host", "serial", "access_code"}``. ``host``/``serial`` may
    be empty when auto-discovery is wanted (then call ``discover()``).
    Raises BambuError when the access code is missing.
    """
    code = _env("BAMBU_ACCESS_CODE")
    if not code:
        raise BambuError(
            "BAMBU_ACCESS_CODE is not set — add it to ~/.hermes/.env "
            "(printer screen: Settings → LAN → Access code)"
        )
    return {
        "host": _env("BAMBU_HOST"),
        "serial": _env("BAMBU_SERIAL"),
        "access_code": code,
    }


# ── TLS / serial discovery ─────────────────────────────────────────────

def serial_from_tls(host: str, port: int = MQTT_PORT, timeout: float = 5.0) -> str:
    """Read the printer serial from its TLS certificate CN.

    Bambu LAN certs are self-signed per printer with ``CN=<serial>``
    (issuer ``CN=BBL CA, O=BBL Technologies``). This is the discovery
    trick that removes the need to look the serial up on the screen.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as sock:
            der = sock.getpeercert(binary_form=True)
    if not der:
        raise BambuError(f"{host}: no TLS certificate presented on :{port}")
    # Parse the CN out of the DER: openssl needs -inform DER (default is PEM).
    import subprocess

    proc = subprocess.run(
        ["openssl", "x509", "-inform", "DER", "-noout", "-subject"],
        input=der,
        capture_output=True,
        timeout=10,
    )
    subject = proc.stdout.decode(errors="replace")
    # subject= /CN=0309AA461500125
    for part in subject.split("/"):
        if part.startswith("CN="):
            return part[3:].strip()
    raise BambuError(f"{host}: no CN found in TLS cert subject: {subject!r}")


def discover(subnet: Optional[str] = None, timeout: float = 1.2) -> list[dict[str, str]]:
    """Scan the local LAN for Bambu printers.

    Fast path: probe :8883 concurrently across the subnet (short per-host
    timeout), then read the TLS CN only on hosts with the port open — the
    CN is authoritative (BBL issuer + serial). Returns a list of
    ``{"host", "serial"}`` dicts, empty when none found. ``subnet`` is a
    ``host/prefix`` CIDR (default: each active interface's /24).
    """
    candidates: list[str] = []
    if subnet:
        candidates = _hosts_in_cidr(subnet)
    else:
        for cidr in _local_cidrs():
            candidates.extend(_hosts_in_cidr(cidr))
    own = set(_self_ips())
    open_hosts: list[str] = []

    def probe(host: str) -> None:
        if host in own:
            return
        try:
            with socket.create_connection((host, MQTT_PORT), timeout=timeout):
                open_hosts.append(host)
        except Exception:
            pass

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        list(pool.map(probe, candidates))

    found: list[dict[str, str]] = []
    for host in sorted(open_hosts):
        try:
            if not _is_bambu(host):
                continue
            serial = serial_from_tls(host, timeout=2.0)
            found.append({"host": host, "serial": serial})
        except Exception:
            continue
        if len(found) >= 4:  # safety cap
            break
    return found


def _is_bambu(host: str, port: int = MQTT_PORT, timeout: float = 3.0) -> bool:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as sock:
            der = sock.getpeercert(binary_form=True)
    import subprocess

    proc = subprocess.run(
        ["openssl", "x509", "-inform", "DER", "-noout", "-issuer"],
        input=der,
        capture_output=True,
        timeout=10,
    )
    return b"BBL" in proc.stdout


def _local_cidrs() -> list[str]:
    out: list[str] = []
    try:
        import subprocess

        for iface in ("en0", "en1", "en2", "en3"):
            proc = subprocess.run(
                ["ipconfig", "getifaddr", iface], capture_output=True, text=True, timeout=5
            )
            ip = proc.stdout.strip()
            if ip:
                out.append(f"{ip}/24")
    except Exception:
        pass
    return out or ["127.0.0.1/24"]


def _hosts_in_cidr(cidr: str) -> list[str]:
    host, _, prefix_s = cidr.partition("/")
    try:
        prefix = int(prefix_s)
    except ValueError:
        prefix = 24
    parts = host.split(".")
    if len(parts) != 4:
        return []
    base = int(parts[0]) << 24 | int(parts[1]) << 16 | int(parts[2]) << 8 | int(parts[3])
    mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF if prefix else 0
    net = base & mask
    hosts = []
    for i in range(1, 255):
        ip = net | i
        hosts.append(f"{(ip >> 24) & 255}.{(ip >> 16) & 255}.{(ip >> 8) & 255}.{ip & 255}")
    return hosts


def _self_ips() -> list[str]:
    try:
        import subprocess

        out: list[str] = []
        for iface in ("en0", "en1"):
            proc = subprocess.run(
                ["ipconfig", "getifaddr", iface], capture_output=True, text=True, timeout=5
            )
            ip = proc.stdout.strip()
            if ip:
                out.append(ip)
        return out
    except Exception:
        return []


# ── MQTT client ────────────────────────────────────────────────────────

class BambuMQTT:
    """A short-lived MQTT session to one printer.

    Use as a context manager: ``with BambuMQTT(host, serial, code) as p:``
    connects, subscribes to reports, and tears down cleanly.
    """

    def __init__(self, host: str, serial: str, access_code: str, timeout: float = 15.0):
        if mqtt is None:
            raise BambuError("paho-mqtt is required — run: pip install paho-mqtt")
        self.host = host
        self.serial = serial
        self.timeout = timeout
        self._messages: list[dict[str, Any]] = []
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.username_pw_set("bblp", access_code)
        # Self-signed per-printer cert: do not verify (identity is the
        # access code + serial pairing, both already in hand).
        self._client.tls_set(cert_reqs=ssl.CERT_NONE)
        self._client.tls_insecure_set(True)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect
        self._connected = False

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self._connected = reason_code == 0 or str(reason_code) == "Success"
        if self._connected:
            client.subscribe(REPORT_TOPIC.format(serial=self.serial), qos=0)

    def _on_disconnect(self, client, userdata, flags, reason_code=None, properties=None):
        self._connected = False

    def _on_message(self, client, userdata, msg):
        try:
            self._messages.append(json.loads(msg.payload.decode()))
        except Exception:
            pass

    def __enter__(self) -> "BambuMQTT":
        self._client.connect(self.host, MQTT_PORT, keepalive=30)
        self._client.loop_start()
        deadline = time.monotonic() + 8
        while not self._connected and time.monotonic() < deadline:
            time.sleep(0.1)
        if not self._connected:
            self._client.loop_stop()
            raise BambuError(f"MQTT connect failed on {self.host}:{MQTT_PORT} — wrong access code or printer offline?")
        return self

    def __exit__(self, *exc):
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            pass

    # -- low-level -------------------------------------------------------

    def _command(self, payload: dict[str, Any], seq: Optional[int] = None) -> None:
        """Send one command object to the printer (fire and forget)."""
        if seq is None:
            seq = int(time.time() * 1000) % 100000
        body = json.dumps(payload)
        # paho wants the sequence id inside each command's own block;
        # callers build the full block, we only wrap topic + delivery.
        self._client.publish(REQUEST_TOPIC.format(serial=self.serial), body)

    def _wait_report(self, predicate: Callable[[dict[str, Any]], bool], timeout: float) -> Optional[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for msg in self._messages:
                if predicate(msg):
                    return msg
            time.sleep(0.15)
        return None

    def pushall(self) -> dict[str, Any]:
        """Ask the printer to push its full state (``system.pushall``)."""
        self._client.publish(
            REQUEST_TOPIC.format(serial=self.serial),
            json.dumps({"pushing": {"sequence_id": str(int(time.time() % 100000)), "command": "pushall"}}),
        )
        state = self._wait_report(lambda m: "print" in m and "mc_percent" in m["print"], timeout=self.timeout)
        if state is None:
            # Fall back to whatever arrived last (warm-up reports still carry print state)
            for msg in reversed(self._messages):
                if "print" in msg:
                    state = msg
                    break
        return state or {}

    # -- high-level ops ---------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return a compact status dict (no command sent — read reports)."""
        self.pushall()
        state: dict[str, Any] = {"connected": True, "host": self.host, "serial": self.serial}
        for msg in reversed(self._messages):
            p = msg.get("print")
            if not p:
                continue
            state["nozzle_c"] = p.get("nozzle_temper")
            state["nozzle_target_c"] = p.get("nozzle_target_temper")
            state["bed_c"] = p.get("bed_temper")
            state["bed_target_c"] = p.get("bed_target_temper")
            state["stage"] = p.get("mc_print_stage")  # 1=idle, 2=printing...
            state["progress_pct"] = p.get("mc_percent")
            state["remaining_min"] = p.get("mc_remaining_time")
            state["cooling_fan"] = p.get("cooling_fan_speed")
            break
        # Command-center style stage label
        stage = state.get("stage")
        state["stage_label"] = {
            "1": "idle", "2": "printing", "3": "paused", "4": "finished",
            "5": "error", "6": "cooling",
        }.get(str(stage), f"unknown({stage})")
        return state

    def light(self, mode: str = "on") -> dict[str, Any]:
        """Toggle the chamber light (harmless write-test). mode: on|off|flashing."""
        if mode not in ("on", "off", "flashing"):
            raise BambuError("light mode must be on|off|flashing")
        self._client.publish(
            REQUEST_TOPIC.format(serial=self.serial),
            json.dumps({
                "system": {
                    "sequence_id": str(int(time.time() % 100000)),
                    "command": "ledctrl",
                    "led_node": "chamber_light",
                    "led_mode": mode,
                }
            }),
        )
        return {"sent": f"chamber_light {mode}"}

    def filament_status(self) -> dict[str, Any]:
        """Report which filament sources actually have material.

        Returns:
          ams:    list of {slot, type, name, color, remain_pct} for AMS trays
          spool:  external-spool (vt_tray) presence bool + type if loaded
        An A1 Mini with an AMS Lite attached reports both; printing with
        use_ams:false against an EMPTY spool runs silently and extrudes
        nothing — check this before starting a job.
        """
        self.pushall()
        out: dict[str, Any] = {"ams": [], "spool": {"present": False}}
        for msg in reversed(self._messages):
            p = msg.get("print")
            if not p:
                continue
            ams = (p.get("ams") or {}).get("ams") or []
            if ams:
                for unit in ams:
                    for t in unit.get("tray") or []:
                        ttype = str(t.get("tray_type") or "").strip()
                        if not ttype:
                            continue
                        out["ams"].append({
                            "slot": int(t.get("id") or 0),
                            "type": ttype,
                            "name": str(t.get("tray_id_name") or ""),
                            "color": str(t.get("tray_color") or ""),
                            "remain_pct": t.get("remain"),
                        })
            vt = p.get("vt_tray") or {}
            if vt.get("tray_type"):
                out["spool"] = {
                    "present": True,
                    "type": str(vt.get("tray_type")),
                    "name": str(vt.get("tray_id_name") or ""),
                }
            elif vt:
                out["spool"] = {"present": False}
            break
        return out

    def stop(self) -> dict[str, Any]:
        """Stop the current job (gcode_file_stop)."""
        self._client.publish(
            REQUEST_TOPIC.format(serial=self.serial),
            json.dumps({"print": {"sequence_id": str(int(time.time() % 100000)), "command": "gcode_file_stop"}}),
        )
        return {"sent": "stop"}


# ── FTPS upload ────────────────────────────────────────────────────────

def upload(host: str, access_code: str, local_path: str, remote_name: Optional[str] = None) -> str:
    """Upload a ``.gcode.3mf`` to the printer SD card via implicit-TLS FTP.

    Returns the SD path (``1:/<name>``) for use as the ``url`` in the
    start command. ``remote_name`` defaults to the local basename.
    """
    if not os.path.exists(local_path):
        raise BambuError(f"local file not found: {local_path}")
    name = remote_name or os.path.basename(local_path)
    if not name.lower().endswith(".3mf"):
        # Bambu accepts .gcode.3mf (3MF-wrapped gcode); plain gcode files
        # are NOT directly printable via project_file — warn loudly.
        raise BambuError(
            f"{name}: Bambu prints .gcode.3mf (sliced by Bambu Studio / OrcaSlicer). "
            "Plain .gcode is not accepted by project_file."
        )
    # curl FTPS (implicit TLS, :990): ftplib's data-channel TLS upgrade
    # (PROT P) hangs against Bambu firmware (live-tested 2026-09-08), while
    # curl negotiates the TLS data connection cleanly. Verified: STOR of a
    # 32KB .gcode.3mf completes and the file appears on the SD root.
    import shutil
    import subprocess

    curl = shutil.which("curl")
    if not curl:
        raise BambuError("curl is required for FTPS upload (not found in PATH)")
    proc = subprocess.run(
        [
            curl, "-sS", "--fail", "--connect-timeout", "10", "--max-time", "120",
            "-k", "--ftp-ssl", "--user", f"bblp:{access_code}",
            "-T", local_path,
            f"ftps://{host}:{FTPS_PORT}/{name}",
        ],
        capture_output=True,
        text=True,
        timeout=140,
    )
    if proc.returncode != 0:
        raise BambuError(f"FTPS upload failed (rc={proc.returncode}): {proc.stderr.strip()[:300]}")
    return SD_ROOT + name


# ── start print ────────────────────────────────────────────────────────

def start(
    host: str, serial: str, access_code: str,
    sd_path: str, subtask_name: str = "",
    bed_level: bool = True, timelapse: bool = False,
    use_ams: bool = True, ams_slot: int = 0,
) -> dict[str, Any]:
    """Start a print of an uploaded SD file via MQTT ``project_file``.

    ``sd_path`` is the ``1:/name.gcode.3mf`` string returned by upload().
    Returns the printer's print-state report shortly after the command.

    ``use_ams=True`` (default) feeds from the AMS/AMS-Lite; set False to
    use the external spool holder. ``ams_slot`` selects the AMS tray for
    ``ams_mapping``. Live lesson 2026-09-08: an A1 Mini WITH an AMS Lite
    will happily "print" from an EMPTY external spool if ``use_ams:false``
    is sent — the job runs, heats, and extrudes nothing. Always confirm
    which filament source has material before starting (read ``vt_tray``
    for the spool, ``ams[].tray[]`` for AMS slots).
    """
    # Bambu reads the gcode at a fixed internal path inside the 3mf.
    param = "Metadata/plate_1.gcode"
    seq = str(int(time.time() % 100000))
    # url = SD root (the printer mounts its SD at /mnt/sdcard); file = the
    # filename on that root. Format live-verified 2026-09-08 (fusing the
    # filename INTO url → HMS 0x10007 file-not-found; split form is correct).
    body = {
        "print": {
            "sequence_id": seq,
            "command": "project_file",
            "param": param,
            "project_id": "0",
            "profile_id": "0",
            "task_id": "0",
            "subtask_id": "0",
            "subtask_name": subtask_name,
            # A1-series mounts the SD at /sdcard (Home Assistant pybambu:
            # url = file:///sdcard/<name> for non-H2 printers). /mnt/sdcard
            # → HMS 0x10007 file-not-found on the A1 Mini (verified 09-08).
            "file": sd_path.split("/")[-1],
            "url": "file:///sdcard/" + sd_path.split("/")[-1],
            "md5": "",
            "timelapse": timelapse,
            "bed_type": "auto",
            "bed_levelling": bed_level,
            "flow_cali": False,
            "vibration_cali": False,
            "layer_inspect": False,
            "use_ams": use_ams,
            "ams_mapping": [] if not use_ams else [int(ams_slot)],
        }
    }
    with BambuMQTT(host, serial, access_code) as prn:
        prn._client.publish(REQUEST_TOPIC.format(serial=serial), json.dumps(body))
        # Watch for the job to leave idle (stage != 1) or an error ack.
        deadline = time.monotonic() + 30
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            for msg in prn._messages:
                p = msg.get("print") or {}
                if p.get("mc_percent") is not None:
                    last = p
                if str(p.get("mc_print_stage")) not in ("1", "None", "", None):
                    return {
                        "started": True,
                        "stage": p.get("mc_print_stage"),
                        "progress_pct": p.get("mc_percent"),
                    }
            time.sleep(0.3)
        # No stage change observed — report what we saw + the print command
        # may still be queued (A1 shows a confirm dialog on the screen for
        # LAN prints when the printer has a display).
        return {
            "started": False,
            "note": "no stage change within 30s — the A1 Mini screen may be showing a "
                    "confirm dialog; check the printer or run status again",
            "last_progress": last.get("mc_percent"),
        }
