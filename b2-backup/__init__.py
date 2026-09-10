"""b2-backup plugin for Hermes Agent.

Self-backup of the Hermes home directory to Backblaze B2, encrypted with
restic before anything leaves the machine:

  * restic NATIVE B2 backend (no rclone dependency)
  * repo: b2:<B2_BUCKET>/<repo_prefix>/<host>   (default prefix
    "hermes-selfbackup") — per-host, so several Hermes machines back up to
    the same bucket without ever touching each other's snapshots (or the
    homelab-playbook's separate b2:<bucket>/hermes repo)
  * everything under $HERMES_HOME by default, minus regenerable code/caches
  * retention via `forget --prune` (playbook-parity defaults: last 7,
    daily 7, weekly 4, monthly 6)

.env (secrets — NEVER in config.yaml):
  B2_ACCOUNT_ID        Backblaze keyID
  B2_APPLICATION_KEY   Backblaze applicationKey
  B2_BUCKET            bucket name (not a secret, but lives here for .env.example parity)
  RESTIC_PASSWORD      repo encryption password — lose it = lose the backups

config.yaml (settings — plugins.entries.b2-backup.settings):
  paths:        [..]  dirs/files to back up (default: whole HERMES_HOME)
  excludes:     [..]  restic exclude patterns (sensible defaults, merged)
  repo_prefix:  hermes-selfbackup
  host:         override hostname segment of the repo path
  tag:          hermes-selfbackup
  keep_last / keep_daily / keep_weekly / keep_monthly: retention (7/7/4/6)
  timeout_sec:  subprocess timeout for `run` (default 3600)

Surfaces:
  tool   hermes_backup_run  (toolset "backup", actions run/status/snapshots/
         restore/forget/unlock/check/init)
  slash  /b2backup <action> [args]
  CLI    hermes b2backup <subcommand>
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

logger = __import__("logging").getLogger(__name__)

_PLUGIN_ID = "b2-backup"
_TOOL_ID = "hermes_backup_run"

# Regenerable / restorable-by-git subtrees of $HERMES_HOME we skip by
# default. state.db sidecars (wal/shm/journal) are transient SQLite state.
_DEFAULT_EXCLUDES = [
    "hermes-agent",      # the source checkout (~1.5G, git-restorable)
    "node",
    "lsp",
    "bin",
    "cache",
    "logs",
    "pastes",
    "hermes-runtime",
    "state.db-wal",
    "state.db-shm",
    "state.db-journal",
]

_ACTIONS = ("run", "status", "snapshots", "restore", "forget", "unlock", "check", "init")


# ── config plumbing ────────────────────────────────────────────────────

def _settings(ctx: Any) -> dict[str, Any]:
    """Plugin settings with defaults (settings keys under plugins.entries.b2-backup.settings)."""
    s: dict[str, Any] = {}
    for key, default in (
        ("paths", None),
        ("excludes", None),
        ("repo_prefix", "hermes-selfbackup"),
        ("host", ""),
        ("tag", "hermes-selfbackup"),
        ("keep_last", 7),
        ("keep_daily", 7),
        ("keep_weekly", 4),
        ("keep_monthly", 6),
        ("timeout_sec", 3600),
    ):
        try:
            v = ctx.get_config(key)
        except Exception:
            v = None
        s[key] = default if v is None else v
    return s


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home()).expanduser().resolve()
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser().resolve()


def _host(settings: dict[str, Any]) -> str:
    name = str(settings.get("host") or "").strip() or os.uname().nodename.split(".")[0]
    name = re.sub(r"[^A-Za-z0-9._-]", "-", name).strip("-.")
    return name or "unknown-host"


def _repo(settings: dict[str, Any]) -> str:
    bucket = (os.environ.get("B2_BUCKET") or "").strip()
    prefix = str(settings.get("repo_prefix") or "hermes-selfbackup").strip("/")
    # restic B2 location syntax: b2:<bucket>:<path> (colon, not slash)
    return f"b2:{bucket}:{prefix}/{_host(settings)}"


def _restic_env(repo: str) -> dict[str, str]:
    env = os.environ.copy()
    acct = (os.environ.get("B2_ACCOUNT_ID") or "").strip()
    key = (os.environ.get("B2_APPLICATION_KEY") or "").strip()
    # restic's B2 env names vary by version (B2_ACCOUNT_ID/B2_ACCOUNT_KEY vs
    # B2_APPLICATION_KEY[_ID]) — set every alias so any restic works.
    env["B2_ACCOUNT_ID"] = acct
    env["B2_APPLICATION_KEY_ID"] = acct
    env["B2_ACCOUNT_KEY"] = key
    env["B2_APPLICATION_KEY"] = key
    env["RESTIC_PASSWORD"] = os.environ.get("RESTIC_PASSWORD") or ""
    env["RESTIC_REPOSITORY"] = repo  # ours wins over any stray shell value
    return env


def _readiness(settings: dict[str, Any]) -> str | None:
    """Human-readable reason we can't run, or None when ready."""
    if not shutil.which("restic"):
        return "restic binary not found — install it first (macOS: brew install restic / Debian: apt install restic)"
    for var in ("B2_ACCOUNT_ID", "B2_APPLICATION_KEY", "B2_BUCKET", "RESTIC_PASSWORD"):
        if not (os.environ.get(var) or "").strip():
            return f"{var} is not set — add it to ~/.hermes/.env"
    return None


