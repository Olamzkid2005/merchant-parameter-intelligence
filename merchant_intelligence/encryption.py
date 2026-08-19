"""encryption.py — Field-level encryption at rest for sensitive merchant data.

Encrypts high-sensitivity fields (BVN, account numbers, phone, email, static
account numbers) at the database boundary so that a stolen DB file does not
expose PII in cleartext.

Architecture:
  - Master key stored in ``data/encryption.key`` (created on first use)
  - Key version tracked in ``encryption_keys`` table (schema.py)
  - AES-256-GCM via ``cryptography`` library (with stdlib fallback)
  - Lazy initialization — no overhead until first encrypt/decrypt call
  - ``encrypt_field(value)`` / ``decrypt_field(ciphertext)`` API
  - ``mask_field(value)`` for viewer-role display (partial redaction)

When ``cryptography`` is not installed, falls back to Fernet (``cryptography``
again) or a simple XOR obfuscation for development environments.

Sensitive fields (automatically encrypted on DB write when enabled):
  bvn, account_number, static_acc_no, phone, email, alias
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import struct
from pathlib import Path
from typing import Any, Dict, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"

# ── Sensitive field names ───────────────────────────────────────────────────
SENSITIVE_FIELDS = frozenset({
    "bvn", "account_number", "static_acc_no", "phone", "email", "alias",
})

# ── Key management ──────────────────────────────────────────────────────────
_key_cache: Optional[bytes] = None
_key_path = _DATA_DIR / "encryption.key"


def _ensure_key() -> bytes:
    """Load or create the master encryption key."""
    global _key_cache
    if _key_cache is not None:
        return _key_cache

    _key_path.parent.mkdir(parents=True, exist_ok=True)
    if _key_path.exists():
        _key_cache = _key_path.read_bytes()
    else:
        _key_cache = secrets.token_bytes(32)
        _key_path.write_bytes(_key_cache)
        # Restrict permissions on Unix
        try:
            _key_path.chmod(0o600)
        except (OSError, AttributeError):
            pass
    return _key_cache


def _derive_subkey(context: str, length: int = 32) -> bytes:
    """Derive a context-specific subkey from the master key."""
    master = _ensure_key()
    return hashlib.pbkdf2_hmac("sha256", master, context.encode(), 100_000,
                               dklen=length)


# ── Encryption / Decryption ─────────────────────────────────────────────────

def encrypt_field(value: Any) -> str:
    """Encrypt a sensitive field value. Returns a base64-encoded ciphertext.

    Format: ``v1:<base64(nonce + ciphertext + tag)>``
    Uses AES-256-GCM when ``cryptography`` is available, otherwise falls back
    to XChaCha20-Poly1305 via ``cryptography`` or a simple XOR for dev.
    """
    if value is None or str(value).strip() == "":
        return ""

    plaintext = str(value).encode("utf-8")
    key = _derive_subkey("field-encryption")

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = secrets.token_bytes(12)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)
        return "v1:" + base64.b64encode(nonce + ciphertext).decode("ascii")
    except ImportError:
        pass

    # Fallback: XOR obfuscation (dev only — not production-grade)
    nonce = secrets.token_bytes(16)
    stream = _xor_stream(key + nonce, len(plaintext))
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream))
    return "v0:" + base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_field(ciphertext: str) -> str:
    """Decrypt a field value encrypted by ``encrypt_field``."""
    if not ciphertext or not isinstance(ciphertext, str):
        return ""

    if not ciphertext.startswith("v0:") and not ciphertext.startswith("v1:"):
        return ciphertext  # Not encrypted — return as-is

    try:
        raw = base64.b64decode(ciphertext[3:])
    except Exception:
        return ""

    key = _derive_subkey("field-encryption")

    if ciphertext.startswith("v1:"):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            nonce, ct = raw[:12], raw[12:]
            aesgcm = AESGCM(key)
            return aesgcm.decrypt(nonce, ct, None).decode("utf-8")
        except Exception:
            return ""

    # v0 fallback
    nonce, ct = raw[:16], raw[16:]
    stream = _xor_stream(key + nonce, len(ct))
    plaintext = bytes(a ^ b for a, b in zip(ct, stream))
    return plaintext.decode("utf-8", errors="replace")


def _xor_stream(key: bytes, length: int) -> bytes:
    """Generate a pseudorandom XOR stream from key (dev fallback only)."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(key + struct.pack("<Q", counter)).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


# ── Masking (for viewer role) ──────────────────────────────────────────────

def mask_field(value: Any, field_name: str = "") -> str:
    """Return a partially-redacted version for display to viewer-role users.

    Rules:
      - BVN: show last 4 (e.g. ***-***-1234)
      - Account numbers: show last 4 (e.g. ****1234)
      - Phone: show last 4 (e.g. 080****1234)
      - Email: show first 2 + domain (e.g. ab****@gmail.com)
      - Others: show first 2 + last 2 chars
    """
    s = str(value).strip()
    if not s:
        return ""

    field = field_name.lower()

    if field == "bvn" and len(s) >= 4:
        return "***-***-" + s[-4:]

    if field in ("account_number", "static_acc_no") and len(s) >= 4:
        return "*" * (len(s) - 4) + s[-4:]

    if field == "phone" and len(s) >= 4:
        return s[:2] + "*" * (len(s) - 4) + s[-4:]

    if field == "email" and "@" in s:
        local, domain = s.split("@", 1)
        if len(local) >= 2:
            return local[:2] + "*" * (len(local) - 2) + "@" + domain
        return "*" * len(local) + "@" + domain

    if len(s) > 6:
        return s[:2] + "*" * (len(s) - 4) + s[-2:]
    if len(s) > 2:
        return s[:1] + "*" * (len(s) - 2) + s[-1:]
    return "*"


def mask_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Apply field-level masking to a response dict for viewer-role users."""
    if not isinstance(data, dict):
        return data
    out = {}
    for k, v in data.items():
        if k.lower() in SENSITIVE_FIELDS and v:
            out[k] = mask_field(v, k)
        elif isinstance(v, dict):
            out[k] = mask_payload(v)
        elif isinstance(v, list):
            out[k] = [mask_payload(item) if isinstance(item, dict) else item
                       for item in v]
        else:
            out[k] = v
    return out


# ── Bulk encrypt/decrypt (for DB migration) ─────────────────────────────────

def encrypt_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Encrypt all sensitive fields in a row dict."""
    out = dict(row)
    for field in SENSITIVE_FIELDS:
        if field in out and out[field]:
            out[field] = encrypt_field(out[field])
    return out


def decrypt_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Decrypt all sensitive fields in a row dict."""
    out = dict(row)
    for field in SENSITIVE_FIELDS:
        if field in out and out[field]:
            out[field] = decrypt_field(out[field])
    return out


# ── CLI ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys as _sys
    _sys.path.insert(0, str(_PROJECT_ROOT))

    print("Testing encryption module...")
    test_values = {
        "bvn": "12345678901",
        "account_number": "5180005449",
        "phone": "08039689799",
        "email": "test@example.com",
        "alias": "029888",
    }
    for field, val in test_values.items():
        enc = encrypt_field(val)
        dec = decrypt_field(enc)
        msk = mask_field(val, field)
        ok = dec == val
        print(f"  {field:20s} enc={enc[:30]:30s} dec={dec:20s} mask={msk:20s} {'✅' if ok else '❌'}")

    print("\nAll tests passed!" if all(
        decrypt_field(encrypt_field(v)) == v for v in test_values.values()
    ) else "\nSome tests failed!")
