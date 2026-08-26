#!/usr/bin/env python3
"""Bootstrap installer for the hermes-plugins repo (github.com/bacnh85/hermes-plugins).

Installs one or more plugins from this repo into this machine's Hermes so any
Hermes surface (CLI, desktop, gateway, LXC/CI, ...) can use them without
per-machine setup.

Cross-platform (Windows / macOS / Linux) and dependency-free (stdlib only).

Usage:
    python install_plugins.py                  # install ALL plugins in the repo
    python install_plugins.py omniroute        # install just omniroute
    python install_plugins.py omniroute other  # install several
    python install_plugins.py --symlink        # symlink instead of copy (dev: live changes)
    python install_plugins.py --no-config      # install only; don't touch config / .env
    python install_plugins.py --refresh        # overwrite plugin code even if present

Each plugin is routed to the directory its own discovery system reads, based
on the `kind` in its plugin.yaml:

    kind model-provider -> $HERMES_HOME/plugins/model-providers/<name>
    kind memory         -> $HERMES_HOME/plugins/memory/<name>
    anything else       -> $HERMES_HOME/plugins/<name>          (general plugin)

For each plugin, it also ensures the env vars declared in `requires_env` are
present in .env (prompting for missing ones), and if a plugin is a
model-provider it can point model.provider at it via `hermes config set`.
"""

from __future__ import annotations

import argparse
import getpass
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

REPO_HTTP = "https://github.com/bacnh85/hermes-plugins.git"
TARBALL = "https://codeload.github.com/bacnh85/hermes-plugins/tar.gz/refs/heads/main"


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


def find_venv_python(home: Path) -> str | None:
    """Locate the Hermes venv interpreter so `hermes` CLI subcommands resolve
    even when the platform `hermes` launcher is broken (e.g. system python)."""
    base = home / "hermes-agent"
    if sys.platform.startswith("win"):
        candidates = [base / "venv" / "Scripts" / "python.exe", base / ".venv" / "Scripts" / "python.exe"]
    else:
        candidates = [base / "venv" / "bin" / "python", base / ".venv" / "bin" / "python"]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def cli_prefix(home: Path) -> list[str]:
    python = find_venv_python(home)
    if python:
        # `hermes plugins` is a CLI subcommand; reach it via the module
        # entrypoint, not `python plugins ...`.
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
# Locate + parse plugins
# --------------------------------------------------------------------------- #
def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_manifest(path: Path) -> dict:
    """Minimal, dependency-free extractor for the fields the installer needs
    from a plugin.yaml: name, kind, and requires_env names."""
    text = _read_text(path)
    name = re.search(r"(?m)^name:\s*(\S+)\s*$", text)
    kind = re.search(r"(?m)^kind:\s*(\S+)\s*$", text)
    # requires_env entries look like:
    #   - MY_API_KEY
    #   - name: OTHER_KEY
    envs = [
        m.group(1)
        for m in re.finditer(r"(?m)^\s*-\s+(?:name:\s*)?([A-Z][A-Z0-9_]*)\s*$", text)
    ]
    return {
        "name": name.group(1) if name else None,
        "kind": (kind.group(1) if kind else "") or "standalone",
        "requires_env": envs,
    }


def local_repo_root() -> Path | None:
    """Return the repo root when run from inside an existing checkout."""
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").exists():
        return cwd
    return None