def _run_restic(repo: str, args: list[str], timeout: int) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["restic", "-r", repo, *args, "--json"] if args[0] in ("backup", "snapshots", "stats")
        else ["restic", "-r", repo, *args],
        env=_restic_env(repo),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ── one-process run lock ───────────────────────────────────────────────

_RUN_LOCK = threading.Lock()
_RUN_RUNNING = False


# ── actions ────────────────────────────────────────────────────────────

def _act_init(settings: dict[str, Any]) -> dict[str, Any]:
    repo = _repo(settings)
    rc, out, err = _run_restic(repo, ["init"], timeout=300)
    if rc == 0 or "config file already exists" in (err + out):
        return {"ok": True, "action": "init", "repo": repo,
                "message": "repository ready (created now)" if rc == 0 else "repository already existed"}
    return {"ok": False, "action": "init", "repo": repo, "error": (err or out).strip()[-2000:]}


def _act_run(settings: dict[str, Any]) -> dict[str, Any]:
    global _RUN_RUNNING
    if not _RUN_LOCK.acquire(blocking=False):
        return {"ok": False, "action": "run", "error": "a backup is already running in this process"}
    _RUN_RUNNING = True
    try:
        home = _hermes_home()
        paths = [str(Path(p)).strip() for p in (settings.get("paths") or [])] or [str(home)]
        excludes = list(dict.fromkeys(
            _DEFAULT_EXCLUDES + [str(e) for e in (settings.get("excludes") or [])]
        ))
        args = ["backup", *paths]
        for pat in excludes:
            args += ["--exclude", pat]
        args += ["--tag", str(settings.get("tag") or "hermes-selfbackup")]
        rc, out, err = _run_restic(_repo(settings), args, timeout=int(settings.get("timeout_sec") or 3600))
        if rc != 0:
            return {"ok": False, "action": "run", "repo": _repo(settings),
                    "error": (err or out).strip()[-2000:]}
        return {"ok": True, "action": "run", "repo": _repo(settings), **_backup_summary(out)}
    finally:
        _RUN_RUNNING = False
        _RUN_LOCK.release()


def _backup_summary(json_out: str) -> dict[str, Any]:
    """Pull the final summary line out of `restic backup --json` output."""
    for line in reversed((json_out or "").strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if data.get("message_type") == "summary":
            return {
                "snapshot_id": data.get("snapshot_id"),
                "files_new": data.get("files_new"),
                "files_changed": data.get("files_changed"),
                "files_unmodified": data.get("files_unmodified"),
                "total_files": data.get("total_files_processed"),
                "bytes_processed": data.get("total_bytes_processed"),
                "bytes_added": data.get("data_added"),
                "duration_sec": data.get("total_duration") and round(data["total_duration"], 1),
            }
    return {}


def _act_snapshots(settings: dict[str, Any]) -> dict[str, Any]:
    rc, out, err = _run_restic(_repo(settings), ["snapshots"], timeout=300)
    if rc != 0:
        return {"ok": False, "action": "snapshots", "error": (err or out).strip()[-2000:]}
    snaps = []
    for line in (out or "").strip().splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, list):
            snaps = data  # restic snapshots --json prints one JSON array
    return {
        "ok": True,
        "action": "snapshots",
        "count": len(snaps),
        "snapshots": [
            {
                "id": s.get("short_id") or (s.get("id") or "")[:8],
                "time": s.get("time"),
                "tags": s.get("tags"),
                "paths": s.get("paths"),
            }
            for s in snaps
        ][-30:],
    }


