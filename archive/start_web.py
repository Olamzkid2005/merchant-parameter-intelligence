"""
start_web.py — start the Vite dev server as a detached background process.

Usage:  python start_web.py [--port 5173]
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

port = "5173"
if "--port" in sys.argv:
    port = sys.argv[sys.argv.index("--port") + 1]

flags = 0
if sys.platform == "win32":
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

log = open(LOG_DIR / "_web.log", "w")
proc = subprocess.Popen(
    ["npm.cmd", "run", "dev", "--", "--port", port, "--host", "127.0.0.1"],
    cwd=str(WEB),
    stdout=log,
    stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL,
    creationflags=flags,
)
print(f"Vite started (pid={proc.pid}) on http://127.0.0.1:{port}  ->  logs/_web.log")
