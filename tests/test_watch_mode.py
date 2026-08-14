"""Test build_intelligence_db.py --watch using a tiny temp folder (fast)."""
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"

TMP = ROOT / "_watch_test_dir"
OUT = TMP / "intel.db"
LOG = ROOT / "_watch_test.log"

# Fresh temp folder with one small Excel file
if TMP.exists():
    shutil.rmtree(TMP)
TMP.mkdir(parents=True)
pd.DataFrame({"merchant_name": ["ALPHA TEST COMPANY"], "email": ["alpha@test.com"]}).to_excel(
    TMP / "alpha.xlsx", index=False
)

print("Starting watch mode on tiny folder...")
proc = subprocess.Popen(
    [str(PY), "scripts/build_intelligence_db.py", "--watch", "--interval", "1",
     "--no-verify", "--out", str(OUT), "--folder", str(TMP)],
    cwd=str(ROOT), stdout=LOG.open("w"), stderr=subprocess.STDOUT,
)
print(f"Watch process pid={proc.pid}")

def log_text():
    return LOG.read_text(encoding="utf-8", errors="ignore") if LOG.exists() else ""

def wait_for(pred, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred(log_text()):
            return True
        time.sleep(1)
    return False

results = {}
try:
    results["initial_build"] = wait_for(lambda t: "intelligence.db built" in t)
    print("Initial build done:", results["initial_build"])

    # Create a second Excel file — must trigger a rebuild
    pd.DataFrame({"merchant_name": ["BETA PROBE MERCHANT"], "email": ["beta@test.com"]}).to_excel(
        TMP / "beta.xlsx", index=False
    )
    results["change_detected"] = wait_for(lambda t: "Change detected" in t)
    print("Change detected:", results["change_detected"])

    # Rebuild must complete (two 'built' markers total)
    results["rebuild_done"] = wait_for(lambda t: t.count("intelligence.db built") >= 2)
    print("Rebuild done:", results["rebuild_done"])

    # Verify both merchants are in the rebuilt DB
    time.sleep(1)
    conn = sqlite3.connect(str(OUT))
    alpha = conn.execute(
        "SELECT COUNT(*) FROM merchants WHERE merchant_name LIKE '%ALPHA TEST%'").fetchone()[0]
    beta = conn.execute(
        "SELECT COUNT(*) FROM merchants WHERE merchant_name LIKE '%BETA PROBE%'").fetchone()[0]
    conn.close()
    results["alpha_in_db"] = alpha > 0
    results["beta_in_db"] = beta > 0
    print("Alpha in DB:", results["alpha_in_db"], "| Beta in DB:", results["beta_in_db"])
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    # Give the child a moment to release its log handle before cleanup.
    time.sleep(1)

print("\n===== LOG TAIL =====")
print("\n".join(log_text().splitlines()[-30:]))

# Cleanup (retry: the terminated child may still hold the log handle)
shutil.rmtree(TMP, ignore_errors=True)
for _ in range(5):
    try:
        if LOG.exists():
            LOG.unlink()
        break
    except PermissionError:
        time.sleep(1)

ok = all(results.get(k) for k in
        ("initial_build", "change_detected", "rebuild_done", "alpha_in_db", "beta_in_db"))
print("\nRESULT:", "PASS" if ok else "FAIL", {k: v for k, v in results.items()})
