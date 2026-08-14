"""Check which Python packages are available for the next-level build."""
import importlib.util

mods = [
    "streamlit", "rapidfuzz", "jellyfish", "pandas", "openpyxl", "numpy",
    "phonetics", "fuzzywuzzy", "flask", "fastapi", "uvicorn",
]

print("Package availability:")
for m in mods:
    found = importlib.util.find_spec(m) is not None
    print(f"  {m:<15} {'OK' if found else 'MISSING'}")
