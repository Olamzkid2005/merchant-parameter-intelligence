"""
app.py - Merchant Intelligence Web UI (Streamlit).
"""

import html
import sqlite3
import sys
import time
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# CLI tools (data_quality.py, reconcile.py, ...) live in scripts/
_SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from merchant_intelligence import MerchantSearch, config
from merchant_intelligence.entity import EntityResolver
from merchant_intelligence.fuzzy import token_sort_ratio
from ui_theme import (FONTS, CSS, app_bar_html, sidebar_html, icon,
                      score_chip, match_pill, score_bar, stat_card,
                      state_card, gauge_html, skeleton_table_html,
                      skeleton_stat_cards)

st.set_page_config(page_title="Merchant Intelligence", layout="wide",
                   initial_sidebar_state="expanded")

st.markdown(FONTS, unsafe_allow_html=True)
st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


@st.cache_resource
def get_searcher() -> MerchantSearch:
    return MerchantSearch()


@st.cache_resource
def get_resolver() -> EntityResolver:
    return EntityResolver()


@st.cache_resource
def record_count() -> int:
    try:
        conn = sqlite3.connect(str(config.active_db()))
        n = conn.execute("SELECT COUNT(*) FROM merchants").fetchone()[0]
        conn.close()
        return n
    except Exception:
        return 0


def to_excel_bytes(dfs: dict) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, df in dfs.items():
            if df is not None and not df.empty:
                df.to_excel(writer, sheet_name=str(name)[:31], index=False)
    return buffer.getvalue()


def esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


PAGES = [
    ("search", "Search"),
    ("batch", "Batch Search"),
    ("entity", "Entity Graph"),
    ("reconcile", "Reconcile"),
    ("quality", "Data Quality"),
]
PAGE_KEYS = [k for k, _ in PAGES]

current_page = st.query_params.get("page", "search")
if current_page not in PAGE_KEYS:
    current_page = "search"

st.markdown(app_bar_html(current_page, PAGES), unsafe_allow_html=True)

with st.sidebar:
    st.markdown(sidebar_html(current_page, record_count()), unsafe_allow_html=True)


def row_contact_html(rec) -> str:
    email = esc(rec.get("email"))
    phone = esc(rec.get("phone"))
    c1 = email or "\u2014"
    c2 = phone or ""
    return (f'<div class="row-contact"><span class="c1">{c1}</span>'
            f'<span class="c2">{c2}</span></div>')


def result_row_html(res, idx: int) -> str:
    rec = res.record
    score10 = round(res.overall_score / 10, 1)
    name = esc(rec.get("merchant_name"))
    tid = esc(rec.get("tid")) or "\u2014"
    mx = esc(rec.get("mxcode")) or esc(rec.get("payable_code")) or "\u2014"
    contact = row_contact_html(rec)
    sheet = esc(rec.get("sheet_name")) or "\u2014"

    fs = res.field_scores or {}
    order = ["merchant_name", "slip_header", "email", "account_name"]
    bars = ""
    for f in order:
        if f in fs:
            tone = "secondary" if f == "email" else "primary"
            bars += score_bar(f.replace("_", " ").title(), fs.get(f, 0), tone)

    tokens = esc(", ".join(res.matched_tokens)) or "\u2014"
    open_attr = " open" if idx == 0 else ""
    return f"""
    <details class="mi-row"{open_attr}>
      <summary>
        {score_chip(score10)}
        {match_pill(res.match_type)}
        <span class="row-name">{name}</span>
        <span class="row-tid">{tid}</span>
        <span class="row-mx">{mx}</span>
        {contact}
        <span class="chev">\u25be</span>
      </summary>
      <div class="mi-panel">
        <div class="panel-grid">
          <div>
            <h4 class="panel-title">Why matched? (Deep Analysis)</h4>
            <div class="score-grid">{bars}</div>
            <p class="tokens-line">Matched tokens: <b>{tokens}</b> \u00b7 Sheet: {sheet}</p>
          </div>
        </div>
      </div>
    </details>"""


def results_table_html(rows_html: str, showing: int, total: int) -> str:
    return f"""
    <div class="mi-table-wrap">
      <div class="mi-table-head">
        <span>Score</span><span>Match Type</span><span>Merchant Name</span>
        <span>TID</span><span>MX Code</span><span>Contact Details</span><span></span>
      </div>
      {rows_html}
      <div style="padding:14px 24px;background:var(--mi-surface-low);display:flex;justify-content:space-between;align-items:center;font-size:12px;color:var(--mi-on-surface-variant);">
        <span>Showing {showing} of {total} records</span>
      </div>
    </div>"""


# ===== Page 1: Search =====

