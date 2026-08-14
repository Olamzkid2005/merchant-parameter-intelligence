"""
start_app.py — Launch the Merchant Intelligence web UI.

Uses the project virtual environment (global Streamlit is corrupted on this
machine). Start it detached so it keeps running after this script exits.

Usage:
    python start_app.py            # launch + print URL
    python start_app.py --stop     # stop a previously launched instance

The server runs at http://localhost:8501
"""
import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
PORT = 8501
URL = f"http://localhost:{PORT}"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
PID_FILE = LOG_DIR / ".app.pid"


def is_alive() -> bool:
    try:
        with urllib.request.urlopen(f"{URL}/_stcore/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def stop():
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 9)
            print(f"Stopped app (PID {pid}).")
        except Exception as e:
            print(f"Could not stop PID: {e}")
        PID_FILE.unlink(missing_ok=True)
    if is_alive():
        print("Note: an app instance is still responding on port 8501 but was not "
              "tracked by this launcher — stop it from Task Manager if needed.")


def launch():
    if is_alive():
        print(f"App already running at {URL}")
        _capture_listener_pid()
        return
    if not VENV_PY.exists():
        print(f"ERROR: venv not found at {VENV_PY}\n"
              f"Create it first:\n"
              f"  python -m venv .venv\n"
              f"  .venv\\Scripts\\python.exe -m pip install "
              f"streamlit pandas openpyxl rapidfuzz jellyfish")
        sys.exit(1)

    # Spawn via PowerShell Start-Process: proven to survive in this
    # environment, unlike subprocess.Popen(DETACHED_PROCESS).
    # NOTE: do NOT use -RedirectStandardOutput/-RedirectStandardError here —
    # they make the parent PowerShell block until the child exits. Instead the
    # child runs hidden (output goes to its hidden console) and PowerShell
    # writes the PID to .app.pid via Set-Content.
    args = ("-m streamlit run archive/app.py "
            "--server.headless=true "
            f"--server.port {PORT} "
            "--browser.gatherUsageStats=false")
    ps_cmd = (f"$p = Start-Process -FilePath '{VENV_PY}' "
              f"-ArgumentList '{args}' "
              f"-WorkingDirectory '{ROOT}' "
              "-WindowStyle Hidden -PassThru; "
              f"Set-Content -Path '{PID_FILE}' -Value $p.Id")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       stdin=subprocess.DEVNULL, timeout=30)
    except Exception as e:
        print(f"Warning: launcher PowerShell call failed ({e}) — "
              "continuing to poll for the app anyway")
    _capture_listener_pid()

    for _ in range(30):
        time.sleep(1)
        if is_alive():
            print(f"READY  {URL}")
            if PID_FILE.exists():
                print(f"PID    {PID_FILE.read_text().strip()}  "
                      f"(stop with: python start_app.py --stop)")
            return
    print("App did not become ready — check Task Manager or the port")


def _capture_listener_pid():
    """Record the PID currently listening on PORT so --stop works even when
    the app was started outside this launcher. Best-effort; no-op on failure."""
    try:
        ps = ("powershell -NoProfile -Command "
              f"\"$c = Get-NetTCPConnection -LocalPort {PORT} -State Listen "
              "-ErrorAction SilentlyContinue; "
              "if ($c) { $c.OwningProcess } else { '' }\"")
        r = subprocess.run(ps, shell=True, capture_output=True, text=True,
                           timeout=30)
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                PID_FILE.write_text(line)
                return int(line)
    except Exception:
        pass
    return None


def main():
    parser = argparse.ArgumentParser(description="Launch Merchant Intelligence UI")
    parser.add_argument("--launch", action="store_true", help="Launch the app (default)")
    parser.add_argument("--stop", action="store_true", help="Stop the running app")
    args = parser.parse_args()
    if args.stop:
        stop()
    else:
        launch()


if __name__ == "__main__":
    main()
