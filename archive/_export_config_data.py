"""One-off: export the current alias + compound-word lists to external JSON.

Run once (python _export_config_data.py) so data/manual_aliases.json and
data/known_compounds.json become the editable source of truth. Afterwards you
can teach the engine by editing the JSON files — no Python changes needed.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merchant_intelligence import config

DATA_DIR = config.DATA_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)

aliases_path = DATA_DIR / "manual_aliases.json"
with aliases_path.open("w", encoding="utf-8") as f:
    json.dump(config.MANUAL_ALIASES, f, indent=2, ensure_ascii=False)
print(f"Wrote {aliases_path} ({len(config.MANUAL_ALIASES)} alias sets)")

compounds_path = DATA_DIR / "known_compounds.json"
with compounds_path.open("w", encoding="utf-8") as f:
    json.dump({
        "prefixes": sorted(config.KNOWN_PREFIXES),
        "suffixes": sorted(config.KNOWN_SUFFIXES),
    }, f, indent=2, ensure_ascii=False)
print(f"Wrote {compounds_path} "
      f"({len(config.KNOWN_PREFIXES)} prefixes, {len(config.KNOWN_SUFFIXES)} suffixes)")
