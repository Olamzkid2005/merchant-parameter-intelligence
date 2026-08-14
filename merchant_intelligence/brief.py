"""
brief.py — LLM Investigation Brief (feature #6).

Turns the merchant 360° profile JSON into a natural-language investigation
dossier, the way a fraud/onboarding analyst would write it:

    \"MARYLAND MALL LIMITED REVENUE COLLECTION ACCOUNT appears under 2 name
     variants across 1 source. It has 1 confirmed email (merchant21@example.com)
     shared by 6 records...\"

Two modes:
  1. LLM mode — calls an OpenAI-compatible /chat/completions endpoint when
     configured (env vars below). Produces the richest, most readable brief.
  2. Template mode — fully offline, deterministic natural-language summary
     built from the profile structure. Used when no LLM key is configured,
     when the call fails, or when the response is unusable.

Env vars (all optional):
  LLM_API_KEY     — API key (enables LLM mode)
  LLM_BASE_URL    — OpenAI-compatible base, default https://api.openai.com/v1
  LLM_MODEL       — model name, default gpt-4o-mini
  LLM_TIMEOUT     — seconds, default 45

Note: env vars are read at import time — after setting LLM_API_KEY,
restart the API process (or run with the var set) for LLM mode to engage.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections import Counter

logger = logging.getLogger(__name__)

# Configurable via env (no code edits needed to point at any OpenAI-compatible
# provider: OpenRouter, Groq, Together, local vLLM/Ollama, etc.)
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "45"))

_SYSTEM_PROMPT = (
    "You are a senior merchant-operations and fraud investigator. "
    "Write a concise natural-language investigation brief (4-8 short "
    "paragraphs) about the merchant described by the JSON profile below. "
    "Cover: identity (which merchant this is), all confirmed contact details, "
    "every name variant and source file/sheet, the number of linked records, "
    "any data-quality red flags (missing email, many name variants, "
    "identifiers shared across many rows), and the alias candidates worth "
    "teaching the engine. Be factual — only state what the data shows. "
    "Do not invent emails, phones, or relationships.\n\n"
    "PROFILE JSON:\n"
)


def llm_available() -> bool:
    """True when an LLM endpoint is configured (key + base URL)."""
    return bool(LLM_API_KEY)


def _call_llm(profile: dict, context: str) -> str:
    """Call the OpenAI-compatible chat completions endpoint. Raises on failure."""
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT + context},
            {"role": "user", "content": json.dumps(profile, default=str)[:9000]},
        ],
        "temperature": 0.3,
        "max_tokens": 800,
    }
    req = urllib.request.Request(
        f"{LLM_BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_API_KEY}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
    text = (text or "").strip()
    if not text:
        raise ValueError("LLM returned empty content")
    return text


# ── Template mode (offline fallback) ──────────────────────────────────────

def _identity_lines(profile: dict) -> list:
    """Short factual lines per identity field, e.g. 'Emails (2): a@b.com, c@d.com'."""
    lines = []
    identity = profile.get("identity") or {}
    order = ["email", "phone", "tid", "mxcode", "payable_code",
             "merchant_id", "account_number", "account_name", "contact_name"]
    for field in order:
        info = identity.get(field)
        if not info or not info.get("values"):
            continue
        vals = [x for v in info["values"][:5]
                if (x := v.get("value") or v.get("canonical"))]
        label = info.get("label", field)
        extra = f" (+{info['total'] - len(vals)} more)" if info["total"] > len(vals) else ""
        lines.append(f"{label} ({info['total']}): {', '.join(vals)}{extra}")
    return lines


def _red_flags(profile: dict) -> list:
    """Data-quality red flags derived from the profile structure."""
    flags = []
    identity = profile.get("identity") or {}
    emails = identity.get("email")
    seed = profile.get("seed") or {}
    if not emails or not emails.get("values"):
        flags.append("no confirmed email on any linked record")
    variants = profile.get("name_variants") or []
    if len(variants) > 1:
        flags.append(f"{len(variants)} name variants for one entity "
                     f"(possible aliases or data drift)")
    members = profile.get("members") or []
    if len(members) > 0:
        # identifier shared across many rows -> duplicated/synthetic signal
        id_fields = ("email", "phone", "tid", "mxcode", "account_number")
        counter = Counter()
        for m in members:
            for f in id_fields:
                v = str(m.get(f) or "").strip().lower()
                if v and len(v) >= 5 and v not in ("y", "n", "n/a", "na", "-"):
                    counter[(f, v)] += 1
        for (field, val), n in counter.most_common(2):
            if n >= 3:
                flags.append(f"{field} '{val}' appears on {n} linked records")
    if not seed:
        flags.append("no confident seed record")
    return flags


def build_template_brief(profile: dict) -> str:
    """Deterministic natural-language dossier from the profile structure."""
    found = bool(profile.get("found"))
    if not found:
        q = profile.get("query", "")
        return (f"No records match '{q}'. The registry has no profile for this "
                f"fragment — check the spelling or search a different identifier.")
    seed = profile.get("seed") or {}
    name = seed.get("merchant_name") or profile.get("query", "merchant")
    match_type = (seed.get("match_type") or "match").lower()
    score = round((seed.get("overall_score") or 0) / 10, 1)
    q = profile.get("query", "")
    article = "an" if match_type[:1] in "aeiou" else "a"

    para = []
    intro = (f"{name} resolves from the query '{q}' as {article} {match_type} "
             f"(confidence {score}/10).")
    if profile.get("family_count"):
        intro += (f" It links to {profile['family_count']} records in the "
                  f"registry through shared identifiers.")
    para.append(intro)

    # Identity
    id_lines = _identity_lines(profile)
    if id_lines:
        para.append("Confirmed details: " + " ".join(id_lines) + ".")

    # Name variants
    variants = profile.get("name_variants") or []
    if len(variants) > 1:
        names = ", ".join(f"'{v['name']}' (x{v['count']})"
                          for v in variants[:4])
        para.append(f"The same entity appears under {len(variants)} name "
                    f"variants: {names}.")

    # Sources
    sources = profile.get("sources") or []
    if sources:
        s = ", ".join(f"'{x['sheet']}'" for x in sources[:4])
        para.append(f"It appears in {len(sources)} source sheet(s): {s}.")

    # Red flags
    flags = _red_flags(profile)
    if flags:
        para.append("Data-quality red flags: " + "; ".join(flags) + ".")

    # Alias candidates
    candidates = profile.get("alias_candidates") or []
    if candidates:
        para.append("Alias candidates worth teaching the engine: "
                    + ", ".join(candidates[:5]) + ".")

    return "\n".join(para)


# ── Public API ────────────────────────────────────────────────────────────

def build_brief(profile: dict) -> dict:
    """Build the investigation brief for a profile dict.

    Returns:
        {
          "found": bool,
          "brief": str,           # the natural-language dossier
          "mode": "llm" | "template",
          "model": str | None,
          "elapsed_ms": int,
          "llm_error": str | None,  # set when LLM mode was attempted but failed
        }
    """
    t0 = time.perf_counter()
    context = _build_context(profile)
    mode = "template"
    model = None
    llm_error = None
    brief = build_template_brief(profile)

    if llm_available():
        try:
            brief = _call_llm(profile, context)
            mode = "llm"
            model = LLM_MODEL
        except Exception as exc:
            # Keep the template brief already computed above.
            llm_error = f"{type(exc).__name__}: {exc}"
            logger.warning("LLM brief failed, using template: %s", llm_error)

    return {
        "found": bool(profile.get("found")),
        "brief": brief,
        "mode": mode,
        "model": model,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        "llm_error": llm_error,
    }


def _build_context(profile: dict) -> str:
    """Compact factual context so the LLM prompt stays small but complete."""
    seed = profile.get("seed") or {}
    identity = profile.get("identity") or {}
    parts = []
    parts.append(f"Seed name: {seed.get('merchant_name') or profile.get('query')}")
    parts.append(f"Match type: {seed.get('match_type')} "
                 f"({round((seed.get('overall_score') or 0) / 10, 1)}/10)")
    parts.append(f"Linked records: {profile.get('family_count')}")
    for field in ("email", "phone", "tid", "mxcode", "account_number"):
        info = identity.get(field)
        if info and info.get("values"):
            vals = [v.get("value") for v in info["values"][:4]]
            parts.append(f"{field}: {', '.join(vals)}")
    variants = profile.get("name_variants") or []
    if variants:
        parts.append("name variants: " + ", ".join(v["name"] for v in variants[:6]))
    sources = profile.get("sources") or []
    if sources:
        parts.append("sources: " + ", ".join(s["sheet"] for s in sources[:6]))
    candidates = profile.get("alias_candidates") or []
    if candidates:
        parts.append("alias candidates: " + ", ".join(candidates[:6]))
    return "\n".join(parts)
