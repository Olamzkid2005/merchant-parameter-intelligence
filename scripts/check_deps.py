"""Check which Python packages are available for the next-level build."""
import importlib.util

mods = [
    "streamlit", "rapidfuzz", "jellyfish", "pandas", "openpyxl", "numpy",
    "phonetics", "fuzzywuzzy", "flask", "fastapi", "uvicorn",
]

# Optional: Tier-2 semantic encoder (hybrid semantic intent layer). Without
# them the tier runs on the pure-Python HashingEncoder — the app works, the
# encoder is just weaker. Install: python -m pip install onnxruntime \
#     huggingface_hub tokenizers && python scripts/export_embedding_model.py
OPTIONAL_TIER2 = ["onnxruntime", "huggingface_hub", "tokenizers"]

print("Package availability:")
for m in mods:
    found = importlib.util.find_spec(m) is not None
    print(f"  {m:<15} {'OK' if found else 'MISSING'}")
print("\nOptional (Tier-2 semantic encoder):")
for m in OPTIONAL_TIER2:
    found = importlib.util.find_spec(m) is not None
    print(f"  {m:<15} {'OK' if found else 'MISSING'}")