def tab_search():
    st.markdown("""
    <div class="page-head">
      <div>
        <h1>Search</h1>
        <p>Single merchant lookup with confidence scoring &amp; deep analysis.</p>
      </div>
    </div>""", unsafe_allow_html=True)

    with st.form("search_form", clear_on_submit=False):
        query = st.text_input(
            "Search",
            placeholder="Merchant name \u2014 try LAGOON WATERS LTD",
            label_visibility="collapsed",
            key="search_query",
        )
        submitted = st.form_submit_button("Search", type="primary", use_container_width=False)

    if submitted:
        st.session_state["query"] = query.strip()

    query = st.session_state.get("query", "").strip()
    if not query:
        st.markdown(state_card(
            "manage_search", "No query yet",
            "Try searching for a merchant name or Merchant ID (TID) to start "
            "your investigation."), unsafe_allow_html=True)
        return

    t0 = time.perf_counter()
    searcher = get_searcher()
    results = searcher.search(query, limit=20, min_score=0)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    st.markdown(f"""
    <div class="search-stats">
      <span>Query: <b>{esc(query)}</b></span>
      <span class="dot-sep"></span>
      <span><b>{len(results)}</b> matches</span>
      <span class="dot-sep"></span>
      <span style="color:var(--mi-outline)">{elapsed_ms:.0f}ms response time</span>
    </div>""", unsafe_allow_html=True)

    if not results:
        st.markdown(state_card(
            "search_off", "No match found",
            f"No records matched <b>{esc(query)}</b>. Check the spelling or "
            "try fewer filters.", ""), unsafe_allow_html=True)
        return

    rows = "".join(result_row_html(r, i) for i, r in enumerate(results))
    st.markdown(results_table_html(rows, min(len(results), 20), len(results)),
                unsafe_allow_html=True)

    st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
    results_list = list(results)
    labels = [f"{r.record.get('merchant_name','')} \u2014 {round(r.overall_score/10,1)}/10"
              for r in results_list]
    c_sel, c_confirm, c_flag = st.columns([3, 1.4, 1.2])
    with c_sel:
        choice = st.selectbox(
            "Confirm this merchant", range(len(labels)),
            format_func=lambda i: labels[i], key="confirm_target")
    with c_confirm:
        st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
        if st.button("Confirm this merchant", type="primary",
                     use_container_width=True, key="confirm_btn"):
            res = results_list[choice]
            learned = searcher.matcher.alias_engine.learn(
                query, res.record.get("merchant_name", ""))
            st.toast("\U0001f9e0 Learned: "
                     f"{query} \u2192 {res.record.get('merchant_name','')} \u2014 saved for next run"
                     if learned else "Already known \u2014 no new mapping needed.")
    with c_flag:
        st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
        if st.button("Flag for Review", use_container_width=True, key="flag_btn"):
            flagged = st.session_state.setdefault("flagged", [])
            flagged.append(f"{query} \u2192 {results_list[choice].record.get('merchant_name','')}")
            st.toast(f"\U0001f6a9 Flagged for review ({len(flagged)})")

    recent = []
    if "query" in st.session_state and st.session_state["query"].strip():
        recent.append(("Searched", esc(st.session_state["query"]), "now"))
    if st.session_state.get("flagged"):
        for f in st.session_state["flagged"][-3:]:
            recent.append(("Flagged", esc(f), "now"))
    if not recent:
        recent.append(("Searched", "LAGOON WATERS LTD", "2m ago"))

    recent_items = "".join(
        f'<div class="bento-item"><span class="t">{label}: {text}</span>'
        f'<span class="m">{m}</span></div>'
        for label, text, m in recent
    )
    try:
        from data_quality import run_quality
        q = run_quality()
        insight = (f"{q['missing'].get('email', 0):,} records are missing emails \u2014 "
                   "a batch scrub on the Email field is recommended to lift "
                   f"coverage towards the 90% threshold.")
    except Exception:
        insight = ("Search engine is live with compound expansion, phonetic "
                   "matching and alias auto-learning enabled.")

    st.markdown(f"""
    <div class="bento-grid">
      <div class="mi-card bento-card">
        <div class="bento-head">{icon("history", filled=True)}
          <h3>Recent Activity</h3></div>
        <div class="bento-list">{recent_items}</div>
      </div>
      <div class="mi-card bento-card">
        <div class="bento-head">{icon("auto_awesome", filled=True)}
          <h3>System Insights</h3></div>
        <p style="font-size:13px;color:var(--mi-on-surface-variant);margin:0;">
          {insight}</p>
        <div class="bento-deco">{icon("analytics")}</div>
      </div>
    </div>""", unsafe_allow_html=True)


# ===== Page 2: Batch Search =====

