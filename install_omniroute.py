#!/usr/bin/env python3
"""Bootstrap installer for the omniroute Hermes provider plugin.

Installs the omniroute model-provider plugin into this machine's Hermes so any
Hermes surface (CLI, desktop, gateway, LXC/CI, ...) can route through OmniRoute
with no further setup.

Cross-platform (Windows / macOS / Linux): stdlib only, no pip dependencies.

Usage:
    python install_omniroute.py             # install + wire config
    python install_omniroute.py --refresh   # overwrite plugin code even if present
    python install_omniroute.py --self-config   # also set model.provider=omniroute
    python install_omniroute.py --no-config     # install only; don't touch config

What it does
   1. Locates $HERMES_HOME (HERMES_HOME env, else ~/.hermes).
   2. Fetches this repo (git clone, or tarball via urllib if git is absent)
      and copies omniroute/ to
      $HERMES_HOME/plugins/model-providers/omniroute -- the directory the
      provider-discovery scanner actually reads.
   3. Ensures OMNIROUTE_API_KEY / OMNIROUTE_BASE_URL are present in .env
      (prompts for the key when unset; never overwrites an existing value).
   4. Optionally points model.provider at omniroute via the supported
      `hermes config set` path (never hand-edits config.yaml).
"""

from __future__ import annotations

import argparse
import getpass
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

REPO_HTTP = "https://github.com/bacnh85/hermes-plugins.git"
TARBALL = "https://codeload.github.com/bacnh85/hermes-plugins/tar.gz/refs/heads/main"
PLUGIN = "omniroute"
SIGNUP_URL = "https://omniroute.online/"


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# --------------------------------------------------------------------------- #
# Environment / path resolution
# --------------------------------------------------------------------------- #
def hermes_home() -> Path:
    env = os.getenv("HERMES_HOME")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    home = Path.home() / ".hermes"
    if home.is_dir():
        return home
    raise SystemExit(
        "Could not find a Hermes home. Set HERMES_HOME or run inside a Hermes "
        "environment (expected ~/.hermes)."
    )


def find_venv_python() -> str | None:
    """Locate the Hermes venv interpreter so `hermes` CLI subcommands resolve
    even when the platform `hermes` launcher is broken (e.g. system python).
    """
    base = Path.home() / ".hermes" / "hermes-agent"
    if sys.platform.startswith("win"):
        candidates = [
            base / "venv" / "Scripts" / "python.exe",
            base / ".venv" / "Scripts" / "python.exe",
        ]
    else:
        candidates = [
            base / "venv" / "bin" / "python",
            base / ".venv" / "bin" / "python",
        ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def cli_prefix(python: str | None) -> list[str]:
    if python:
        # `hermes plugins` is a Hermes CLI subcommand; reach it through the
        # module entrypoint, not `python plugins ...`.
        return [python, "-m", "hermes_cli.main"]
    return ["hermes"]


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    log("  > " + " ".join(cmd))
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        if check:
            raise
        return subprocess.CompletedProcess(cmd, 1, "", "command not found")


# --------------------------------------------------------------------------- #
# Fetch + install the plugin directory
# --------------------------------------------------------------------------- #
def _download_tarball(dst_dir: Path) -> None:
    req = urllib.request.Request(TARBALL, headers={"User-Agent": "hermes-plugins-installer/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        tf.extractall(dst_dir)


def fetch_repo_root(tmp: Path) -> Path:
    """Return a directory containing omniroute/ (repo root)."""
    if shutil.which("git"):
        got = subprocess.run(
            ["git", "clone", "--depth", "1", REPO_HTTP, str(tmp / "repo")],
            capture_output=True, text=True, timeout=120,
        )
        if got.returncode == 0:
            return tmp / "repo"
        log("  git clone failed; falling back to tarball")
    _download_tarball(tmp)
    # tarball extracts to <tmp>/hermes-plugins-main/
    for d in tmp.iterdir():
        if d.is_dir() and (d / PLUGIN / "__init__.py").exists():
            return d
    raise SystemExit("Could not fetch the plugin source (git clone and tarball both failed).")


def install_plugin(home: Path, refresh: bool) -> Path:
    target = home / "plugins" / "model-providers" / PLUGIN
    if target.is_dir() and not refresh:
        log(f"  omniroute already present at {target} (pass --refresh to overwrite)")
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hermes-plugins-", dir=str(home / "plugins")) as tmp:
        src_root = fetch_repo_root(Path(tmp))
        src = src_root / PLUGIN
        if not src.is_dir():
            raise SystemExit(f"Plugin source {src} missing from the fetched repo.")
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target)
    log(f"  installed plugin -> {target}")
    return target


# --------------------------------------------------------------------------- #
# .env wiring
# --------------------------------------------------------------------------- #
def ensure_env(home: Path) -> None:
    env_path = home / ".env"
    env_path.touch(exist_ok=True)
    lines = env_path.read_text(encoding="utf-8").splitlines()
    have = {l.split("=", 1)[0] for l in lines if "=" in l}

    if "OMNIROUTE_API_KEY" not in have:
        key = getpass.getpass(f"  OmniRoute API key (get one at {SIGNUP_URL}): ").strip()
        if key:
            lines.append(f"OMNIROUTE_API_KEY={key}")
            log("  OMNIROUTE_API_KEY written to .env")
        else:
            log("  (skipped) set OMNIROUTE_API_KEY in .env later")
    if "OMNIROUTE_BASE_URL" not in have:
        log("  OMNIROUTE_BASE_URL unset -- the provider falls back to the default "
            "self-host endpoint; set it in .env to override")
    if lines != env_path.read_text(encoding="utf-8").splitlines():
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log("  .env updated")


def enable_and_configure(home: Path, python: str | None, do_config: bool) -> None:
    r = run(cli_prefix(python) + ["plugins", "enable", "omniroute"], check=False)
    if r.returncode != 0:
        log("  (note) `hermes plugins enable` unavailable/non-fatal")
    if do_config:
        run(cli_prefix(python) + ["config", "set", "model.provider", "omniroute"], check=False)


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Install the omniroute Hermes provider plugin")
    ap.add_argument("--refresh", action="store_true",
                    help="overwrite plugin code even if already installed")
    ap.add_argument("--no-config", dest="do_config", action="store_false",
                    help="install only; do not touch config")
    ap.add_argument("--self-config", dest="do_config", action="store_true")
    ap.set_defaults(do_config=True)
    args = ap.parse_args()

    log("hermes-plugins => omniroute installer")
    home = hermes_home()
    python = find_venv_python()
    log(f"  hermes home: {home}")
    log(f"  venv python: {python or '(falling back to `hermes` command)'}")

    install_plugin(home, args.refresh)
    ensure_env(home)
    enable_and_configure(home, python, args.do_config)

    log("done. Restart Hermes (or run `hermes doctor`) to pick up the provider.")
    log(f"  plugin: {home / 'plugins' / 'model-providers' / PLUGIN}")
    log(f"  source repo: {'https://github.com/bacnh85/hermes-plugins'}")


if __name__ == "__main__":
    main()