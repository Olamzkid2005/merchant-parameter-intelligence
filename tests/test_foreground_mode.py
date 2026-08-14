"""
test_foreground_mode.py — end-to-end test for the --foreground/--log-follow
mode in app.start.

Verifies that when launched with --foreground on CUSTOM ports, both services
come up healthy AND their logs stream to the console with [API]/[WEB] prefixes.

Run:  .venv\\Scripts\\python.exe test_foreground_mode.py
"""
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
API_PORT = 8098
WEB_PORT = 5198


def http_ok(url, timeout=2.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    # 1. Launch in foreground mode on custom ports (backgrounded for the test)
    print("[1] launching with --foreground on :%d / :%d ..." % (API_PORT, WEB_PORT))
    launcher = subprocess.Popen(
        [str(PY), "app.start", "app", "--foreground",
         "--api-port", str(API_PORT), "--web-port", str(WEB_PORT)],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    out_chunks = []
    def _pump():
        for line in launcher.stdout:
            out_chunks.append(line)
    t = threading.Thread(target=_pump, daemon=True)
    t.start()

    try:
        # 2. Poll both health endpoints until ready
        deadline = time.time() + 120
        api_ok = web_ok = False
        while time.time() < deadline:
            api_ok = http_ok(f"http://127.0.0.1:{API_PORT}/api/health")
            web_ok = http_ok(f"http://127.0.0.1:{WEB_PORT}/")
            if api_ok and web_ok:
                break
            if launcher.poll() is not None:
                break
            time.sleep(1)
        assert api_ok, "API did not become healthy"
        assert web_ok, "Web did not become healthy"
        print("[2] both health checks pass")

        # 3. Give the log streams a moment, then confirm [API]/[WEB] prefixes
        time.sleep(4)
        out = "".join(out_chunks)
        assert "[API]" in out, "no [API]-prefixed log lines streamed"
        assert "[WEB]" in out, "no [WEB]-prefixed log lines streamed"
        print("[3] log streaming confirmed ([API] and [WEB] lines present)")

        # 4. Stop and confirm ports released
        subprocess.run([str(PY), "app.start", "stop",
                        "--api-port", str(API_PORT), "--web-port", str(WEB_PORT)],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        time.sleep(2)
        assert not http_ok(f"http://127.0.0.1:{API_PORT}/api/health"), "API still up"
        assert not http_ok(f"http://127.0.0.1:{WEB_PORT}/"), "Web still up"
        print("[4] stop worked - ports released")
    finally:
        if launcher.poll() is None:  # safety net
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(launcher.pid)],
                           capture_output=True, text=True)

    print("\nALL FOREGROUND TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nTEST FAILED {e}")
        sys.exit(1)