def _act_status(settings: dict[str, Any]) -> dict[str, Any]:
    repo = _repo(settings)
    result: dict[str, Any] = {"ok": True, "action": "status", "repo": repo,
                              "hermes_home": str(_hermes_home())}
    rc, out, err = _run_restic(repo, ["stats", "--mode", "raw-data"], timeout=600)
    if rc == 0:
        try:
            data = json.loads(out or "{}")
            result["repo_b2_bytes_stored"] = data.get("total_blob_size") or data.get("total_size")
            result["repo_file_count"] = data.get("total_blob_count") or data.get("total_file_count")
        except ValueError:
            pass
    else:
        msg = (err or out).strip()
        if "unable to open config file" in msg or "does not exist" in msg:
            result["note"] = "repository not initialized yet (run action=init)"
        else:
            result["stats_error"] = msg[-500:]
    snaps = _act_snapshots(settings)
    if snaps.get("ok"):
        result["snapshot_count"] = snaps.get("count")
        result["latest_snapshot"] = (snaps.get("snapshots") or [None])[-1]
    else:
        result["snapshots_error"] = snaps.get("error", "")[-500:]
    return result


def _act_restore(settings: dict[str, Any], snapshot_id: str, target: str,
                 confirm: bool) -> dict[str, Any]:
    if not target:
        return {"ok": False, "action": "restore",
                "error": "target directory required (action=restore, snapshot_id=..., target=...)"}
    target_path = Path(target).expanduser()
    home = _hermes_home()
    try:
        target_resolved = target_path.resolve()
    except OSError:
        target_resolved = target_path
    inside_home = target_resolved == home or home in target_resolved.parents
    if inside_home and not confirm:
        return {"ok": False, "action": "restore",
                "error": (f"refusing to restore into {home} (live Hermes state) without "
                          f"confirm=true — restore to a scratch dir first, or pass confirm=true to overwrite")}
    rc, out, err = _run_restic(
        _repo(settings),
        ["restore", snapshot_id or "latest", "--target", str(target_path)],
        timeout=int(settings.get("timeout_sec") or 3600),
    )
    if rc != 0:
        return {"ok": False, "action": "restore", "error": (err or out).strip()[-2000:]}
    return {"ok": True, "action": "restore", "snapshot_id": snapshot_id or "latest",
            "target": str(target_path), "output": out.strip()[-1000:]}


def _act_forget(settings: dict[str, Any]) -> dict[str, Any]:
    args = ["forget",
            "--keep-last", str(settings.get("keep_last") or 7),
            "--keep-daily", str(settings.get("keep_daily") or 7),
            "--keep-weekly", str(settings.get("keep_weekly") or 4),
            "--keep-monthly", str(settings.get("keep_monthly") or 6),
            "--prune"]
    rc, out, err = _run_restic(_repo(settings), args, timeout=1800)
    if rc != 0:
        return {"ok": False, "action": "forget", "error": (err or out).strip()[-2000:]}
    return {"ok": True, "action": "forget",
            "policy": f"last {settings.get('keep_last')}/daily {settings.get('keep_daily')}/"
                      f"weekly {settings.get('keep_weekly')}/monthly {settings.get('keep_monthly')}",
            "output": (out or "").strip()[-1500:]}


def _act_unlock(settings: dict[str, Any]) -> dict[str, Any]:
    rc, out, err = _run_restic(_repo(settings), ["unlock"], timeout=300)
    if rc != 0:
        return {"ok": False, "action": "unlock", "error": (err or out).strip()[-2000:]}
    return {"ok": True, "action": "unlock", "message": "stale locks removed"}


