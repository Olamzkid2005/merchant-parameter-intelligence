"""
start_api.py — start the FastAPI backend as a detached background process.

Usage:  python start_api.py [--port 8000]
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

port = "8000"
if "--port" in sys.argv:
    port = sys.argv[sys.argv.index("--port") + 1]

flags = 0
if sys.platform == "win32":
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

log = open(LOG_DIR / "_api.log", "w")
proc = subprocess.Popen(
    [PY, "-m", "uvicorn", "api:app", "--host", "127.0.0.1", "--port", port],
    cwd=str(ROOT),
    stdout=log,
    stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL,
    creationflags=flags,
)
print(f"API started (pid={proc.pid}) on http://127.0.0.1:{port}  ->  logs/_api.log")
