#!/usr/bin/env python3
"""Cross-platform launcher for Vertex Chat.

Run with any Python 3.10+:
    python start.py          (Windows -- or just double-click run.bat)
    python3 start.py         (macOS / Linux -- or ./run.sh)

What it does (safe to re-run any time):
  1. If the app is already running, just opens it in your browser.
  2. Creates a virtual environment (.venv) if missing; repairs it if broken.
  3. Installs requirements -- with retries and clear help if the network fights back.
     Skipped entirely once installed, so later launches work offline.
  4. Creates .env from .env.example if missing.
  5. Picks a free port, starts the server, opens your browser when it is ready.

Flags:
  --reinstall    force re-install of requirements
  --no-browser   do not open a browser window
  --no-update    skip the check for a newer version on GitHub
  --update       force re-download of the latest version, even if up to date
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import venv
from pathlib import Path

BASE = Path(__file__).resolve().parent
VENV = BASE / ".venv"
REQS = BASE / "requirements.txt"
DEPS_MARKER = VENV / ".deps-ok"

PYPI_HOSTS = ["pypi.org", "files.pythonhosted.org"]

# Corporate Windows machines often have a system proxy that urllib would use even
# for 127.0.0.1 (the '<local>' bypass rule ignores dotted hosts). Loopback checks
# must never go through a proxy, or the browser would never auto-open.
NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Held (bound) for the whole life of this launcher so a second double-click can
# detect us instead of racing the install or starting a duplicate server.
GUARD_PORT = 47821
_guard_socket = None


# ---------------------------------------------------------------- helpers

def say(msg=""):
    print(msg, flush=True)


def fail(msg):
    # run.bat has `pause`, so the window stays open for the user to read this.
    say()
    say("=" * 64)
    say(msg)
    say("=" * 64)
    sys.exit(1)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def reqs_hash() -> str:
    return hashlib.sha256(REQS.read_bytes()).hexdigest()[:16]


def env_file_value(name: str) -> str:
    # Tiny .env read (no dotenv here -- it lives inside the venv we may not have yet).
    env = BASE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    return ""


def preferred_port() -> int:
    raw = os.environ.get("PORT") or env_file_value("PORT") or "8000"
    try:
        return int(raw)
    except ValueError:
        return 8000


# ---------------------------------------------------------------- single instance

def our_app_at(port: int) -> bool:
    """True if a Vertex Chat instance answers on this port."""
    try:
        with NO_PROXY_OPENER.open(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            return bool(json.load(r).get("ok"))
    except Exception:
        return False


def find_running_instance() -> int | None:
    start = preferred_port()
    for port in range(start, start + 11):
        if our_app_at(port):
            return port
    return None


def acquire_single_instance() -> bool:
    global _guard_socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", GUARD_PORT))
        s.listen(1)
        _guard_socket = s  # keep it alive; released automatically on exit
        return True
    except OSError:
        s.close()
        return False


# ---------------------------------------------------------------- venv

def ensure_venv() -> Path:
    py = venv_python()
    if py.exists():
        # Repair a half-created venv (e.g. a previous run was interrupted).
        probe = subprocess.run([str(py), "-c", "import pip"], capture_output=True)
        if probe.returncode == 0:
            return py
        say("Existing .venv looks broken - recreating it...")
        shutil.rmtree(VENV, ignore_errors=True)

    say("Creating virtual environment (.venv)...")
    try:
        venv.create(VENV, with_pip=True)
    except Exception as e:
        shutil.rmtree(VENV, ignore_errors=True)
        fail(f"Could not create the virtual environment: {e}\n\n"
             "Fixes to try:\n"
             "  - Reinstall Python 3.10+ from https://www.python.org/downloads/\n"
             "    (on Windows, tick 'Add python.exe to PATH' during install)\n"
             "  - Move this folder somewhere simple like C:\\vertex-chat\n"
             "    (OneDrive/network folders sometimes block this)")
    if not venv_python().exists():
        shutil.rmtree(VENV, ignore_errors=True)
        fail("Virtual environment was created but its Python is missing.\n"
             "Antivirus may have quarantined it - add this folder to the\n"
             "antivirus exclusion list, then run this again.")
    return venv_python()


# ---------------------------------------------------------------- install

def run_pip(py: Path, extra_args) -> tuple[int, str]:
    """Run pip install, streaming output live AND capturing it for diagnosis."""
    cmd = [str(py), "-m", "pip", "install",
           "--prefer-binary", "--retries", "5", "--timeout", "60",
           "--disable-pip-version-check",
           *extra_args, "-r", str(REQS)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    captured = []
    for line in proc.stdout:
        print(line, end="", flush=True)
        captured.append(line)
    proc.wait()
    return proc.returncode, "".join(captured)


def diagnose(output: str) -> str:
    """Map raw pip failure output to a plain-language hint."""
    low = output.lower()
    if "certificate_verify_failed" in low or "sslerror" in low or "ssl:" in low:
        return ("Secure connection to the package servers failed - common on corporate\n"
                "or VPN networks that inspect SSL traffic. Try the relaxed-SSL retry below.")
    if "proxyerror" in low or ("407" in low and "proxy" in low):
        return ("A proxy is blocking the download. Ask IT for the proxy address, then\n"
                "set it before running again:  set HTTPS_PROXY=http://proxy.example:8080")
    if "10054" in low or "connectionreset" in low or "connection broken" in low:
        return ("The network connection keeps dropping mid-download. Usual causes:\n"
                "  - VPN or firewall cutting large downloads  -> try without VPN,\n"
                "    or on a different network (e.g. phone hotspot) just for the install\n"
                "  - Antivirus scanning downloads             -> pause it for 2 minutes\n"
                "After the one-time install finishes, the app works offline.")
    if "read timed out" in low or "timed out" in low:
        return "The connection is very slow. Just run this again - it resumes where it left off."
    if "no matching distribution" in low or "could not find a version" in low:
        return ("Packages were not found. Either the network is blocking pypi.org, or this\n"
                "Python version is too new/old. Python 3.12 or 3.13 is the safest choice.")
    if "winerror 5" in low or "access is denied" in low or "permission" in low:
        return ("Windows blocked file access. Close other copies of this app, or move the\n"
                "folder out of OneDrive/Downloads to somewhere like C:\\vertex-chat.\n"
                "Antivirus 'ransomware protection' can also cause this.")
    if "no space left" in low or "not enough space" in low or "errno 28" in low:
        return "The disk is full - free up ~200 MB and run this again."
    if "microsoft visual c++" in low or ("building wheel" in low and "error" in low):
        return ("A package tried to compile from source (no prebuilt wheel for this Python\n"
                "version). Install Python 3.12 or 3.13 from python.org and run this again.")
    return "Check your internet connection and run this again."


def ssl_retry_may_help(output: str) -> bool:
    """Only offer the relaxed-SSL retry when the failure looks network/SSL-shaped.
    Disk-full or permission errors would fail identically with --trusted-host."""
    low = output.lower()
    return any(k in low for k in (
        "ssl", "certificate", "10054", "connectionreset", "connection reset",
        "connection broken", "connection aborted", "proxy", "timed out",
        "no matching distribution", "could not find a version",
    ))


def ask_yes_no(question: str) -> bool:
    try:
        return input(question + "  (type y then Enter for yes; just Enter for no) ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def install_requirements(py: Path, force: bool) -> None:
    if not REQS.exists():
        fail("requirements.txt is missing - re-download the project.")

    if not force and DEPS_MARKER.exists() and DEPS_MARKER.read_text().strip() == reqs_hash():
        return  # already installed for this exact requirements.txt -- instant, offline launch

    say("Installing requirements (one-time, may take a minute)...")
    say()

    attempts = [
        ("", []),
        ("Retrying (fresh download, no cache)...", ["--no-cache-dir"]),
    ]
    output = ""
    for note, extra in attempts:
        if note:
            say()
            say(note)
            time.sleep(2)
        code, output = run_pip(py, extra)
        if code == 0:
            DEPS_MARKER.write_text(reqs_hash())
            say()
            say("Requirements installed.")
            return

    say()
    say("-" * 64)
    say("The download failed. Likely reason:")
    say()
    say(diagnose(output))
    say("-" * 64)

    # Last resort for corporate networks that intercept SSL. Explicit consent only:
    # it disables certificate verification for the Python package servers
    # (pypi.org and files.pythonhosted.org) for this single install command, so
    # downloads could in theory be tampered with on a hostile network. Only offered
    # when the failure actually looks like a network/SSL problem.
    if ssl_retry_may_help(output):
        say()
        say("If you are on a corporate or VPN network, a relaxed-SSL retry usually works.")
        say("It skips certificate checks for the Python package servers (pypi.org,")
        say("files.pythonhosted.org) for this one install only - less secure, so only")
        say("do this on a network you trust (like your office), not public Wi-Fi.")
        if ask_yes_no("Try the relaxed-SSL install now?"):
            say()
            extra = []
            for host in PYPI_HOSTS:
                extra += ["--trusted-host", host]
            code, output = run_pip(py, extra)
            if code == 0:
                DEPS_MARKER.write_text(reqs_hash())
                say()
                say("Requirements installed.")
                return
            say()
            say(diagnose(output))

    # Download failed, but maybe enough is already installed to run today.
    # Deliberately do NOT write the marker: next launch retries the install,
    # so updated requirements are not silently dropped forever.
    probe = subprocess.run([str(py), "-c", "import fastapi, uvicorn, httpx, dotenv"],
                           capture_output=True)
    if probe.returncode == 0:
        say()
        say("Download had errors, but the app can still start with what is already")
        say("installed. It will try to finish the install next time you launch it.")
        return

    fail("Could not install requirements.\n\n"
         "It is safe to simply run this again - it resumes where it left off.\n"
         "If it keeps failing, try a different network (phone hotspot) once;\n"
         "after the one-time install the app works offline.")


# ---------------------------------------------------------------- run

def ensure_env_file() -> None:
    env, example = BASE / ".env", BASE / ".env.example"
    if not env.exists() and example.exists():
        shutil.copy(example, env)
        say("Created .env - add your API key there, or set it in the app's Settings (gear icon).")


def pick_port() -> int:
    start = preferred_port()
    for port in range(start, start + 11):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
        if port != start:
            say(f"Port {start} is busy - using {port} instead.")
        return port
    fail(f"No free port found between {start} and {start + 10}.\n"
         "Close other running programs using these ports and try again.")


def open_browser_when_ready(url: str) -> None:
    def poll():
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                with NO_PROXY_OPENER.open(url + "/api/health", timeout=2):
                    import webbrowser
                    webbrowser.open(url)
                    return
            except Exception:
                time.sleep(0.5)
    threading.Thread(target=poll, daemon=True).start()


def maybe_self_update(force: bool) -> None:
    """Apply a GitHub update if available, then relaunch so the new start.py runs.
    Kept optional -- a missing/broken updater must never stop the app launching."""
    try:
        import updater
    except Exception:
        return
    try:
        updated = updater.check_and_update(say, force=force)
    except Exception as e:
        say(f"Update check skipped ({e}).")
        return
    # If files changed, re-exec this launcher once so a changed start.py takes
    # effect immediately. The guard env var prevents any relaunch loop.
    if updated and not os.environ.get("VCHAT_RELAUNCHED"):
        say("Restarting with the updated version...")
        os.environ["VCHAT_RELAUNCHED"] = "1"
        args = [a for a in sys.argv[1:] if a != "--update"]  # don't force again
        try:
            os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *args])
        except Exception:
            pass  # fall through and run the (updated) files in this process


def main() -> None:
    if sys.version_info < (3, 10):
        fail(f"Python 3.10 or newer is required (you have {sys.version.split()[0]}).\n"
             "Install Python 3.12 or 3.13 from https://www.python.org/downloads/\n"
             "On Windows, tick 'Add python.exe to PATH' during install.")

    os.chdir(BASE)
    force = "--reinstall" in sys.argv
    no_browser = "--no-browser" in sys.argv

    # Second double-click? Reuse the running app instead of starting a duplicate
    # (a duplicate would land on a new port, where the browser has no saved key
    # or chat history -- very confusing).
    running = find_running_instance()
    if running:
        say(f"Vertex Chat is already running - opening http://127.0.0.1:{running}")
        if not no_browser:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{running}")
        return

    # Auto-update from GitHub before installing/launching, so new requirements and
    # new code are picked up. Fails silently offline; never blocks the app.
    if "--no-update" not in sys.argv:
        maybe_self_update(force="--update" in sys.argv)

    if not acquire_single_instance():
        fail("Vertex Chat is already starting in another window.\n"
             "Please wait for that window to finish, then use the browser tab it opens.\n"
             "(If no other window exists, wait a minute and run this again.)")

    py = ensure_venv()
    install_requirements(py, force)
    ensure_env_file()

    port = pick_port()
    url = f"http://127.0.0.1:{port}"
    say()
    say(f"Starting Vertex Chat on {url}")
    say(f"If the browser does not open by itself, open this address: {url}")
    say("Keep this window open while using the app. Press Ctrl+C to stop.")
    say()
    if not no_browser:
        open_browser_when_ready(url)
    try:
        code = subprocess.call([str(py), "-m", "uvicorn", "app:app",
                                "--host", "127.0.0.1", "--port", str(port)])
        if code != 0:
            fail("The server stopped unexpectedly. Run this again.\n"
                 "If it keeps happening, delete the .venv folder and run again\n"
                 "for a clean re-install.")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
