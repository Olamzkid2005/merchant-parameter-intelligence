"""test_api_split.py — hermetic regression checks for the roadmap #3
api.py router split.

Locks in three invariants so a future refactor cannot silently drop or
rename an endpoint:

  1. Every legacy handler/model name still resolves through `import api`
     (tests + frontend depend on the re-exports).
  2. The OpenAPI path set exactly matches the pre-split api.py route set
     (55 unique paths — verified against git HEAD at split time).
  3. Handlers moved into api_routes/ modules are importable from where they
     now live (catches the "moved but forgot the import" class of bug).
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

passed = 0
failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [OK] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {extra}")


print("[1] legacy names still resolve through api module")
import api

legacy = [
    # handlers
    "search", "health", "autocomplete", "suggest", "similar", "duplicates",
    "aliases", "alias_approve", "alias_reject", "entity", "idclass_debug",
    "search_export", "profile", "timeline", "compare", "stats", "report",
    "report_export", "learn", "quickmatch", "quickmatch_export", "task",
    "task_export", "task_analyze", "feedback_suggestions", "suggestion_apply",
    "suggestion_reject", "get_synonym_candidates", "propose_synonyms",
    "synonym_status", "apply_synonyms", "synonym_manifest", "shadow_review",
    "shadow_review_label", "audit_endpoint", "ingest_endpoint",
    "get_calibration", "reset_calibration", "get_preferences",
    "forget_preference", "get_intents", "update_intent", "get_settings",
    "update_settings", "reset_settings", "batch", "batch_export", "quality",
    "quality_export", "reconcile_endpoint", "reconcile_export", "brief",
    "selfimprove_status", "auth_login", "auth_logout", "auth_me",
    "auth_config", "auth_save_config", "auth_add_user", "auth_remove_user",
    "auth_reset_password",
    # models
    "SearchRequest", "BatchRequest", "LearnRequest", "EntityRequest",
    "QuickMatchRequest", "ProfileRequest", "CompareRequest", "TaskRequest",
    "AliasAction", "LoginRequest", "AuthConfigRequest", "AuthUserRequest",
    "AuthPasswordRequest", "TimelineRequest", "SuggestionAction",
    "SynonymStatusRequest", "SynonymApplyRequest", "ShadowReviewLabelRequest",
    "PreferenceForgetRequest", "IntentPattern", "IntentUpdateRequest",
    "SettingsUpdateRequest",
]
missing = [n for n in legacy if not hasattr(api, n)]
check(f"all {len(legacy)} legacy names resolve", not missing,
      f"missing: {missing}")

print("[2] legacy /api surface matches the pre-split route set (55 unique)")
# Baseline = the pre-split monolith — the most recent commit whose api.py
# still carried @app decorators (HEAD~1 is only valid right after the split;
# as commits move on the baseline must be walked back to the last monolith).
last = subprocess.run(["git", "log", "--format=%H", "-n", "50"],
                      capture_output=True, text=True).stdout.split()
old_src = ""
for commit in last:
    src = subprocess.run(["git", "show", f"{commit}:api.py"],
                         capture_output=True, text=True).stdout
    if re.search(r'@app\.(?:get|post|put|delete|patch)\("', src):
        old_src = src
        break
old_paths = set(re.findall(r'@app\.(?:get|post|put|delete|patch)\("([^"]+)"',
                           old_src))
check("pre-split unique paths == 55", len(old_paths) == 55, repr(len(old_paths)))
# Deliberate additions after the split (new features, not regressions): each
# one bumps the 55-path baseline below and is excluded from the added-check.
DELIBERATE_ADDITIONS = {"/api/copilot"}
all_paths = set(api.app.openapi()["paths"].keys())
legacy = {p for p in all_paths if p.startswith("/api/") and not p.startswith("/api/v1/")}
expected = 55 + len(DELIBERATE_ADDITIONS)
check(f"legacy surface unchanged ({expected} paths)", len(legacy) == expected,
      repr(len(legacy)))
check("no legacy paths dropped", not (old_paths - legacy),
      f"dropped: {sorted(old_paths - legacy)}")
undocumented = (legacy - old_paths) - DELIBERATE_ADDITIONS
check("no undocumented legacy paths added", not undocumented,
      f"added: {sorted(undocumented)}")

print("[2b] /api/v1 mirror exists for every legacy path (roadmap #3 slice 2)")
v1 = {p for p in all_paths if p.startswith("/api/v1/")}
check(f"v1 has {expected} paths", len(v1) == expected, repr(len(v1)))
legacy_tails = {p[len("/api"):] for p in legacy}
v1_tails = {p[len("/api/v1"):] for p in v1}
check("v1 mirrors legacy exactly", not (legacy_tails - v1_tails) and not (v1_tails - legacy_tails),
      f"diff: {sorted(legacy_tails ^ v1_tails)}")

print("[3] router modules are importable (no moved-but-unimportable handlers)")
router_names = [
    "auth_routes", "profile_routes", "search_routes", "tasks_routes",
    "admin_routes",
]
for rn in router_names:
    try:
        __import__(f"api_routes.{rn}")
        check(f"api_routes.{rn} imports cleanly", True)
    except Exception as exc:  # noqa: BLE001
        check(f"api_routes.{rn} imports cleanly", False, repr(exc))

print("[4] every legacy handler is callable (not None)")
for n in ("search", "task", "batch", "autocomplete", "health",
          "reconcile_export", "task_export"):
    check(f"{n} is callable", callable(getattr(api, n, None)))

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
