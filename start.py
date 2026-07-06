#!/usr/bin/env python3
"""Cross-platform launcher for Vertex Chat.

Run with any Python 3.9+:
    python start.py          (Windows)
    python3 start.py         (macOS / Linux)

It creates a virtual environment, installs requirements, prepares .env,
starts the server and opens your browser. Safe to re-run.
"""
import os
import shutil
import subprocess
import sys
import threading
import venv
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
VENV = BASE / ".venv"


def port_from_env_file() -> str:
    # Tiny .env read (no dotenv here — it lives inside the venv we're about to create).
    env = BASE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("PORT="):
                return line.split("=", 1)[1].strip() or "8000"
    return "8000"


PORT = os.environ.get("PORT") or port_from_env_file()
URL = f"http://127.0.0.1:{PORT}"


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def ensure_venv() -> Path:
    py = venv_python()
    if not py.exists():
        print("Creating virtual environment (.venv)…")
        try:
            venv.create(VENV, with_pip=True)
        except Exception as e:
            # Broken half-created venv blocks every future run — remove and re-raise.
            shutil.rmtree(VENV, ignore_errors=True)
            sys.exit(f"Could not create virtual environment: {e}\n"
                     f"Make sure Python 3.9+ is installed from https://www.python.org/downloads/ "
                     f"(on Windows, tick 'Add python.exe to PATH').")
    return py


def install_requirements(py: Path) -> None:
    print("Installing requirements (first run may take a minute)…")
    r = subprocess.run([str(py), "-m", "pip", "install", "-q", "-r", str(BASE / "requirements.txt")])
    if r.returncode != 0:
        sys.exit("pip install failed — check your internet connection and re-run.")


def ensure_env_file() -> None:
    env, example = BASE / ".env", BASE / ".env.example"
    if not env.exists() and example.exists():
        shutil.copy(example, env)
        print("Created .env from .env.example — add your API key there, or set it later in the app's ⚙ Settings.")


def open_browser_soon() -> None:
    threading.Timer(1.5, lambda: webbrowser.open(URL)).start()


def main() -> None:
    if sys.version_info < (3, 9):
        sys.exit(f"Python 3.9+ required, you have {sys.version.split()[0]}. "
                 "Install a newer Python from https://www.python.org/downloads/")
    os.chdir(BASE)
    py = ensure_venv()
    install_requirements(py)
    ensure_env_file()
    print(f"\nStarting Vertex Chat on {URL}  (Ctrl+C to stop)\n")
    open_browser_soon()
    try:
        subprocess.call([str(py), "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", PORT])
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
