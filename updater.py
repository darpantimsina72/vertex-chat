#!/usr/bin/env python3
"""Self-update from GitHub.

On launch, the app checks the public GitHub repo for a newer version and, if one
exists, downloads it and copies the new files over the app folder. Your data is
never touched: .env, the .venv folder, and chat history (which lives in the
browser) are all left alone.

No git and no login required -- it downloads the branch as a zip over plain HTTPS.

Config comes from update.json next to this file, so a fork only edits that file:
    { "owner": "darpantimsina72", "repo": "vertex-chat", "branch": "main" }
"""
import json
import os
import shutil
import ssl
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "update.json"
APPLIED = BASE / ".applied-commit"   # last version we installed (short sha)

# Never overwrite these when applying an update -- they hold user data / local state.
PROTECT = {".env", ".venv", ".git", ".applied-commit", "__pycache__", "logs"}

# Proxy-free opener: corporate proxies choke on these calls otherwise.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_UA = {"User-Agent": "vertex-chat-updater"}


def _config():
    try:
        c = json.loads(CONFIG.read_text(encoding="utf-8"))
        return c["owner"], c["repo"], c.get("branch", "main")
    except Exception:
        return None


def _get(url, timeout=10):
    req = urllib.request.Request(url, headers=_UA)
    # Fall back to an unverified context only if the platform's certs are missing
    # (some minimal Windows Python installs); GitHub is HTTPS either way.
    try:
        return _OPENER.open(req, timeout=timeout)
    except ssl.SSLError:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return _OPENER.open(req, timeout=timeout, context=ctx)


def applied_commit() -> str:
    try:
        return APPLIED.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def remote_commit(owner, repo, branch) -> str:
    """Latest commit sha on the branch (via GitHub API). '' if unreachable."""
    url = f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}"
    try:
        with _get(url) as r:
            return json.load(r).get("sha", "")[:12]
    except Exception:
        return ""


def _download_zip(owner, repo, branch, dest: Path):
    url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
    with _get(url, timeout=60) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def _apply(zip_path: Path):
    """Extract the downloaded zip and copy its files over the app folder."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmpdir)
        # Archive contains a single top folder: <repo>-<branch>/
        tops = [p for p in tmpdir.iterdir() if p.is_dir()]
        if not tops:
            raise RuntimeError("downloaded update was empty")
        src = tops[0]
        if not (src / "app.py").exists():
            raise RuntimeError("downloaded update looks incomplete (no app.py)")
        _copy_tree(src, BASE)
    # GitHub zip archives don't carry the executable bit, so restore it on the
    # shell launcher (macOS/Linux) -- otherwise ./run.sh breaks after an update.
    if os.name != "nt":
        sh = BASE / "run.sh"
        if sh.exists():
            try:
                sh.chmod(sh.stat().st_mode | 0o111)
            except Exception:
                pass


def _copy_tree(src: Path, dst: Path):
    for item in src.iterdir():
        if item.name in PROTECT:
            continue
        target = dst / item.name
        if item.is_dir():
            # Refresh the whole directory (e.g. static/) so removed files go away.
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def check_and_update(say, ask_yes_no=None, force=False) -> bool:
    """Check GitHub and apply an update if one is available.

    Returns True if files were updated (caller may want to relaunch).
    Fails silently/offline -- an update problem must never block launching the app.
    """
    cfg = _config()
    if not cfg:
        return False
    owner, repo, branch = cfg

    remote = remote_commit(owner, repo, branch)
    if not remote:
        return False  # offline or GitHub unreachable -- just run what we have

    current = applied_commit()
    if not force and current and remote == current:
        return False  # already up to date

    # First run after a manual download has no applied-commit yet. Record the
    # current version silently instead of re-downloading what the user just got.
    if not current and not force:
        _record(remote)
        return False

    if ask_yes_no is not None:
        say()
        say(f"An update is available ({current or '?'} -> {remote}).")
        if not ask_yes_no("Download and install it now?"):
            return False

    say("Updating to the latest version from GitHub...")
    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tf:
            zpath = Path(tf.name)
        try:
            _download_zip(owner, repo, branch, zpath)
            _apply(zpath)
        finally:
            zpath.unlink(missing_ok=True)
    except Exception as e:
        say(f"Update skipped ({e}). Starting the current version instead.")
        return False

    _record(remote)
    say("Update installed.")
    return True


def _record(sha: str):
    try:
        APPLIED.parent.mkdir(parents=True, exist_ok=True)
        APPLIED.write_text(sha, encoding="utf-8")
    except Exception:
        pass