def _act_check(settings: dict[str, Any], read_data: bool) -> dict[str, Any]:
    args = ["check"] + (["--read-data"] if read_data else [])
    rc, out, err = _run_restic(_repo(settings), args, timeout=int(settings.get("timeout_sec") or 3600))
    return {
        "ok": rc == 0,
        "action": "check",
        "read_data": read_data,
        "output": ((out or "") + (err or "")).strip()[-1500:],
    }


# ── unified entry point ────────────────────────────────────────────────

def backup_action(ctx: Any, action: str = "run", snapshot_id: str = "",
                  target: str = "", confirm: bool = False,
                  read_data: bool = False) -> dict[str, Any]:
    settings = _settings(ctx)
    action = (action or "run").strip().lower()
    if action not in _ACTIONS:
        return {"ok": False, "error": f"unknown action {action!r} — one of {', '.join(_ACTIONS)}"}
    not_ready = _readiness(settings)
    if not_ready:
        return {"ok": False, "action": action, "error": not_ready}
    if action == "run":
        return _act_run(settings)
    if action == "init":
        return _act_init(settings)
    if action == "snapshots":
        return _act_snapshots(settings)
    if action == "status":
        return _act_status(settings)
    if action == "restore":
        return _act_restore(settings, snapshot_id, target, confirm)
    if action == "forget":
        return _act_forget(settings)
    if action == "unlock":
        return _act_unlock(settings)
    if action == "check":
        return _act_check(settings, read_data)
    return {"ok": False, "error": "unreachable"}


# ── surfaces: tool / slash / CLI ───────────────────────────────────────

def _tool_handler(ctx: Any):
    def handler(args: dict, **kw) -> str:
        try:
            result = backup_action(
                ctx,
                action=str(args.get("action") or "run"),
                snapshot_id=str(args.get("snapshot_id") or ""),
                target=str(args.get("target") or ""),
                confirm=bool(args.get("confirm")),
                read_data=bool(args.get("read_data")),
            )
        except subprocess.TimeoutExpired:
            result = {"ok": False, "error": "restic timed out — raise b2-backup.settings.timeout_sec"}
        except Exception as exc:  # never raise into the agent loop
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return json.dumps(result, indent=2, default=str)
    return handler


def _pretty(result: dict[str, Any]) -> str:
    ok = result.get("ok")
    head = "OK" if ok else "FAILED"
    lines = [f"b2-backup {result.get('action', '')}: {head}"]
    for key in ("repo", "snapshot_id", "target", "count", "snapshot_count",
                "bytes_added", "bytes_processed", "total_files", "policy", "message", "note"):
        if key in result:
            lines.append(f"  {key}: {result[key]}")
    if result.get("latest_snapshot"):
        ls = result["latest_snapshot"]
        lines.append(f"  latest: {ls.get('id')} @ {ls.get('time')}")
    if not ok and result.get("error"):
        lines.append(f"  error: {result['error']}")
    return "\n".join(str(x) for x in lines)


# ── surfaces: slash / CLI (share the module ctx captured at register) ──

_CTX: Any = None


def _kv_args(raw: str) -> dict[str, Any]:
    """'restore abc123 target=/tmp/r confirm=true' → dict (key=val / bare tokens)."""
    out: dict[str, Any] = {}
    for tok in (raw or "").split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k.strip()] = v.strip()
        else:
            out.setdefault("positional", []).append(tok)
    pos = out.pop("positional", [])
    if pos and "action" not in out:
        out["action"] = pos.pop(0)
    for key in ("snapshot_id", "target"):
        if not out.get(key) and pos:
            out[key] = pos.pop(0)
    return out


def _slash_b2backup(raw_args: str) -> str:
    args = _kv_args(raw_args)
    try:
        result = backup_action(
            _CTX,
            action=str(args.get("action") or "run"),
            snapshot_id=str(args.get("snapshot_id") or ""),
            target=str(args.get("target") or ""),
            confirm=str(args.get("confirm", "")).lower() in ("1", "true", "yes", "y"),
            read_data=str(args.get("read_data", "")).lower() in ("1", "true", "yes", "y"),
        )
    except subprocess.TimeoutExpired:
        result = {"ok": False, "error": "restic timed out — raise b2-backup.settings.timeout_sec"}
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return _pretty(result)