def tab_batch():
    st.markdown("""
    <div class="page-head">
      <div>
        <h1>Batch Search</h1>
        <p>Paste a merchant list \u2014 search all, review stats, export Excel.</p>
      </div>
    </div>""", unsafe_allow_html=True)

    with st.form("batch_form"):
        text = st.text_area(
            "Merchant list",
            height=220,
            placeholder="THE FILM HOUSE LIMITED\nSPAR Lekki\nBEACONHEALTH DIAGNOSTICS",
            label_visibility="collapsed",
        )
        run_btn = st.form_submit_button(
            f"Search {len([l for l in text.splitlines() if l.strip()]) if text else 0} merchants",
            type="primary", use_container_width=False)

    if not run_btn or not text.strip():
        st.markdown(state_card(
            "playlist_add_check", "Batch input",
            "Paste one merchant per line (name, alias, or TID). Limit: 1,000 entries.",
            ""), unsafe_allow_html=True)
        return

    merchants = [l.strip() for l in text.splitlines() if l.strip()][:1000]
    searcher = get_searcher()

    loading = st.empty()
    loading.markdown(
        f'<div style="margin-bottom:16px">{skeleton_table_html(rows=4)}</div>'
        '<p style="text-align:center;color:var(--mi-on-surface-variant);font-size:13px">'
        f'Searching {len(merchants)} merchants\u2026</p>', unsafe_allow_html=True)

    t0 = time.perf_counter()
    rows = []
    try:
        for m in merchants:
            res = searcher.search(m, limit=1, min_score=0)
            best = res[0] if res else None
            rec = best.record if best else {}
            rows.append({
                "Input": m,
                "Best Match": rec.get("merchant_name", ""),
                "Score": round(best.overall_score / 10, 1) if best else 0,
                "Match Type": best.match_type if best else "Not Found",
                "Email": rec.get("email", ""),
                "Phone": rec.get("phone", ""),
                "TID": rec.get("tid", ""),
                "MX Code": rec.get("mxcode", ""),
                "Sheet": rec.get("sheet_name", ""),
            })
    finally:
        loading.empty()
    elapsed = time.perf_counter() - t0

    found = sum(1 for r in rows if r["Score"] >= 5.0)
    missing = len(rows) - found
    emails = sum(1 for r in rows if r["Email"])
    pct = round(found / len(rows) * 100) if rows else 0

    st.markdown(f"""
    <div class="mi-card" style="margin-bottom:18px;">
      <div style="display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:10px;">
        <span style="font-family:'IBM Plex Sans',sans-serif;font-size:12px;font-weight:600;color:var(--mi-on-surface-variant);">
          Processing complete</span>
        <span style="font-size:24px;font-weight:800;color:var(--mi-primary);">{pct}%</span>
      </div>
      <div style="height:8px;background:var(--mi-surface-highest);border-radius:999px;overflow:hidden;margin-bottom:18px;">
        <div style="height:100%;width:{pct}%;background:var(--mi-primary);border-radius:999px;transition:width .8s ease;"></div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;font-size:13px;">
        <div><span style="color:var(--mi-on-surface-variant);">Matches Found</span><br><b>{found}</b></div>
        <div><span style="color:var(--mi-on-surface-variant);">Missing Info</span><br><b style="color:var(--mi-error);">{missing}</b></div>
        <div><span style="color:var(--mi-on-surface-variant);">Emails</span><br><b>{emails}</b></div>
        <div><span style="color:var(--mi-on-surface-variant);">Time Taken</span><br><b>{elapsed:.1f}s</b></div>
      </div>
    </div>""", unsafe_allow_html=True)

    df = pd.DataFrame(rows)

    def rows_df_html_rows(rows_list):
        out = []
        for r in rows_list:
            score = r["Score"]
            mt = r["Match Type"]
            if score >= 8.5:
                chip = "score-green"
            elif score >= 7:
                chip = "score-orange"
            elif score >= 5:
                chip = "score-slate"
            else:
                chip = "score-red"
            email = esc(r["Email"]) or "\u2014"
            phone = esc(r["Phone"]) or "\u2014"
            out.append((r, f"""
            <div class="mi-row">
              <div style="display:grid;grid-template-columns:150px 1.5fr 64px 150px 1.4fr 1.2fr 1fr;gap:12px;align-items:center;padding:14px 20px;border-bottom:1px solid var(--mi-outline-variant);">
                <span class="mi-mono" style="font-size:12px;color:var(--mi-on-surface-variant);">{esc(r['Input'])}</span>
                <span style="font-weight:700;color:var(--mi-on-surface);">{esc(r['Best Match']) or 'NOT FOUND'}</span>
                <span class="score-chip {chip}" style="width:44px;height:32px;">{score:.1f}</span>
                <span>{match_pill(mt)}</span>
                <span style="font-size:12px;">{email}</span>
                <span style="font-size:12px;">{phone}</span>
                <span class="mi-mono" style="font-size:12px;color:var(--mi-outline);">{esc(r['TID']) or '\u2014'}</span>
              </div>
            </div>"""))
        return out

    body = ""
    for _, r in rows_df_html_rows(rows):
        body += r

    st.markdown(f"""
    <div class="mi-table-wrap">
      <div style="padding:16px 24px;display:flex;justify-content:space-between;align-items:center;background:var(--mi-surface-low);border-bottom:1px solid var(--mi-outline-variant);">
        <div style="display:flex;gap:8px;align-items:center;">
          <b style="font-size:16px;">Intelligence Results</b>
          <span style="background:rgba(37,99,235,.1);color:var(--mi-primary);padding:4px 10px;border-radius:6px;font-size:10px;font-weight:700;border:1px solid rgba(37,99,235,.2);">FUZZY MATCH: ON</span>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:150px 1.5fr 64px 150px 1.4fr 1.2fr 1fr;gap:12px;align-items:center;padding:12px 20px;background:var(--mi-surface-container);font-family:'IBM Plex Sans',sans-serif;font-size:11px;font-weight:600;color:var(--mi-on-surface-variant);text-transform:uppercase;letter-spacing:.06em;">
        <span>Input</span><span>Best Match</span><span>Score</span><span>Match Type</span><span>Email</span><span>Phone</span><span>TID</span>
      </div>
      {body}
      <div style="padding:14px 24px;background:var(--mi-surface-low);display:flex;justify-content:space-between;align-items:center;font-size:12px;color:var(--mi-on-surface-variant);">
        <span>Showing {len(rows)} of {len(rows)} entries</span>
      </div>
    </div>""", unsafe_allow_html=True)

    excel = to_excel_bytes({"Batch Search": df})
    st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)
    st.download_button(
        "Download Excel", excel,
        file_name="batch_search_results.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary")


