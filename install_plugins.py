#!/usr/bin/env python3
"""Bootstrap installer for the hermes-plugins repo (github.com/bacnh85/hermes-plugins).

Doctrine (keep it simple):

  * Most plugins install fine with Hermes' built-in CLI:

        hermes plugins install bacnh85/hermes-plugins/<name>

    That is the canonical path for every `kind: memory` and general plugin
    (memory discovery scans the general plugins dir, so the native install
    location is exactly right). Updates: `hermes plugins update <name>`.

  * EXCEPT `kind: model-provider`. The native CLI always installs to
    `$HERMES_HOME/plugins/<name>/` (general dir), and provider discovery
    (`providers/__init__.py::_discover_providers`) scans ONLY
    `$HERMES_HOME/plugins/model-providers/<name>/`. The general PluginManager
    deliberately does not import `kind: model-provider` modules either (it
    records the manifest and skips: "handled by providers/ discovery").
    Net effect: a native-installed provider shows as "enabled" but the model
    picker finds no models. So THIS installer exists for exactly one job:
    put model-provider plugins where provider discovery reads, prompt for
    their `requires_env`, and point `model.provider` at the new provider.

Usage:
    python install_plugins.py                  # handle ALL plugins in the repo
    python install_plugins.py omniroute        # just omniroute
    python install_plugins.py omniroute munin  # several
    python install_plugins.py --symlink        # dev: symlink instead of copy
    python install_plugins.py --no-config      # install only; don't touch config / .env
    python install_plugins.py --refresh        # overwrite existing plugin code

Cross-platform (Windows / macOS / Linux), stdlib only.
"""

from __future__ import annotations

import argparse
import atexit
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
REPO_SLUG = "bacnh85/hermes-plugins"
TARBALL = "https://codeload.github.com/bacnh85/hermes-plugins/tar.gz/refs/heads/main"

# Kinds this installer must handle locally. Everything else is delegated to
# `hermes plugins install bacnh85/hermes-plugins/<name>` (the native CLI).
LOCAL_KINDS = {"model-provider"}


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


# --------------------------------------------------------------------------- #
# Native (delegated) install — the default path
# --------------------------------------------------------------------------- #
def native_install(home: Path, meta: dict, *, enable: bool) -> bool:
    """Install via the built-in CLI: hermes plugins install <slug>/<name>.

    Returns True when the CLI reported success. Never touches .env or config —
    the native installer already prompts for requires_env, and memory-kind
    activation is one explicit `hermes config set memory.provider <name>`
    the user controls.
    """
    cmd = cli_prefix(home) + [
        "plugins", "install", f"{REPO_SLUG}/{meta['slug']}",
        "--enable" if enable else "--no-enable",
    ]
    # Stream output (not captured) so the native prompts for env vars work.
    log("  > " + " ".join(cmd))
    try:
        proc = subprocess.run(cmd, timeout=300)
    except FileNotFoundError:
        log("  ERROR: Hermes CLI not found; install the plugin manually:")
        log(f"         hermes plugins install {REPO_SLUG}/{meta['slug']}")
        return False
    if proc.returncode != 0:
        log(f"  native install of {meta['name']} failed (exit {proc.returncode})")
        return False
    log(f"  {meta['name']}: installed via native CLI")
    return True


# --------------------------------------------------------------------------- #
# Local install — model-provider only
# --------------------------------------------------------------------------- #
def install_plugin(home: Path, meta: dict, *, refresh: bool, symlink: bool) -> Path:
    target = home / "plugins" / "model-providers" / meta["name"]
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
# .env wiring (model-provider path only)
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
        label = f"  {var} for '{meta['name']}': "
        prompt = (lambda: getpass.getpass(label)) if _looks_secret(var) else (lambda: input(label))  # noqa: E731
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
    ap = argparse.ArgumentParser(
        description="Install plugins from hermes-plugins: model-providers locally, "
                    "everything else via `hermes plugins install`",
    )
    ap.add_argument("names", nargs="*", help="Which plugins to handle (default: all)")
    ap.add_argument("--symlink", action="store_true", help="model-providers: symlink instead of copy (dev)")
    ap.add_argument("--refresh", action="store_true", help="model-providers: overwrite existing install")
    ap.add_argument("--no-enable", action="store_true",
                    help="native installs: install disabled (default is --enable)")
    ap.add_argument("--no-config", dest="do_config", action="store_false",
                    help="model-providers: install only; do not touch config / .env")
    ap.set_defaults(do_config=True)
    args = ap.parse_args()

    log("hermes-plugins installer")
    home = hermes_home()
    log(f"  hermes home: {home}")
    log(f"  venv python: {find_venv_python(home) or '(falling back to `hermes` command)'}")

    # Source: local checkout if present, else fetch from GitHub (only needed
    # for model-providers — native installs fetch the repo themselves).
    local = local_repo_root()
    if local:
        log(f"  source: local checkout {local}")
        repo_root: Path | None = local
    else:
        log("  source: fetching from GitHub")
        tmp = Path(tempfile.mkdtemp(prefix="hermes-plugins-src-"))
        # plugin dirs are referenced after this block; clean up at exit
        atexit.register(shutil.rmtree, tmp, ignore_errors=True)
        repo_root = fetch_repo_root(tmp)

    if repo_root is None:
        raise SystemExit("Could not resolve the plugin source.")

    all_plugins = discover_plugins(repo_root)
    if not all_plugins:
        raise SystemExit(f"No plugins (dirs with plugin.yaml) found under {repo_root}.")
    if args.names:
        wanted = set(args.names)
        selected = [p for p in all_plugins if p["name"] in wanted or p["slug"] in wanted]
        missing = wanted - {p["name"] for p in selected} - {p["slug"] for p in selected}
        if missing:
            raise SystemExit(
                f"Unknown plugin(s): {', '.join(sorted(missing))}. "
                f"Available: {', '.join(p['name'] for p in all_plugins)}"
            )
    else:
        selected = all_plugins

    failures = 0
    for meta in selected:
        local_kind = meta["kind"] in LOCAL_KINDS
        log(f"\n[{meta['name']}] kind={meta['kind']} -> "
            f"{'this installer' if local_kind else 'native `hermes plugins install`'}")
        if local_kind:
            install_plugin(home, meta, refresh=args.refresh, symlink=args.symlink)
            if args.do_config:
                ensure_env(home, meta)
                set_cfg = cli_prefix(home) + ["config", "set", "model.provider", meta["name"]]
                run(set_cfg, check=False)
        else:
            if not native_install(home, meta, enable=not args.no_enable):
                failures += 1
                continue
            if meta["kind"] == "memory" and args.do_config:
                # Activation is a single explicit config key — the native
                # installer doesn't manage provider config for us.
                log("  activate with: hermes config set memory.provider " + meta["name"])

    log(
        "\ndone."
        + (f" {failures} native install(s) FAILED — see above." if failures else "")
        + " Restart Hermes (or run `hermes doctor`) to pick up new providers/plugins."
    )
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