def _cli_setup(subparsers) -> None:
    p_run = subparsers.add_parser("run", help="encrypted incremental backup now")
    p_run.set_defaults(cli_action="run")
    subparsers.add_parser("status", help="repo size + latest snapshot").set_defaults(cli_action="status")
    subparsers.add_parser("snapshots", help="list snapshots").set_defaults(cli_action="snapshots")
    subparsers.add_parser("init", help="initialize the B2 repo (idempotent)").set_defaults(cli_action="init")
    subparsers.add_parser("forget", help="apply retention policy + prune").set_defaults(cli_action="forget")
    subparsers.add_parser("unlock", help="remove stale repo locks").set_defaults(cli_action="unlock")
    p_check = subparsers.add_parser("check", help="repo integrity check")
    p_check.add_argument("--read-data", action="store_true", help="verify data content too (slow)")
    p_check.set_defaults(cli_action="check")
    p_restore = subparsers.add_parser("restore", help="restore a snapshot to a target dir")
    p_restore.add_argument("snapshot_id", nargs="?", default="latest")
    p_restore.add_argument("target", help="target directory (scratch dir recommended)")
    p_restore.add_argument("--confirm", action="store_true",
                           help="allow restoring into the live Hermes home")
    p_restore.set_defaults(cli_action="restore")


def _cli_handler(args) -> None:
    result = backup_action(
        _CTX,
        action=str(getattr(args, "cli_action", None) or "run"),
        snapshot_id=str(getattr(args, "snapshot_id", "") or ""),
        target=str(getattr(args, "target", "") or ""),
        confirm=bool(getattr(args, "confirm", False)),
        read_data=bool(getattr(args, "read_data", False)),
    )
    print(_pretty(result))


def register(ctx) -> None:
    """Register the backup tool, /b2backup slash command, and `hermes b2backup` CLI."""
    global _CTX
    _CTX = ctx
    ctx.register_tool(
        name=_TOOL_ID,
        toolset="backup",
        schema={
            "name": _TOOL_ID,
            "description": (
                "Backup/self-restore of this Hermes home to Backblaze B2 (restic-encrypted). "
                "Actions: run (default — incremental encrypted snapshot + nothing else), "
                "status (repo size + latest snapshot), snapshots (list), "
                "restore (snapshot_id + target scratch dir), forget (apply retention + prune), "
                "unlock, check, init."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": list(_ACTIONS),
                               "description": "default: run"},
                    "snapshot_id": {"type": "string",
                                    "description": "for restore — snapshot short id, or 'latest'"},
                    "target": {"type": "string",
                               "description": "for restore — target directory (use a scratch dir; "
                                              "restoring into the live Hermes home needs confirm=true)"},
                    "confirm": {"type": "boolean",
                                "description": "required to restore into the live Hermes home"},
                    "read_data": {"type": "boolean",
                                  "description": "for check — full data read (slow) instead of metadata-only"},
                },
            },
        },
        handler=_tool_handler(ctx),
        description="Encrypted Hermes home backup to Backblaze B2 via restic",
        requires_env=["B2_ACCOUNT_ID", "B2_APPLICATION_KEY", "B2_BUCKET", "RESTIC_PASSWORD"],
        check_fn=lambda: bool(shutil.which("restic")),
    )
    ctx.register_command(
        "b2backup",
        handler=_slash_b2backup,
        description="Hermes home backup to Backblaze B2 (restic-encrypted): run/status/snapshots/restore/forget",
        args_hint="[run|status|snapshots|restore <id> target=...|forget|unlock|check]",
    )
    ctx.register_cli_command(
        "b2backup",
        help="Back up this Hermes home to Backblaze B2 (restic-encrypted)",
        setup_fn=_cli_setup,
        handler_fn=_cli_handler,
        description="Backblaze B2 backup of the Hermes home (restic)",
    )