# ===== Page 3: Entity Graph =====

def tab_entity():
    st.markdown("""
    <div class="page-head">
      <div>
        <h1>Entity Graph</h1>
        <p>Merchant families linked through shared emails, phones, MX codes &amp; TIDs.</p>
      </div>
    </div>""", unsafe_allow_html=True)

    with st.form("entity_form"):
        c1, c2 = st.columns([5, 1])
        with c1:
            seed = st.text_input("Seed merchant", placeholder="Seed merchant (Name/TID)",
                                 label_visibility="collapsed")
        with c2:
            st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
            seed_btn = st.form_submit_button("Map Family", use_container_width=True)

    if not seed_btn or not seed.strip():
        st.markdown(state_card(
            "hub", "Entity graph",
            "Type a seed merchant to map its family \u2014 records connected by "
            "shared email, phone, MX code, TID or account number.",
            ""), unsafe_allow_html=True)
        return

    resolver = get_resolver()
    family = resolver.family_of(seed.strip())
    members = family.get("members", [])

    if not members:
        st.markdown(state_card(
            "hub_off", "No linked records",
            f"No family members found for <b>{esc(seed)}</b>.",
            ""), unsafe_allow_html=True)
        return

    seed_name = esc(seed.strip().upper())
    seed_tid = esc(members[0].get("tid")) or "\u2014"
    seed_email = esc(members[0].get("email")) or "\u2014"

    def link_label(reasons):
        labels = []
        for reason in (reasons or [])[:2]:
            field = reason.split("=")[0] if "=" in reason else reason
            val = reason.split("=", 1)[1] if "=" in reason else ""
            pretty = {"email": "Shared Email", "mxcode": "Shared MX",
                      "phone": "Shared Phone", "tid": "Shared TID",
                      "payable_code": "Shared Payable",
                      "account_number": "Shared Account",
                      "merchant_id": "Shared MID"}.get(field, field.title())
            labels.append(f"{pretty}: {esc(val)}" if val else pretty)
        return labels

    scored = []
    for m in members:
        mname = str(m.get("merchant_name", ""))
        sim = token_sort_ratio(seed.strip(), mname)
        pct = max(int(sim * 100), 30) if mname else 0
        scored.append((m, pct))

    scored.sort(key=lambda x: -x[1])
    top = scored[:5]

    node_cards = ""
    for m, pct in top:
        mname = esc(m.get("merchant_name"))
        sub = esc(m.get("sheet_name")) or "Parameter File"
        reasons = link_label(m.get("link_reasons"))
        reason_html = ""
        if reasons:
            reason_html = (f'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;">'
                           + "".join(f'<span class="link-chip">{r}</span>' for r in reasons)
                           + "</div>")
        badge_cls = "match-badge good" if pct >= 80 else "match-badge"
        node_cards += f"""
        <div class="node-card">
          <div class="nc-head">
            <span class="ic">{icon("business")}</span>
            <span class="{badge_cls}">MATCH: {pct}%</span>
          </div>
          <h4>{mname}</h4>
          <div class="nc-sub">{sub}</div>
          {reason_html}
        </div>"""

    shared = family.get("shared", {})
    id_panel = ""
    field_meta = [
        ("email", "Email Identity", "mail", "3 records"),
        ("mxcode", "Network Infrastructure", "dns", "2 records"),
        ("phone", "Phone / KYC", "call", "1 record"),
        ("tid", "Terminal Identity", "point_of_sale", "\u2014"),
    ]
    for field, title, sym, _ in field_meta:
        groups = shared.get(field, {})
        if not groups:
            continue
        for value, names in list(groups.items())[:1]:
            total = len(names)
            lis = "".join(
                f'<li>{"<b>" if i == 0 else ""}{esc(n)}{"</b>" if i == 0 else ""}</li>'
                for i, n in enumerate(names[:5]))
            id_panel += f"""
            <div class="id-group">
              <div class="id-group-head"><span>{title}</span>
                <span class="cnt">{total} record{'s' if total != 1 else ''}</span></div>
              <div class="id-box">
                <div class="id-head">
                  <div class="id-ic {'' if field != 'mxcode' else 'alt'}">{icon(sym)}</div>
                  <div><div class="id-mail">{esc(value)}</div>
                  <div class="id-sub">Shared identifier</div></div>
                </div>
                <ul>{lis}</ul>
              </div>
            </div>"""

    if not id_panel:
        id_panel = ('<div class="id-box"><div class="id-head"><div class="id-ic">'
                    f'{icon("link")}</div><div><div class="id-mail">No shared identifiers</div>'
                    '<div class="id-sub">Only the seed record matched</div></div></div></div>')

    st.markdown(f"""
    <div class="graph-canvas">
      <div class="seed-node">
        <span class="ic">{icon("hub", filled=True)}</span>
        <span class="nm">{seed_name}</span>
        <span class="td">TID: {seed_tid}</span>
      </div>
      <div class="node-row">{node_cards}</div>
    </div>
    <div style="display:flex;gap:14px;margin-top:14px;">
      <span class="link-chip">{icon("mail")} Shared Email</span>
      <span class="link-chip">{icon("dns")} Shared MX Code</span>
      <span class="link-chip">{icon("call")} Shared Phone</span>
      <span class="link-chip">{icon("point_of_sale")} Shared TID</span>
    </div>
    <div style="margin-top:16px;display:flex;gap:22px;font-size:13px;color:var(--mi-on-surface-variant);padding:14px 18px;background:var(--mi-surface-container);border-radius:14px;align-items:center;">
      <span><b style="color:var(--mi-on-surface);">{len(members)}</b> Nodes</span>
      <span><b style="color:var(--mi-on-surface);">{len(family.get('shared', {}))}</b> Identifier Types</span>
      <span style="margin-left:auto;">Seed: {seed_email}</span>
    </div>
    """, unsafe_allow_html=True)

    candidates = family.get("alias_candidates", [])
    if candidates:
        st.markdown('<div style="height:18px"></div>', unsafe_allow_html=True)
        st.markdown("### \U0001f4a1 Alias candidates")
        cols = st.columns(min(len(candidates[:6]), 3))
        for i, cand in enumerate(candidates[:6]):
            with cols[i % 3]:
                if st.button(f"Teach: {cand}", key=f"teach_{i}"):
                    engine = get_searcher().matcher.alias_engine
                    engine.learn(seed.strip(), cand)
                    st.toast(f"\U0001f9e0 Learned alias: {seed.strip()} \u2192 {cand}")


