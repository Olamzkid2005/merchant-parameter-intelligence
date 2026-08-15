"""
export_embedding_model.py — Ship the Tier-2 ONNX encoder artifact.

Phase 0 deliverable of the hybrid semantic intent layer (docs/hybrid-semantic-
intent-layer.md, §9): downloads the pre-exported ONNX sentence-transformer
(model.onnx + tokenizer.json + config.json) from Hugging Face Hub into
data/models/<model_id>/, verifies it runs (probe encode through onnxruntime),
and writes a provenance manifest. `ONNXEncoder` in semantic.py loads this
artifact; without it the tier uses the pure-Python HashingEncoder.

No torch, no sentence-transformers: we pull the already-exported ONNX weights
and run them with onnxruntime + the fast tokenizers library, so the only new
deps are light. Phase-0 default model: all-MiniLM-L6-v2 (~80MB, Apache 2.0).

Outputs (gitignored data/):
    data/models/<model_id>/model.onnx
    data/models/<model_id>/tokenizer.json
    data/models/<model_id>/config.json
    data/models/<model_id>/manifest.json

Usage:
    python scripts/export_embedding_model.py             # default model
    python scripts/export_embedding_model.py --model bge-small-en-v1.5
    python scripts/export_embedding_model.py --force    # redownload

Exit codes: 0 ok, 2 missing huggingface_hub, 3 download/verify failure.
"""
import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

# Windows cp1252 consoles can't encode the ⬇/✅/✗ markers — force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from merchant_intelligence import config

# Repo -> ONNX file candidates. The sentence-transformers org publishes an
# `onnx/` subfolder; some repos keep model.onnx at the root. First candidate
# whose files all download wins.
SOURCES = {
    "all-MiniLM-L6-v2": [
        ("sentence-transformers/all-MiniLM-L6-v2",
         ["onnx/model.onnx", "tokenizer.json", "config.json"]),
        ("sentence-transformers/all-MiniLM-L6-v2",
         ["model.onnx", "tokenizer.json", "config.json"]),
    ],
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch(repo_id: str, filenames, dest: Path) -> bool:
    """Download all `filenames` flat into `dest` (already created). True on
    success; on any failure the partial download is removed."""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("  ✗ huggingface_hub not installed — run: "
              ".venv/Scripts/python.exe -m pip install huggingface_hub")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        try:
            for name in filenames:
                hf_hub_download(repo_id, name,
                                local_dir=str(tmpd / "hub"))
        except Exception as exc:
            print(f"  ✗ download failed for {repo_id} {filenames[:1]}: {exc}")
            return False
        # Flatten: onnx/model.onnx -> model.onnx (ONNXEncoder expects the
        # model at the dir root).
        for name in filenames:
            src = tmpd / "hub" / name
            if not src.exists():
                print(f"  ✗ {name} missing after download")
                return False
            shutil.copy2(src, dest / Path(name).name)
    return True


def _verify(dest: Path, model_id: str) -> int:
    """Probe-encode one sentence through onnxruntime; returns vector dim."""
    import onnxruntime as ort
    from tokenizers import Tokenizer
    session = ort.InferenceSession(
        str(dest / "model.onnx"), providers=["CPUExecutionProvider"])
    tok = Tokenizer.from_file(str(dest / "tokenizer.json"))
    enc = tok.encode("verify the merchant static account")
    import numpy as np
    feeds = {
        "input_ids": np.array([enc.ids], dtype=np.int64),
        "attention_mask": np.array([enc.attention_mask], dtype=np.int64),
    }
    # sentence-transformers ONNX exports also take token_type_ids.
    if hasattr(enc, "type_ids") and enc.type_ids:
        feeds["token_type_ids"] = np.array([enc.type_ids], dtype=np.int64)
    out = session.run(None, feeds)[0]
    return out.shape[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="all-MiniLM-L6-v2")
    ap.add_argument("--force", action="store_true",
                    help="redownload even if the artifact exists")
    args = ap.parse_args()

    model_id = args.model
    if model_id not in SOURCES:
        print(f"  ✗ unknown model {model_id!r}; supported: {sorted(SOURCES)}")
        return 3
    try:
        import huggingface_hub  # noqa: F401
    except ImportError:
        print("  ✗ huggingface_hub not installed — run: "
              ".venv/Scripts/python.exe -m pip install huggingface_hub onnxruntime tokenizers")
        return 2

    dest = config.DATA_DIR / "models" / model_id
    manifest_path = dest / "manifest.json"
    if dest.exists() and manifest_path.exists() and not args.force:
        print(f"  ✓ artifact already present: {dest}")
        print("    (use --force to redownload)")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    print(f"  ⬇  downloading {model_id} from Hugging Face Hub …")
    ok = False
    for repo_id, filenames in SOURCES[model_id]:
        print(f"    repo {repo_id} {filenames[0]}")
        if _fetch(repo_id, filenames, dest):
            ok = True
            break
    if not ok:
        shutil.rmtree(dest, ignore_errors=True)
        print("  ✗ could not fetch the model — check the network and retry")
        return 3

    try:
        dim = _verify(dest, model_id)
    except Exception as exc:
        shutil.rmtree(dest, ignore_errors=True)
        print(f"  ✗ model downloaded but probe encode failed: {exc}")
        return 3

    manifest = {
        "model_id": model_id,
        "source_repos": [r for r, _ in SOURCES[model_id]],
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "vector_dim": dim,
        "files": {p: _sha256(dest / p)
                  for p in ("model.onnx", "tokenizer.json", "config.json")},
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print(f"  ✅ {model_id} ready ({dim}d) -> {dest}")
    print("    restart the app; MERCHANT_TIER2_ENCODER defaults to auto "
          "(ONNX when present).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