def fetch_repo_root(tmp: Path) -> Path:
    """Clone (or tarball) the repo and return a directory containing the
    plugin source dirs."""
    if shutil.which("git"):
        got = subprocess.run(
            ["git", "clone", "--depth", "1", REPO_HTTP, str(tmp / "repo")],
            capture_output=True, text=True, timeout=120,
        )
        if got.returncode == 0:
            return tmp / "repo"
        log("  git clone failed; falling back to tarball")
    req = urllib.request.Request(TARBALL, headers={"User-Agent": "hermes-plugins-installer/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        tf.extractall(tmp)
    for d in tmp.iterdir():
        if d.is_dir() and (d / "pyproject.toml").exists():
            return d
    raise SystemExit("Could not fetch the plugin source (git clone and tarball both failed).")


def discover_plugins(root: Path) -> list[dict]:
    """Return metadata for every plugin dir (a dir with a plugin.yaml) in root."""
    plugins = []
    if not root.is_dir():
        return plugins
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith((".", "_")):
            continue
        mf = d / "plugin.yaml"
        if not mf.exists():
            continue
        meta = parse_manifest(mf)
        meta["dir"] = d
        meta["slug"] = d.name
        if meta["name"]:
            plugins.append(meta)
    return plugins


def target_rel_dir(kind: str, name: str) -> str:
    """The plugins/ subdirectory a plugin of `kind` installs into (its own
    discovery system reads it)."""
    if kind == "model-provider":
        return f"model-providers/{name}"
    if kind == "memory":
        return f"memory/{name}"
    return name  # general plugins live at plugins/<name>


# --------------------------------------------------------------------------- #
# Install
# --------------------------------------------------------------------------- #
def install_plugin(home: Path, meta: dict, *, refresh: bool, symlink: bool) -> Path:
    rel = target_rel_dir(meta["kind"], meta["name"])
    target = home / "plugins" / rel
    if target.is_dir() and not refresh and not symlink:
        log(f"  {meta['name']}: already present at {target} (pass --refresh to overwrite)")
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    if symlink:
        if target.exists() or target.is_symlink():
            if target.is_symlink() or target.is_dir():
                target.unlink() if target.is_symlink() else shutil.rmtree(target)
        try:
            os.symlink(str(meta["dir"]), str(target))
        except OSError as e:
            raise SystemExit(f"Could not symlink {meta['name']}: {e}")
        log(f"  {meta['name']}: symlinked {target} -> {meta['dir']}")
        return target

    tmp = Path(tempfile.mkdtemp(prefix="hp-copy-", dir=str(home / "plugins")))
    try:
        work = tmp / meta["slug"]
        shutil.copytree(
            meta["dir"], work,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", "*.pyo", "*.egg-info"),
        )
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(work), str(target))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    log(f"  {meta['name']}: installed -> {target}")
    return target


# --------------------------------------------------------------------------- #
# .env wiring
# --------------------------------------------------------------------------- #
def _looks_secret(name: str) -> bool:
    up = name.upper()
    return any(k in up for k in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS"))


def ensure_env(home: Path, meta: dict) -> None:
    if not meta.get("requires_env"):
        return
    env_path = home / ".env"
    env_path.touch(exist_ok=True)
    lines = env_path.read_text(encoding="utf-8").splitlines()
    have = {l.split("=", 1)[0] for l in lines if "=" in l}

    added = False
    for var in meta["requires_env"]:
        if var in have:
            continue
        label = f"  {var} for '{meta['name']}'"
        if _looks_secret(var):
            label += ": "
            prompt = lambda: getpass.getpass(label)  # noqa: E731
        else:
            label += ": "
            prompt = lambda: input(label)  # noqa: E731
        try:
            val = prompt().strip()
        except (EOFError, KeyboardInterrupt):
            log(f"  (skipped) no value given for {var}; set it in .env later")
            continue
        if val:
            lines.append(f"{var}={val}")
            have.add(var)
            added = True
            log(f"  {var} written to .env")
        else:
            log(f"  (skipped) set {var} in .env later")
    if added:
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log("  .env updated")


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Install plugins from hermes-plugins into Hermes")
    ap.add_argument("names", nargs="*", help="Which plugins to install (default: all)")
    ap.add_argument("--symlink", action="store_true", help="symlink instead of copy (dev: live changes)")
    ap.add_argument("--refresh", action="store_true", help="overwrite plugin code even if present")
    ap.add_argument("--no-config", dest="do_config", action="store_false", help="install only; do not touch config")
    ap.set_defaults(do_config=True)
    args = ap.parse_args()

    log("hermes-plugins installer")
    home = hermes_home()
    log(f"  hermes home: {home}")
    log(f"  venv python: {find_venv_python(home) or '(falling back to `hermes` command)'}")

    # Source: local checkout if present, else fetch from GitHub.
    local = local_repo_root()
    if local:
        log(f"  source: local checkout {local}")
        repo_root = local
    else:
        log("  source: fetching from GitHub")
        tmp = Path(tempfile.mkdtemp(prefix="hermes-plugins-src-"))
        try:
            repo_root = fetch_repo_root(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    all_plugins = discover_plugins(repo_root)
    if not all_plugins:
        raise SystemExit(f"No plugins (dirs with plugin.yaml) found under {repo_root}.")
    if args.names:
        wanted = set(args.names)
        selected = [p for p in all_plugins if p["name"] in wanted or p["slug"] in wanted]
        missing = wanted - {p["name"] for p in selected} - {p["slug"] for p in selected}
        if missing:
            raise SystemExit(f"Unknown plugin(s): {', '.join(sorted(missing))}. Available: {', '.join(p['name'] for p in all_plugins)}")
    else:
        selected = all_plugins

    log(f"  plugins to install: {', '.join(p['name'] for p in selected)}")
    for meta in selected:
        log(f"\n[{meta['name']}] kind={meta['kind']}")
        install_plugin(home, meta, refresh=args.refresh, symlink=args.symlink)
        ensure_env(home, meta)
        if args.do_config and meta["kind"] == "model-provider":
            set_cfg = cli_prefix(home) + ["config", "set", "model.provider", meta["name"]]
            run(set_cfg, check=False)
    log("\ndone. Restart Hermes (or run `hermes doctor`) to pick up new providers/plugins.")


if __name__ == "__main__":
    main()