# ===== Page 4: Reconcile =====

def tab_reconcile():
    st.markdown("""
    <div class="page-head">
      <div>
        <h1>Reconcile</h1>
        <p>Turn a merchant list into a verified report with emails &amp; contacts.</p>
      </div>
    </div>""", unsafe_allow_html=True)

    with st.form("reconcile_form"):
        text = st.text_area(
            "Merchant list",
            height=200,
            placeholder="One merchant per line\u2026",
            label_visibility="collapsed",
        )
        run_btn = st.form_submit_button("Re-run Batch", type="primary")

    if not run_btn or not text.strip():
        st.markdown(state_card(
            "rule", "Reconciliation",
            "Paste a list of merchants to reconcile against the parameter files. "
            "You'll get verified matches, not-found records and recovered emails.",
            ""), unsafe_allow_html=True)
        return

    merchants = [l.strip() for l in text.splitlines() if l.strip()][:1000]
    loading = st.empty()
    loading.markdown(skeleton_stat_cards(5), unsafe_allow_html=True)
    try:
        from reconcile import reconcile as run_reconcile
        report = run_reconcile(merchants, top_n=3)
    finally:
        loading.empty()

    matches = report["matches"]
    not_found = report["not_found"]
    emails = report["emails"]
    contacts = report["contacts"]
    n_total = len(merchants)
    n_found = len(matches)
    n_missing = len(not_found)
    n_emails = len(emails)
    n_contacts = len(contacts)

    st.markdown(f"""
    <div class="stat-row">
      {stat_card("Total Records", n_total, "analytics")}
      {stat_card("Confirmed Matches", f'{n_found} <span style="font-size:13px;font-weight:500;color:var(--mi-on-surface-variant);">({round(n_found/n_total*100) if n_total else 0}%)</span>', "check_circle", "success")}
      {stat_card("Unresolved", n_missing, "warning", "error")}
      {stat_card("Extracted Emails", n_emails, "mail")}
      {stat_card("Unique Contacts", n_contacts, "person")}
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div style="height:20px"></div>', unsafe_allow_html=True)

    view = st.segmented_control(
        "Reconcile view",
        ["Matches", "Not Found", "Recovered Assets"],
        default="Matches", key="recon_view")

    if view == "Matches":
        if matches.empty:
            st.markdown(state_card("search_off", "No matches",
                                   "No confident matches found for this list.",
                                   ""), unsafe_allow_html=True)
        else:
            body = ""
            for _, r in matches.iterrows():
                score = float(r.get("Score") or 0)
                chip = "score-green" if score >= 8.5 else ("score-orange" if score >= 7 else "score-slate")
                loc = esc(r.get("Sheet")) or "\u2014"
                body += f"""
                <div class="mi-row">
                  <div style="display:grid;grid-template-columns:170px 1.6fr 70px 1.2fr 150px;gap:12px;align-items:center;padding:14px 20px;border-bottom:1px solid var(--mi-outline-variant);">
                    <span class="mi-mono" style="font-size:12px;color:var(--mi-on-surface-variant);">{esc(r.get('Merchant (input)'))}</span>
                    <span style="font-weight:700;">{esc(r.get('Best Match'))}</span>
                    <span class="score-chip {chip}" style="width:44px;height:32px;">{score:.1f}</span>
                    <span>{match_pill(r.get('Match Type'))}</span>
                    <span style="font-size:12px;color:var(--mi-on-surface-variant);">{loc}</span>
                  </div>
                </div>"""
            st.markdown(f"""
            <div class="mi-table-wrap">
              <div style="display:grid;grid-template-columns:170px 1.6fr 70px 1.2fr 150px;gap:12px;align-items:center;padding:12px 20px;background:var(--mi-surface-container);font-family:'IBM Plex Sans',sans-serif;font-size:11px;font-weight:600;color:var(--mi-on-surface-variant);text-transform:uppercase;letter-spacing:.06em;">
                <span>Merchant ID</span><span>Verified Name</span><span>Score</span><span>Status</span><span>Location</span>
              </div>
              {body}
            </div>""", unsafe_allow_html=True)

    elif view == "Not Found":
        if not_found.empty:
            st.markdown(state_card("verified", "All resolved",
                                   "Every merchant in the list was matched.",
                                   ""), unsafe_allow_html=True)
        else:
            for _, r in not_found.iterrows():
                st.markdown(f"""
                <div class="mi-card" style="display:flex;justify-content:space-between;align-items:center;opacity:.95;margin-bottom:12px;">
                  <div>
                    <b>{esc(r.get('Merchant (input)'))}</b>
                    <div style="font-size:12px;color:var(--mi-on-surface-variant);">No direct match found in Registry.</div>
                  </div>
                  <div style="display:flex;align-items:center;gap:14px;">
                    <div style="text-align:right;">
                      <div style="font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--mi-outline);">Closest Candidate</div>
                      <b style="font-size:13px;">{esc(r.get('Closest Candidate')) or '\u2014'} (Score: {r.get('Score', 0):.1f})</b>
                    </div>
                  </div>
                </div>""", unsafe_allow_html=True)

    else:
        c_emails, c_phones = st.columns(2)
        with c_emails:
            st.markdown(f"### {icon('mail')} Unique Emails ({n_emails})")
            if emails.empty:
                st.markdown('<p style="color:var(--mi-on-surface-variant);font-size:13px;">None recovered.</p>',
                            unsafe_allow_html=True)
            else:
                html_rows = "".join(
                    f'<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-radius:8px;transition:background .12s;">'
                    f'<span style="font-size:13px;">{esc(r.get("Email"))}</span>'
                    f'<span style="font-size:10px;color:var(--mi-outline);">{esc(str(r.get("Matched As"))[:26])}</span></div>'
                    for _, r in emails.iterrows())
                st.markdown(f'<div style="background:var(--mi-surface);border:1px solid var(--mi-outline-variant);border-radius:14px;padding:12px;max-height:420px;overflow-y:auto;">{html_rows}</div>',
                            unsafe_allow_html=True)
        with c_phones:
            st.markdown(f"### {icon('phone')} Phone Numbers ({n_contacts})")
            if contacts.empty:
                st.markdown('<p style="color:var(--mi-on-surface-variant);font-size:13px;">None recovered.</p>',
                            unsafe_allow_html=True)
            else:
                html_rows = "".join(
                    f'<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-radius:8px;">'
                    f'<span style="font-size:13px;">{esc(r.get("Phone")) or "\u2014"}</span>'
                    f'<span style="font-size:10px;color:var(--mi-outline);">{esc(str(r.get("Contact Name"))[:26])}</span></div>'
                    for _, r in contacts.head(60).iterrows())
                st.markdown(f'<div style="background:var(--mi-surface);border:1px solid var(--mi-outline-variant);border-radius:14px;padding:12px;max-height:420px;overflow-y:auto;">{html_rows}</div>',
                            unsafe_allow_html=True)

    st.markdown('<div style="height:14px"></div>', unsafe_allow_html=True)
    excel = to_excel_bytes(report)
    st.download_button(
        "Download Excel Report", excel,
        file_name="Merchant_Reconciliation_Report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary")


# ===== Shared CSS for st.html() iframes =====
_DQ_IFRAME_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=IBM+Plex+Sans:wght@400;500;600;700&family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=swap" rel="stylesheet">'
)
_DQ_SHARED_CSS = (
    'body { margin: 0; padding: 0; font-family: Inter, sans-serif; }'
    '.material-symbols-outlined { font-variation-settings: "FILL" 0, "wght" 400, "GRAD" 0, "opsz" 24; display: inline-block; line-height: 1; vertical-align: middle; }'
    '.dq-table-wrap { background: #fff; border: 1px solid #c3c6d7; border-radius: 12px; box-shadow: 0 1px 2px rgba(13,28,47,.05); overflow: hidden; }'
    '.dq-table-head { display: flex; justify-content: space-between; align-items: center; padding: 16px 20px 12px; }'
    '.dq-table-head h3 { font-size: 16px; font-weight: 700; margin: 0; color: #0d1c2f; }'
    '.dq-table-head a { font-family: IBM Plex Sans, sans-serif; font-size: 12px; font-weight: 600; color: #004ac6; text-decoration: none; }'
    '.dq-table { width: 100%; border-collapse: collapse; }'
    '.dq-table th { font-family: IBM Plex Sans, sans-serif; font-size: 11px; font-weight: 600; color: #434655; text-transform: uppercase; letter-spacing: .06em; text-align: left; padding: 8px 20px; border-bottom: 1px solid #c3c6d7; }'
    '.dq-table td { font-size: 13px; padding: 12px 20px; border-bottom: 1px solid #c3c6d7; color: #0d1c2f; }'
    '.dq-table tr:last-child td { border-bottom: none; }'
    '.field-icon { display: flex; align-items: center; gap: 8px; font-weight: 500; }'
    '.field-icon .ic { width: 28px; height: 28px; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 16px; flex-shrink: 0; }'
    '.field-icon .ic.blue { background: rgba(37,99,235,.1); color: #004ac6; }'
    '.field-icon .ic.green { background: rgba(0,108,73,.1); color: #006c49; }'
    '.field-icon .ic.amber { background: rgba(120,75,0,.1); color: #784b00; }'
    '.field-icon .ic.red { background: rgba(186,26,26,.1); color: #ba1a1a; }'
    '.sev-pill { display: inline-block; padding: 3px 10px; border-radius: 999px; font-family: IBM Plex Sans, sans-serif; font-size: 11px; font-weight: 700; }'
    '.sev-high { background: #fee2e2; color: #991b1b; }'
    '.sev-medium { background: #fff7ed; color: #9a3412; }'
    '.sev-low { background: #dcfce7; color: #166534; }'
    '.dq-list-card { background: #fff; border: 1px solid #c3c6d7; border-radius: 12px; box-shadow: 0 1px 2px rgba(13,28,47,.05); overflow: hidden; }'
    '.dq-list-head { display: flex; justify-content: space-between; align-items: center; padding: 16px 20px 12px; }'
    '.dq-list-head h3 { font-size: 16px; font-weight: 700; margin: 0; color: #0d1c2f; display: flex; align-items: center; gap: 8px; }'
    '.dq-list-head .badge { font-family: IBM Plex Sans, sans-serif; font-size: 10px; font-weight: 700; color: #434655; background: #dde9ff; padding: 3px 8px; border-radius: 6px; }'
    '.dq-list-item { display: flex; justify-content: space-between; align-items: center; padding: 12px 20px; border-bottom: 1px solid #c3c6d7; }'
    '.dq-list-item:last-child { border-bottom: none; }'
    '.dq-list-item .left { display: flex; flex-direction: column; gap: 2px; }'
    '.dq-list-item .code { font-family: IBM Plex Sans, ui-monospace, monospace; font-size: 14px; font-weight: 700; color: #0d1c2f; }'
    '.dq-list-item .sub { font-size: 12px; color: #434655; }'
    '.dq-list-item .right { font-size: 12px; color: #434655; display: flex; align-items: center; gap: 6px; }'
    '.dq-list-item .right .num { font-weight: 700; color: #004ac6; }'
)


def _dq_iframe(inner_html: str) -> str:
    """Wrap inner HTML with fonts + shared DQ CSS for st.html() iframe."""
    return f"{_DQ_IFRAME_FONTS}<style>{_DQ_SHARED_CSS}</style>{inner_html}"


# ===== Page 5: Data Quality =====

def tab_quality():
    # Page header
    st.markdown("""
    <div style="display:flex;justify-content:space-between;align-items:center;margin:24px 0 20px;">
      <div>
        <h1 style="font-size:28px;font-weight:800;letter-spacing:-.02em;margin:0;">Data Quality</h1>
        <p style="color:var(--mi-on-surface-variant);font-size:14px;margin:4px 0 0;">Real-time health assessment across active merchant registries.</p>
      </div>
    </div>""", unsafe_allow_html=True)

    from data_quality import run_quality
    q = run_quality()
    total = q["total"]
    missing = q["missing"]
    code_names = q["code_names"]
    orphans = q["orphans"]
    dup = q["duplicate_tids"]
    mx_multi = q["mx_multiname"]

    pct_email = missing.get("email", 0) / total * 100 if total else 0
    pct_orphan = orphans / total * 100 if total else 0
    pct_name = code_names / total * 100 if total else 0

    # Synced percentage (records with name) = 100 - pct_name
    synced_pct = round(100 - pct_name, 1)
    missing_names_pct = round(pct_name, 1)
    orphan_pct = round(pct_orphan, 1)

    # ===== Row 1: Three stat cards (mockup style) =====
    st.markdown(f"""
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:20px;">
      <div class="dq-stat">
        <span class="dq-icon">{icon("database")}</span>
        <div class="dq-label">Total Records</div>
        <div class="dq-value" style="color:var(--mi-on-surface);">{total:,}</div>
        <div class="dq-desc"><span class="dot green"></span> Synchronized</div>
      </div>
      <div class="dq-stat">
        <span class="dq-icon" style="color:var(--mi-error);">{icon("person_off")}</span>
        <div class="dq-label">Missing Names</div>
        <div class="dq-value" style="color:var(--mi-error);">{code_names:,}</div>
        <div class="dq-desc">~{missing_names_pct}% of total records</div>
      </div>
      <div class="dq-stat">
        <span class="dq-icon" style="color:#b45309;">{icon("link_off")}</span>
        <div class="dq-label">Orphan Records</div>
        <div class="dq-value" style="color:#b45309;">{orphans:,}</div>
        <div class="dq-desc">Disconnected entities</div>
      </div>
    </div>""", unsafe_allow_html=True)

    # ===== Row 2: Left (Missing Fields table) + Right (Duplicate TIDs + MX Codes) =====
    c_left, c_right = st.columns([3, 2])

    with c_left:
        # Missing Critical Fields table
        field_specs = [
            ("Email Address", missing.get("email", 0), "email", "blue", "High"),
            ("Phone Number", missing.get("phone", 0), "phone", "green", "Medium"),
            ("Tax ID (TIN)", missing.get("mxcode", 0), "badge", "amber", "High"),
            ("Physical Address", missing.get("address", 0), "location_on", "red", "Low"),
        ]
        table_rows = ""
        for fname, cnt, sym, color_cls, severity in field_specs:
            pct_val = cnt / total * 100 if total else 0
            sev_cls = "sev-high" if severity == "High" else ("sev-medium" if severity == "Medium" else "sev-low")
            table_rows += f"""
            <tr>
              <td>
                <div class="field-icon">
                  <span class="ic {color_cls}">{icon(sym)}</span>
                  <span>{fname}</span>
                </div>
              </td>
              <td style="font-weight:700;">{cnt:,}</td>
              <td>{pct_val:.1f}%</td>
              <td><span class="sev-pill {sev_cls}">{severity}</span></td>
            </tr>"""

        st.html(_dq_iframe(f"""
        <div class="dq-table-wrap">
          <div class="dq-table-head">
            <h3>Missing Critical Fields</h3>
            <a href="#">View All</a>
          </div>
          <table class="dq-table">
            <thead>
              <tr>
                <th>Field Name</th>
                <th>Missing Count</th>
                <th>Percentage</th>
                <th>Severity</th>
              </tr>
            </thead>
            <tbody>
              {table_rows}
            </tbody>
          </table>
        </div>"""))

    with c_right:
        # Duplicate TIDs list
        dup_items = ""
        for i, r in dup.head(3).iterrows():
            tid = esc(r.get("TID")) or "\u2014"
            n = r.get("Records", 0)
            dup_items += f"""
            <div class="dq-list-item">
              <div class="left">
                <span class="code">{tid}</span>
              </div>
              <div class="right">Shared by <span class="num">{n}</span> entities</div>
            </div>"""
        if not dup_items:
            dup_items = '<div class="dq-list-item"><div class="left"><span class="code">No duplicates found</span></div></div>'

        st.html(_dq_iframe(f"""
        <div class="dq-list-card">
          <div class="dq-list-head">
            <h3>{icon("warning", filled=True)} Duplicate TIDs</h3>
            <span class="badge">{len(dup)} clusters found</span>
          </div>
          {dup_items}
        </div>"""))

        st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)

        # MX Codes w/ Multiple Names list
        mx_items = ""
        for i, r in mx_multi.head(2).iterrows():
            mx = esc(r.get("MX Code")) or "\u2014"
            n = r.get("Distinct Names", 0)
            try:
                import sqlite3 as _sql
                _conn = _sql.connect(str(config.active_db()))
                names = [row[0] for row in _conn.execute(
                    "SELECT DISTINCT merchant_name FROM merchants WHERE mxcode = ? AND merchant_name != '' LIMIT 3",
                    (r.get("MX Code", ""),)
                ).fetchall()]
                _conn.close()
            except Exception:
                names = []
            names_html = "<br>".join(f'<span class="sub">{esc(n)}</span>' for n in names[:2]) if names else f'<span class="sub">{n} names</span>'
            mx_items += f"""
            <div class="dq-list-item">
              <div class="left">
                <span class="code">{mx}</span>
                {names_html}
              </div>
              <div class="right">{icon("open_in_new")}</div>
            </div>"""
        if not mx_items:
            mx_items = '<div class="dq-list-item"><div class="left"><span class="code">No multi-name MX codes</span></div></div>'

        st.html(_dq_iframe(f"""
        <div class="dq-list-card">
          <div class="dq-list-head">
            <h3>{icon("dns")} MX Codes w/ Multiple Names</h3>
            <span class="badge">{len(mx_multi)} flags</span>
          </div>
          {mx_items}
        </div>"""))

    # ===== Download button =====
    excel = to_excel_bytes({
        "Summary": pd.DataFrame([
            {"Metric": "Total", "Value": total},
            {"Metric": "Code names", "Value": code_names},
            {"Metric": "Orphans", "Value": orphans},
        ]),
        "Missing Fields": pd.DataFrame(
            [{"Field": f, "Missing": n} for f, n in missing.items()]
        ),
        "Duplicate TIDs": dup,
        "MX Multi-Name": mx_multi,
    })
    st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
    st.download_button(
        "Download Data Health Report", excel,
        file_name="data_quality_report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary")


# ===== Dispatch =====

if current_page == "search":
    tab_search()
elif current_page == "batch":
    tab_batch()
elif current_page == "entity":
    tab_entity()
elif current_page == "reconcile":
    tab_reconcile()
elif current_page == "quality":
    tab_quality()
