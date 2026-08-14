"""
test_app_start.py — end-to-end test for the `app.start` launcher.

Starts the full stack on CUSTOM ports (so it never collides with a dev
instance on 8000/5173), verifies both services become healthy, then stops
them and confirms the ports are released.

Run:  .venv\\Scripts\\python.exe test_app_start.py
"""
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
API_PORT = 8099
WEB_PORT = 5199


def http_ok(url, timeout=2.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def run_launcher(*args, timeout=120):
    return subprocess.run([str(PY), "app.start", *args], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=timeout)


def main():
    # 1. Syntax check
    import py_compile
    py_compile.compile(str(ROOT / "app.start"), doraise=True)
    print("[1] app.start compiles OK")

    # 2. status works (services on custom ports are down)
    r = run_launcher("status", "--api-port", str(API_PORT),
                     "--web-port", str(WEB_PORT))
    assert r.returncode == 0, r.stdout + r.stderr
    print("[2] status ran OK")

    # 3. Launch in the BACKGROUND (launch() stays attached in an infinite
    #    loop, so we cannot use subprocess.run here — it would block forever).
    print("[3] launching backend + frontend on :%d / :%d ..." % (API_PORT, WEB_PORT))
    launcher = subprocess.Popen(
        [str(PY), "app.start", "app", "--api-port", str(API_PORT),
         "--web-port", str(WEB_PORT)],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        # Read the launcher's output on a side channel so its stdout pipe
        # never fills up and blocks the child.
        import threading
        out_chunks = []
        def _pump():
            for line in launcher.stdout:
                out_chunks.append(line)
        t = threading.Thread(target=_pump, daemon=True)
        t.start()

        # 4. Poll both health endpoints until ready (up to 120s)
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

        print("".join(out_chunks))
        assert api_ok, "API did not become healthy"
        assert web_ok, "Web did not become healthy"
        assert launcher.poll() is None, "launcher exited before services were healthy"
        print("[4] both health checks pass")

        # 5. Stop and confirm ports released
        run_launcher("stop", "--api-port", str(API_PORT),
                     "--web-port", str(WEB_PORT))
        time.sleep(2)
        assert not http_ok(f"http://127.0.0.1:{API_PORT}/api/health"), "API still up"
        assert not http_ok(f"http://127.0.0.1:{WEB_PORT}/"), "Web still up"
        print("[5] stop worked - ports released")

        # The launcher's watchdog notices the dead children and exits on its own.
        try:
            launcher.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
    finally:
        if launcher.poll() is None:  # safety net
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(launcher.pid)],
                           capture_output=True, text=True)

    print("\nALL TESTS PASSED OK")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nTEST FAILED {e}")
        sys.exit(1)
