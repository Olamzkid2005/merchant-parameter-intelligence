"""
ui_theme.py — Material-3 "Light Clean Enterprise" design system v2.

Overhaul: aggressive Streamlit CSS overrides so widgets, sidebar, and layout
match the designer's HTML mockup closely.
"""
import html

# ── Fonts ───────────────────────────────────────────────────────────────────
FONTS = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=IBM+Plex+Sans:wght@400;500;600;700&family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=swap" rel="stylesheet">
"""

# ── Design tokens ───────────────────────────────────────────────────────────
TOKENS = """
:root {
  --mi-bg:              #f8f9ff;
  --mi-surface:         #ffffff;
  --mi-surface-low:     #eff4ff;
  --mi-surface-container:   #e6eeff;
  --mi-surface-high:    #dde9ff;
  --mi-surface-highest: #d5e3fd;
  --mi-on-surface:      #0d1c2f;
  --mi-on-surface-variant:  #434655;
  --mi-outline:         #737686;
  --mi-outline-variant: #c3c6d7;
  --mi-primary:         #004ac6;
  --mi-primary-container:   #2563eb;
  --mi-on-primary:      #ffffff;
  --mi-on-primary-container: #00174b;
  --mi-primary-fixed:   #dbe1ff;
  --mi-secondary:       #006c49;
  --mi-secondary-container: #6cf8bb;
  --mi-on-secondary-container: #00714d;
  --mi-error:           #ba1a1a;
  --mi-error-container: #ffdad6;
  --mi-on-error-container: #93000a;
  --mi-inverse-surface: #233144;
  --mi-inverse-on-surface: #ebf1ff;
  --mi-radius:          12px;
  --mi-shadow-sm:       0 1px 2px rgba(13,28,47,.05), 0 1px 3px rgba(13,28,47,.06);
  --mi-shadow-md:       0 4px 12px rgba(13,28,47,.08);
  --mi-shadow-lg:       0 8px 24px rgba(13,28,47,.10);
}
"""

# ── Global CSS ──────────────────────────────────────────────────────────────
CSS = TOKENS + r"""
/* ========== NUCLEAR RESET: override ALL Streamlit defaults ========== */

/* 1. Background & base type */
html, body,
.stApp,
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stMainBlockContainer"],
.block-container {
  background: var(--mi-bg) !important;
  color: var(--mi-on-surface) !important;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
}

/* 2. Kill Streamlit header/footer/sidebar chrome */
header[data-testid="stHeader"],
[data-testid="stToolbar"],
#MainMenu,
footer,
[data-testid="stDecoration"] {
  display: none !important;
  visibility: hidden !important;
  height: 0 !important;
  min-height: 0 !important;
  padding: 0 !important;
}

/* 3. Main content area */
[data-testid="stAppViewBlockContainer"],
[data-testid="stMainBlockContainer"],
.block-container {
  padding: 0 32px 40px !important;
  max-width: 1440px !important;
  margin: 0 auto !important;
}
[data-testid="stVerticalBlock"] {
  gap: 0.5rem !important;
}

/* 4. SIDEBAR — full override */
[data-testid="stSidebar"] {
  background: var(--mi-surface-low) !important;
  border-right: 1px solid var(--mi-outline-variant) !important;
  box-shadow: var(--mi-shadow-sm) !important;
  width: 272px !important;
  min-width: 272px !important;
  max-width: 272px !important;
}
[data-testid="stSidebar"] > div:first-child {
  padding: 24px 18px !important;
}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
  gap: 0.35rem !important;
}
[data-testid="stSidebar"] [data-testid="stMarkdown"] {
  padding: 0 !important; margin: 0 !important;
}
[data-testid="stSidebar"] [data-testid="stMarkdown"] p {
  margin: 0 !important; padding: 0 !important;
}
[data-testid="stSidebar"] [data-testid="stMarkdown"] h1,
[data-testid="stSidebar"] [data-testid="stMarkdown"] h2,
[data-testid="stSidebar"] [data-testid="stMarkdown"] h3 {
  margin: 0 !important; padding: 0 !important;
}
[data-testid="stSidebar"] a {
  text-decoration: none !important;
  color: inherit !important;
}
[data-testid="stSidebar"] #MainMenu,
[data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] {
  display: none !important;
}

/* ========== SEARCH FORM — clean styling, no overlay hack ========== */
/* The native st.text_input already gets styled by the overrides below;
   this block just removes the default form border so the search bar
   doesn't look like a card inside a card. */
#search_form[data-testid="stForm"] {
  background: transparent !important;
  border: none !important;
  padding: 0 !important;
  box-shadow: none !important;
  margin-top: -4px !important;
}
#search_form label {
  display: none !important;
}
#search_form [data-baseweb="input"] {
  height: 52px !important;
  font-size: 16px !important;
}

/* ========== TYPOGRAPHY HELPERS ========== */
.mi-label { font-family: 'IBM Plex Sans', sans-serif; }
.mi-muted { color: var(--mi-on-surface-variant); }
.mi-mono  { font-family: 'IBM Plex Sans', ui-monospace, monospace; }

/* ========== ICONS ========== */
.material-symbols-outlined {
  font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24;
  display: inline-block; line-height: 1; vertical-align: middle;
  user-select: none;
}

/* ========== APP BAR (top tabs) ========== */
.app-bar {
  position: sticky; top: 0; z-index: 50;
  background: var(--mi-bg);
  border-bottom: 1px solid var(--mi-outline-variant);
  padding: 0 32px;
  margin: 0 -32px;
}
.app-bar-inner {
  max-width: 1440px; margin: 0 auto;
  display: flex; align-items: center; justify-content: space-between;
  height: 64px;
}
.app-tabs { display: flex; gap: 28px; align-items: center; }
.app-tab {
  font-family: 'IBM Plex Sans', sans-serif; font-size: 14px; font-weight: 500;
  color: var(--mi-on-surface-variant); text-decoration: none;
  padding: 18px 0 16px; border-bottom: 2px solid transparent;
  transition: color .15s, border-color .15s;
}
.app-tab:hover { color: var(--mi-primary); }
.app-tab.active {
  color: var(--mi-primary); font-weight: 700;
  border-bottom-color: var(--mi-primary);
}
.app-bar-right { display: flex; align-items: center; gap: 14px; }
.env-badge {
  font-family: 'IBM Plex Sans', sans-serif; font-size: 10px; font-weight: 700;
  letter-spacing: .12em; text-transform: uppercase;
  background: var(--mi-surface-high); color: var(--mi-on-surface-variant);
  border: 1px solid var(--mi-outline-variant); border-radius: 6px;
  padding: 4px 10px;
}
.app-icon-btn {
  color: var(--mi-on-surface-variant); text-decoration: none; font-size: 22px; line-height: 1;
}
.app-icon-btn:hover { color: var(--mi-primary); }
.avatar {
  width: 34px; height: 34px; border-radius: 50%;
  background: var(--mi-primary-fixed); border: 1px solid var(--mi-outline-variant);
  display: flex; align-items: center; justify-content: center;
  font-family: 'Inter', sans-serif; font-weight: 700; font-size: 12px; color: var(--mi-primary);
}

/* ========== SIDEBAR brand + nav ========== */
/* ULTRA-NUCLEAR: kill every Streamlit default in the sidebar */
[data-testid="stSidebar"] * {
  font-family: 'IBM Plex Sans', sans-serif !important;
  text-decoration: none !important;
  -webkit-text-decoration: none !important;
  text-decoration-line: none !important;
  text-decoration-style: none !important;
  text-decoration-color: transparent !important;
  text-underline-offset: 0 !important;
  border-bottom: none !important;
}
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] div,
[data-testid="stSidebar"] a,
[data-testid="stSidebar"] strong,
[data-testid="stSidebar"] em,
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] label {
  line-height: inherit !important;
  text-decoration: none !important;
  -webkit-text-decoration: none !important;
}
/* Streamlit wraps markdown in these containers — strip ALL underlines */
[data-testid="stSidebar"] [data-testid="stMarkdown"] a,
[data-testid="stSidebar"] [data-testid="stMarkdown"] p,
[data-testid="stSidebar"] [data-testid="stMarkdown"] span,
[data-testid="stSidebar"] [data-testid="stMarkdown"] strong {
  text-decoration: none !important;
  -webkit-text-decoration: none !important;
  text-decoration-line: none !important;
  border-bottom: none !important;
  background-image: none !important;
  background: transparent !important;
}

.side-brand { margin-bottom: 32px !important; padding-top: 4px !important; }
.side-brand h1 {
  font-family: 'Inter', sans-serif !important;
  font-size: 22px !important; font-weight: 900 !important;
  letter-spacing: -.02em !important; color: var(--mi-primary) !important;
  margin: 0 0 8px !important; padding: 0 !important;
  text-decoration: none !important;
}
.side-records {
  display: flex !important; align-items: center !important; gap: 8px !important;
  font-size: 12px !important; color: var(--mi-on-surface-variant) !important;
  margin: 0 0 2px !important; padding: 0 !important;
}
.side-dot {
  width: 8px !important; height: 8px !important; border-radius: 50% !important;
  background: var(--mi-secondary) !important; flex-shrink: 0 !important;
}
.side-sub {
  font-family: 'IBM Plex Sans', sans-serif !important;
  font-size: 10px !important; font-weight: 600 !important;
  letter-spacing: .14em !important; text-transform: uppercase !important;
  color: var(--mi-outline) !important; margin-top: 4px !important;
  padding: 0 !important;
}
.side-nav {
  display: flex !important; flex-direction: column !important; gap: 2px !important;
  margin: 0 !important; padding: 0 !important;
}
.side-item {
  display: flex !important; align-items: center !important; gap: 12px !important;
  padding: 11px 14px !important; margin: 0 !important;
  color: var(--mi-on-surface-variant) !important;
  text-decoration: none !important;
  -webkit-text-decoration: none !important;
  text-decoration-line: none !important;
  border-radius: 10px !important; border: none !important;
  font-family: 'IBM Plex Sans', sans-serif !important; font-size: 13px !important;
  font-weight: 500 !important; line-height: 1.4 !important;
  transition: background .15s, color .15s !important;
  cursor: pointer !important;
}
.side-item:hover { background: var(--mi-surface-container) !important; color: var(--mi-on-surface) !important; }
.side-item.active {
  background: var(--mi-primary-container) !important;
  color: var(--mi-on-primary-container) !important;
  font-weight: 700 !important; box-shadow: var(--mi-shadow-sm) !important;
}
.side-item .material-symbols-outlined {
  font-size: 20px !important; line-height: 1 !important;
  display: inline-flex !important; vertical-align: middle !important;
  flex-shrink: 0 !important;
}
.side-btn {
  display: flex !important; align-items: center !important; justify-content: center !important;
  gap: 8px !important; width: 100% !important;
  margin: 20px 0 !important; padding: 14px !important;
  background: var(--mi-primary) !important; color: var(--mi-on-primary) !important;
  text-decoration: none !important;
  -webkit-text-decoration: none !important;
  border-radius: 12px !important;
  border: none !important; cursor: pointer !important;
  font-family: 'IBM Plex Sans', sans-serif !important;
  font-weight: 700 !important; font-size: 13px !important;
  box-shadow: var(--mi-shadow-md) !important;
  transition: opacity .15s, transform .1s !important;
}
.side-btn:hover { opacity: .92 !important; }
.side-btn:active { transform: scale(.97) !important; }
.side-foot {
  border-top: 1px solid var(--mi-outline-variant) !important;
  padding-top: 16px !important; margin-top: 16px !important;
  display: flex !important; flex-direction: column !important; gap: 2px !important;
}
.side-foot .side-item {
  padding: 10px 14px !important;
}

/* ========== CARDS ========== */
.mi-card {
  background: var(--mi-surface);
  border: 1px solid var(--mi-outline-variant);
  border-radius: var(--mi-radius);
  box-shadow: var(--mi-shadow-sm);
  padding: 20px 24px;
}
.stat-card { display: flex; flex-direction: column; gap: 6px; }
.stat-card .stat-label {
  font-family: 'IBM Plex Sans', sans-serif; font-size: 11px;
  color: var(--mi-on-surface-variant); font-weight: 600; letter-spacing: .02em;
}
.stat-card .stat-value { display: flex; align-items: flex-end; justify-content: space-between; }
.stat-card .stat-num { font-size: 28px; font-weight: 800; line-height: 1.1; }
.stat-card .stat-icon { font-size: 24px; }

/* ========== SCORE CHIPS + PILLS ========== */
.score-chip {
  display: inline-flex; align-items: center; justify-content: center;
  width: 48px; height: 42px; border-radius: 12px;
  font-weight: 800; font-size: 15px;
}
.text-success { color: var(--mi-secondary); }
.text-error   { color: var(--mi-error); }
.text-tertiary { color: #784b00; }
.score-green  { background: #dcfce7; color: #166534; }
.score-orange { background: #ffedd5; color: #9a3412; }
.score-slate  { background: #f1f5f9; color: #334155; }
.score-red    { background: #fee2e2; color: #991b1b; }

.pill {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 4px 12px; border-radius: 999px;
  font-family: 'IBM Plex Sans', sans-serif; font-size: 11px; font-weight: 700;
  text-transform: uppercase; letter-spacing: .03em; border: 1px solid transparent;
}
.pill-exact    { background: #dcfce7; color: #14532d; border-color: #bbf7d0; }
.pill-high     { background: #ffedd5; color: #7c2d12; border-color: #fed7aa; }
.pill-possible { background: #f1f5f9; color: #1e293b; border-color: #e2e8f0; }
.pill-alias    { background: #dbeafe; color: #1e40af; border-color: #bfdbfe; }
.pill-low      { background: #fee2e2; color: #7f1d1d; border-color: #fecaca; }

/* ========== PROGRESS BARS ========== */
.bar-row { display: flex; flex-direction: column; gap: 5px; }
.bar-head { display: flex; justify-content: space-between; align-items: center; }
.bar-label { font-size: 10px; color: var(--mi-outline); font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
.bar-val   { font-size: 16px; font-weight: 800; }
.bar-track { height: 7px; background: var(--mi-outline-variant); border-radius: 999px; overflow: hidden; }
.bar-fill  { height: 100%; border-radius: 999px; background: var(--mi-primary); transition: width .6s ease; }
.bar-fill.secondary { background: var(--mi-secondary); }
.bar-fill.error     { background: var(--mi-error); }

/* ========== RESULTS TABLE ========== */
.mi-table-wrap {
  background: var(--mi-surface); border: 1px solid var(--mi-outline-variant);
  border-radius: var(--mi-radius); box-shadow: var(--mi-shadow-sm); overflow: hidden;
}
.mi-table-head {
  display: grid;
  grid-template-columns: 64px 150px 1.6fr 1fr 1fr 1.4fr 32px;
  gap: 12px; align-items: center;
  background: var(--mi-surface-container); border-bottom: 1px solid var(--mi-outline-variant);
  padding: 14px 24px;
  font-family: 'IBM Plex Sans', sans-serif; font-size: 11px; font-weight: 600;
  color: var(--mi-on-surface-variant); text-transform: uppercase; letter-spacing: .06em;
}
.mi-row { border-bottom: 1px solid var(--mi-outline-variant); }
.mi-row:last-child { border-bottom: none; }
.mi-row summary {
  display: grid;
  grid-template-columns: 64px 150px 1.6fr 1fr 1fr 1.4fr 32px;
  gap: 12px; align-items: center;
  padding: 16px 24px; cursor: pointer; list-style: none;
  transition: background .12s;
}
.mi-row summary::-webkit-details-marker { display: none; }
.mi-row summary:hover { background: rgba(37,99,235,.04); }
.mi-row .row-name { font-weight: 700; color: var(--mi-on-surface); font-size: 14px; }
.mi-row .row-tid, .mi-row .row-mx { font-family: 'IBM Plex Sans', ui-monospace, monospace; font-size: 12px; color: var(--mi-outline); }
.mi-row .row-contact { display: flex; flex-direction: column; }
.mi-row .row-contact .c1 { font-size: 12px; font-weight: 500; color: var(--mi-on-surface); }
.mi-row .row-contact .c2 { font-size: 11px; color: var(--mi-outline); }
.chev { font-size: 18px; color: var(--mi-outline); transition: transform .18s; text-align: right; }
.mi-row[open] .chev { transform: rotate(180deg); }
.mi-panel { padding: 24px; background: var(--mi-surface-low); border-top: 1px solid var(--mi-outline-variant); }
.panel-grid { display: flex; gap: 32px; flex-wrap: wrap; }
.panel-grid > div:first-child { flex: 1; min-width: 320px; }
.panel-grid > div:last-child { width: 260px; display: flex; flex-direction: column; justify-content: flex-end; gap: 10px; }
.panel-title {
  font-family: 'IBM Plex Sans', sans-serif;
  font-size: 11px; font-weight: 600; color: var(--mi-on-surface-variant);
  text-transform: uppercase; letter-spacing: .08em; margin: 0 0 14px;
}
.score-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 14px; }
.tokens-line { font-size: 12px; color: var(--mi-on-surface-variant); margin-top: 12px; }

/* ========== BUTTONS ========== */
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  border-radius: 12px; font-family: 'IBM Plex Sans', sans-serif; font-weight: 700; font-size: 13px;
  text-decoration: none; cursor: pointer; transition: opacity .15s, transform .1s, background .15s;
  padding: 12px 18px; border: 1px solid transparent;
}
.btn:active { transform: scale(.97); }
.btn-primary {
  background: var(--mi-primary); color: var(--mi-on-primary);
  box-shadow: var(--mi-shadow-sm); border: none;
}
.btn-primary:hover { opacity: .9; }
.btn-ghost { background: transparent; color: var(--mi-on-surface-variant); border-color: var(--mi-outline-variant); }
.btn-ghost:hover { background: var(--mi-surface-container); }

/* ========== STREAMLIT WIDGET OVERRIDES ========== */

/* Primary buttons */
.stButton > button,
[data-testid="stBaseButton-primary"],
button[kind="primary"],
.stFormSubmitButton button {
  background: var(--mi-primary) !important;
  color: var(--mi-on-primary) !important;
  border: none !important;
  border-radius: 12px !important;
  font-family: 'IBM Plex Sans', sans-serif !important;
  font-weight: 700 !important;
  font-size: 13px !important;
  padding: 10px 22px !important;
  box-shadow: var(--mi-shadow-sm) !important;
  transition: opacity .15s, transform .1s !important;
  min-height: 42px !important;
}
.stButton > button:hover,
[data-testid="stBaseButton-primary"]:hover,
button[kind="primary"]:hover,
.stFormSubmitButton button:hover {
  opacity: .92 !important;
}
.stButton > button:active,
button[kind="primary"]:active {
  transform: scale(.97) !important;
}

/* Secondary buttons */
[data-testid="stBaseButton-secondary"],
button[kind="secondary"],
.stDownloadButton button {
  background: var(--mi-surface) !important;
  color: var(--mi-on-surface-variant) !important;
  border: 1px solid var(--mi-outline-variant) !important;
  border-radius: 12px !important;
  font-family: 'IBM Plex Sans', sans-serif !important;
  font-weight: 600 !important;
  font-size: 13px !important;
  padding: 10px 18px !important;
}

/* Text inputs */
[data-testid="stTextInput"] input,
.stTextInput input {
  background: var(--mi-surface) !important;
  border: 1.5px solid var(--mi-outline-variant) !important;
  border-radius: 14px !important;
  color: var(--mi-on-surface) !important;
  font-family: 'Inter', sans-serif !important;
  font-size: 15px !important;
  padding: 14px 18px !important;
  min-height: 48px !important;
  box-shadow: var(--mi-shadow-sm) !important;
  transition: border-color .2s, box-shadow .2s !important;
}
[data-testid="stTextInput"] input:focus,
.stTextInput input:focus {
  border-color: var(--mi-primary) !important;
  box-shadow: 0 0 0 3px rgba(37,99,235,.15) !important;
  outline: none !important;
}
[data-testid="stTextInput"] input::placeholder,
.stTextInput input::placeholder {
  color: var(--mi-outline) !important;
  font-size: 15px !important;
}

/* Labels */
[data-testid="stTextInput"] label,
[data-testid="stTextArea"] label,
.stTextInput label,
.stTextArea label,
.stSelectbox label,
.stMultiSelect label {
  font-family: 'IBM Plex Sans', sans-serif !important;
  font-weight: 600 !important;
  font-size: 13px !important;
  color: var(--mi-on-surface-variant) !important;
}

/* Text areas */
[data-testid="stTextArea"] textarea,
.stTextArea textarea {
  background: var(--mi-surface) !important;
  border: 1.5px solid var(--mi-outline-variant) !important;
  border-radius: 14px !important;
  color: var(--mi-on-surface) !important;
  font-family: 'Inter', sans-serif !important;
  font-size: 14px !important;
  padding: 14px 18px !important;
  box-shadow: var(--mi-shadow-sm) !important;
  transition: border-color .2s, box-shadow .2s !important;
}
[data-testid="stTextArea"] textarea:focus,
.stTextArea textarea:focus {
  border-color: var(--mi-primary) !important;
  box-shadow: 0 0 0 3px rgba(37,99,235,.15) !important;
  outline: none !important;
}

/* Selectbox */
[data-testid="stSelectbox"] div[data-baseweb="select"],
.stSelectbox div[data-baseweb="select"] {
  background: var(--mi-surface) !important;
  border: 1.5px solid var(--mi-outline-variant) !important;
  border-radius: 12px !important;
  font-family: 'Inter', sans-serif !important;
}
[data-testid="stSelectbox"] div[data-baseweb="select"]:focus-within {
  border-color: var(--mi-primary) !important;
  box-shadow: 0 0 0 3px rgba(37,99,235,.15) !important;
}

/* Forms */
[data-testid="stForm"] {
  border: 1px solid var(--mi-outline-variant) !important;
  border-radius: 16px !important;
  padding: 24px !important;
  box-shadow: var(--mi-shadow-sm) !important;
}

/* Expanders */
[data-testid="stExpander"] {
  border: 1px solid var(--mi-outline-variant) !important;
  border-radius: 12px !important;
  background: var(--mi-surface) !important;
}

/* Toasts */
[data-testid="stToast"] {
  background: var(--mi-inverse-surface) !important;
  color: var(--mi-inverse-on-surface) !important;
  border-radius: 12px !important;
  font-family: 'IBM Plex Sans', sans-serif !important;
}

/* Download button */
.stDownloadButton > button {
  background: var(--mi-primary) !important;
  color: var(--mi-on-primary) !important;
  border: none !important;
}

/* Segmented control */
[data-testid="stSegmentedControl"] {
  background: var(--mi-surface-container) !important;
  border-radius: 12px !important;
  padding: 3px !important;
}
[data-testid="stSegmentedControl"] button {
  font-family: 'IBM Plex Sans', sans-serif !important;
  font-weight: 600 !important;
  font-size: 13px !important;
  color: var(--mi-on-surface-variant) !important;
  border-radius: 10px !important;
  background: transparent !important;
  border: none !important;
}
[data-testid="stSegmentedControl"] button[aria-checked="true"],
[data-testid="stSegmentedControl"] button[data-selected="true"] {
  background: var(--mi-primary) !important;
  color: var(--mi-on-primary) !important;
  box-shadow: var(--mi-shadow-sm) !important;
}

/* Columns gap */
[data-testid="stHorizontalBlock"] {
  gap: 16px !important;
}

/* ========== EMPTY / ERROR / LOADING STATES ========== */
.state-card {
  background: var(--mi-surface);
  border: 1px solid var(--mi-outline-variant);
  border-radius: 16px;
  box-shadow: var(--mi-shadow-sm);
  padding: 48px 40px;
  display: flex; flex-direction: column; align-items: center; text-align: center;
}
.state-icon {
  width: 88px; height: 88px; border-radius: 50%;
  background: var(--mi-surface-high);
  display: flex; align-items: center; justify-content: center;
  color: var(--mi-outline-variant); font-size: 44px; margin-bottom: 20px;
}
.state-card h3 { margin: 0 0 8px; font-size: 20px; font-weight: 700; color: var(--mi-on-surface); }
.state-card p { color: var(--mi-on-surface-variant); font-size: 14px; max-width: 360px; margin: 0 0 16px; line-height: 1.5; }

/* ========== SKELETONS ========== */
.sk {
  display: inline-block;
  background: linear-gradient(90deg, #f1f5f9 25%, #e2e8f0 50%, #f1f5f9 75%);
  background-size: 200% 100%;
  animation: shimmer 1.4s infinite linear;
  border-radius: 6px;
  height: 12px;
}
@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
.sk.w40    { width: 40%; }
.sk.w70    { width: 70%; }
.sk.circle { width: 40px; height: 36px; border-radius: 10px; }
.sk.pill   { width: 90px; height: 20px; border-radius: 999px; }
.skeleton-row {
  display: grid; grid-template-columns: 64px 150px 1.6fr 1fr 1fr 1.4fr 32px;
  gap: 12px; align-items: center; padding: 14px 24px;
  border-bottom: 1px solid var(--mi-outline-variant);
}

/* ========== BENTO GRID ========== */
.bento-grid { display: grid; grid-template-columns: 1fr 2fr; gap: 20px; margin-top: 24px; }
@media (max-width: 900px) { .bento-grid { grid-template-columns: 1fr; } }
.bento-card { position: relative; overflow: hidden; }
.bento-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.bento-head h3 { font-size: 14px; font-weight: 700; margin: 0; }
.bento-list { display: flex; flex-direction: column; gap: 12px; }
.bento-item { display: flex; justify-content: space-between; align-items: center; font-size: 12px; }
.bento-item .t { color: var(--mi-on-surface); }
.bento-item .m { color: var(--mi-outline); font-size: 10px; }
.bento-deco { position: absolute; right: -20px; bottom: -20px; opacity: .08; font-size: 120px; pointer-events: none; }

/* ========== STAT CARD ROW ========== */
.stat-row { display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px; }
@media (max-width: 1100px) { .stat-row { grid-template-columns: repeat(2, 1fr); } }

/* ========== ENTITY GRAPH ========== */
.graph-canvas {
  background-image: radial-gradient(#cbd5e1 1px, transparent 1px);
  background-size: 26px 26px;
  border: 1px solid var(--mi-outline-variant); border-radius: var(--mi-radius);
  padding: 40px; min-height: 440px; position: relative;
}
.seed-node {
  display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center;
  width: 200px; height: 200px; border-radius: 50%;
  background: var(--mi-primary); color: var(--mi-on-primary);
  box-shadow: 0 0 0 8px var(--mi-surface), var(--mi-shadow-lg);
  margin: 0 auto;
}
.seed-node .ic { font-size: 36px; margin-bottom: 8px; }
.seed-node .nm { font-size: 14px; font-weight: 800; line-height: 1.3; text-transform: uppercase; letter-spacing: .01em; padding: 0 16px; }
.seed-node .td { font-size: 11px; opacity: .8; margin-top: 6px; }
.node-row { display: flex; gap: 20px; justify-content: center; flex-wrap: wrap; margin-top: 36px; }
.node-card {
  width: 220px; background: var(--mi-surface); border: 2px solid var(--mi-outline-variant);
  border-radius: 14px; padding: 16px; box-shadow: var(--mi-shadow-sm);
  transition: border-color .15s, transform .15s, box-shadow .15s;
}
.node-card:hover { border-color: var(--mi-primary); transform: translateY(-3px); box-shadow: var(--mi-shadow-md); }
.node-card .nc-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px; }
.node-card .nc-head .ic { color: var(--mi-primary); font-size: 22px; }
.node-card .match-badge {
  font-size: 10px; font-weight: 700; padding: 4px 10px; border-radius: 999px;
  background: var(--mi-surface-high); color: var(--mi-on-surface-variant);
}
.node-card .match-badge.good { background: var(--mi-secondary-container); color: var(--mi-on-secondary-container); }
.node-card h4 { margin: 0 0 4px; font-size: 14px; font-weight: 700; }
.node-card .nc-sub { font-size: 11px; color: var(--mi-on-surface-variant); margin-bottom: 10px; }
.node-card .nc-reason {
  display: flex; align-items: center; gap: 6px; font-size: 10px; font-weight: 700;
  color: var(--mi-primary); text-transform: uppercase; letter-spacing: .03em;
}
.link-chip {
  display: inline-flex; align-items: center; gap: 6px;
  background: var(--mi-surface); border: 1px solid var(--mi-outline-variant);
  border-radius: 999px; padding: 5px 12px; font-size: 11px; color: var(--mi-on-surface-variant);
  box-shadow: var(--mi-shadow-sm);
}

/* ========== SHARED IDENTIFIERS ========== */
.id-group { margin-bottom: 22px; }
.id-group-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.id-group-head span { font-size: 10px; font-weight: 700; color: var(--mi-on-surface-variant); text-transform: uppercase; letter-spacing: .1em; }
.id-group-head .cnt { background: var(--mi-surface-high); padding: 3px 10px; border-radius: 6px; font-size: 10px; }
.id-box { background: var(--mi-surface-low); border: 1px solid var(--mi-outline-variant); border-radius: 14px; padding: 14px; }
.id-box .id-head { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
.id-box .id-ic { background: rgba(37,99,235,.1); padding: 8px; border-radius: 10px; color: var(--mi-primary); font-size: 20px; }
.id-box .id-ic.alt { background: rgba(120,75,0,.08); color: #784b00; }
.id-box .id-mail { font-size: 14px; font-weight: 700; }
.id-box .id-sub  { font-size: 11px; opacity: .7; }
.id-box ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.id-box li { font-size: 12px; display: flex; align-items: center; gap: 8px; }
.id-box li::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: var(--mi-primary); flex-shrink: 0; }

/* ========== DARK DUPLICATE-TID CARD ========== */
.dup-card {
  background: var(--mi-inverse-surface); color: var(--mi-inverse-on-surface);
  border-radius: var(--mi-radius); padding: 24px; position: relative; overflow: hidden;
  display: flex; flex-direction: column; gap: 16px;
}
.dup-card .deco {
  position: absolute; right: -16px; top: -16px; width: 140px; height: 140px;
  background: rgba(37,99,235,.25); filter: blur(46px);
}
.dup-item {
  background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.12);
  border-radius: 12px; padding: 14px;
}
.dup-item .sev { font-size: 10px; font-weight: 700; letter-spacing: .12em; color: var(--mi-secondary-container); text-transform: uppercase; }
.dup-item .tid { font-size: 20px; font-family: 'IBM Plex Sans', ui-monospace, monospace; font-weight: 700; margin: 4px 0; }
.dup-item .n { font-weight: 700; font-size: 15px; }
.dup-item .tags { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
.dup-item .tag { background: rgba(255,255,255,.1); border-radius: 8px; padding: 4px 10px; font-size: 11px; }

/* ========== GAUGE ========== */
.gauge-wrap { display: flex; align-items: center; gap: 28px; }
.gauge { position: relative; width: 160px; height: 160px; flex-shrink: 0; }
.gauge svg { transform: rotate(-90deg); }
.gauge .g-label { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.gauge .g-num { font-size: 34px; font-weight: 900; }
.gauge .g-sub { font-size: 11px; font-weight: 700; color: var(--mi-on-surface-variant); text-transform: uppercase; letter-spacing: .06em; }

/* ========== PAGE HEADER ========== */
.page-head {
  display: flex; align-items: center; justify-content: space-between;
  margin: 24px 0 20px; gap: 16px; flex-wrap: wrap;
}
.page-head h1 { font-size: 28px; font-weight: 800; letter-spacing: -.02em; margin: 0; }
.page-head p { color: var(--mi-on-surface-variant); font-size: 14px; margin: 4px 0 0; }
.page-head .actions { display: flex; gap: 12px; }

/* ========== SEARCH STATS ========== */
.search-stats {
  display: flex; align-items: center; justify-content: center; gap: 14px;
  color: var(--mi-on-surface-variant); font-size: 14px; margin: 14px 0 20px;
}
.search-stats b { color: var(--mi-on-surface); }
.dot-sep { width: 5px; height: 5px; border-radius: 50%; background: var(--mi-outline-variant); }

/* ========== LEGEND ========== */
.legend { display: flex; gap: 18px; align-items: center; font-size: 12px; font-weight: 700; margin-top: 14px; }
.legend .sw { width: 12px; height: 12px; border-radius: 50%; display: inline-block; }

/* ========== DQ STAT CARDS (mockup style) ========== */
.dq-stat {
  background: var(--mi-surface); border: 1px solid var(--mi-outline-variant);
  border-radius: 12px; padding: 20px 24px; position: relative;
  box-shadow: var(--mi-shadow-sm);
}
.dq-stat .dq-icon {
  position: absolute; top: 18px; right: 18px;
  font-size: 22px; color: var(--mi-outline-variant);
}
.dq-stat .dq-label {
  font-family: 'IBM Plex Sans', sans-serif; font-size: 11px; font-weight: 700;
  color: var(--mi-on-surface-variant); text-transform: uppercase;
  letter-spacing: .08em; margin-bottom: 8px;
}
.dq-stat .dq-value {
  font-size: 32px; font-weight: 800; line-height: 1; margin-bottom: 4px;
}
.dq-stat .dq-desc {
  font-size: 12px; color: var(--mi-on-surface-variant); display: flex; align-items: center; gap: 6px;
}
.dq-stat .dq-desc .dot {
  width: 6px; height: 6px; border-radius: 50%; display: inline-block;
}
.dq-stat .dq-desc .dot.green { background: var(--mi-secondary); }
.dq-stat .dq-desc .dot.red { background: var(--mi-error); }
.dq-stat .dq-desc .dot.amber { background: #b45309; }

/* ========== DQ TABLE (mockup style) ========== */
.dq-table-wrap {
  background: var(--mi-surface); border: 1px solid var(--mi-outline-variant);
  border-radius: 12px; box-shadow: var(--mi-shadow-sm); overflow: hidden;
}
.dq-table-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 16px 20px 12px;
}
.dq-table-head h3 {
  font-size: 16px; font-weight: 700; margin: 0; color: var(--mi-on-surface);
}
.dq-table-head a {
  font-family: 'IBM Plex Sans', sans-serif; font-size: 12px; font-weight: 600;
  color: var(--mi-primary); text-decoration: none;
}
.dq-table-head a:hover { text-decoration: underline; }
.dq-table {
  width: 100%; border-collapse: collapse;
}
.dq-table th {
  font-family: 'IBM Plex Sans', sans-serif; font-size: 11px; font-weight: 600;
  color: var(--mi-on-surface-variant); text-transform: uppercase;
  letter-spacing: .06em; text-align: left; padding: 8px 20px;
  border-bottom: 1px solid var(--mi-outline-variant);
}
.dq-table td {
  font-size: 13px; padding: 12px 20px; border-bottom: 1px solid var(--mi-outline-variant);
  color: var(--mi-on-surface);
}
.dq-table tr:last-child td { border-bottom: none; }
.dq-table .field-icon {
  display: inline-flex; align-items: center; gap: 8px; font-weight: 500;
}
.dq-table .field-icon .ic {
  width: 28px; height: 28px; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  font-size: 16px; flex-shrink: 0;
}
.dq-table .field-icon .ic.blue { background: rgba(37,99,235,.1); color: var(--mi-primary); }
.dq-table .field-icon .ic.green { background: rgba(0,108,73,.1); color: var(--mi-secondary); }
.dq-table .field-icon .ic.amber { background: rgba(120,75,0,.1); color: #784b00; }
.dq-table .field-icon .ic.red { background: rgba(186,26,26,.1); color: var(--mi-error); }

/* Severity pills */
.sev-pill {
  display: inline-block; padding: 3px 10px; border-radius: 999px;
  font-family: 'IBM Plex Sans', sans-serif; font-size: 11px; font-weight: 700;
}
.sev-high { background: #fee2e2; color: #991b1b; }
.sev-medium { background: #fff7ed; color: #9a3412; }
.sev-low { background: #dcfce7; color: #166534; }

/* ========== DQ LIST ITEMS (Duplicate TIDs, MX Codes) ========== */
.dq-list-card {
  background: var(--mi-surface); border: 1px solid var(--mi-outline-variant);
  border-radius: 12px; box-shadow: var(--mi-shadow-sm); overflow: hidden;
}
.dq-list-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 16px 20px 12px;
}
.dq-list-head h3 {
  font-size: 16px; font-weight: 700; margin: 0; color: var(--mi-on-surface);
  display: flex; align-items: center; gap: 8px;
}
.dq-list-head .badge {
  font-family: 'IBM Plex Sans', sans-serif; font-size: 10px; font-weight: 700;
  color: var(--mi-on-surface-variant); background: var(--mi-surface-high);
  padding: 3px 8px; border-radius: 6px;
}
.dq-list-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 20px; border-bottom: 1px solid var(--mi-outline-variant);
}
.dq-list-item:last-child { border-bottom: none; }
.dq-list-item .left { display: flex; flex-direction: column; gap: 2px; }
.dq-list-item .code {
  font-family: 'IBM Plex Sans', ui-monospace, monospace;
  font-size: 14px; font-weight: 700; color: var(--mi-on-surface);
}
.dq-list-item .sub {
  font-size: 12px; color: var(--mi-on-surface-variant);
}
.dq-list-item .right {
  font-size: 12px; color: var(--mi-on-surface-variant);
  display: flex; align-items: center; gap: 6px;
}
.dq-list-item .right .num {
  font-weight: 700; color: var(--mi-primary);
}
"""


# ── Component helpers ───────────────────────────────────────────────────────

def icon(name: str, filled: bool = False, cls: str = "") -> str:
    fill = 1 if filled else 0
    extra = f" {cls}" if cls else ""
    return (f'<span class="material-symbols-outlined{extra}" '
            f'style="font-variation-settings: \'FILL\' {fill}, \'wght\' 500;">{name}</span>')


def score_chip(score: float) -> str:
    if score >= 8.5:
        cls = "score-green"
    elif score >= 7.0:
        cls = "score-orange"
    elif score >= 5.0:
        cls = "score-slate"
    else:
        cls = "score-red"
    return f'<span class="score-chip {cls}">{score:.1f}</span>'


def match_pill(match_type: str) -> str:
    t = (match_type or "").lower()
    if "exact" in t:
        cls = "pill-exact"
    elif "alias" in t:
        cls = "pill-alias"
    elif "high" in t:
        cls = "pill-high"
    elif "possible" in t or "medium" in t:
        cls = "pill-possible"
    else:
        cls = "pill-low"
    label = (match_type or "Unknown").replace("Match", "").strip() or match_type or "Unknown"
    if "low" in t:
        label = "Low Confidence"
    return f'<span class="pill {cls}">{html.escape(label, quote=True)}</span>'


def score_bar(label: str, value: float, tone: str = "primary") -> str:
    v = max(0.0, min(float(value), 100.0))
    tone_cls = "" if tone == "primary" else f" {tone}"
    return f"""
    <div class="bar-row">
      <div class="bar-head">
        <span class="bar-label">{label}</span>
        <span class="bar-val">{v:.0f}</span>
      </div>
      <div class="bar-track"><div class="bar-fill{tone_cls}" style="width:{v:.0f}%"></div></div>
    </div>"""


def stat_card(label: str, value, icon_name: str, tone: str = "") -> str:
    tone_cls = f" text-{tone}" if tone else ""
    return f"""
    <div class="mi-card stat-card">
      <span class="stat-label">{label}</span>
      <div class="stat-value">
        <span class="stat-num">{value}</span>
        <span class="stat-icon mi-muted{tone_cls}">{icon(icon_name)}</span>
      </div>
    </div>"""


def state_card(icon_name: str, title: str, text: str, button_html: str = "") -> str:
    return f"""
    <div class="state-card">
      <div class="state-icon">{icon(icon_name, filled=True)}</div>
      <h3>{title}</h3>
      <p>{text}</p>
      {button_html}
    </div>"""


def app_bar_html(active_page: str, tabs: list) -> str:
    tab_html = ""
    for key, label in tabs:
        cls = "app-tab active" if key == active_page else "app-tab"
        tab_html += f'<a class="{cls}" href="?page={key}">{label}</a>'
    return f"""
    <header class="app-bar">
      <div class="app-bar-inner">
        <nav class="app-tabs">{tab_html}</nav>
        <div class="app-bar-right">
          <span class="env-badge">PROD</span>
          <a class="app-icon-btn" href="#" title="Notifications">{icon("notifications")}</a>
          <a class="app-icon-btn" href="#" title="Settings">{icon("settings")}</a>
          <div class="avatar" title="Operator">OI</div>
        </div>
      </div>
    </header>"""


def sidebar_html(active_page: str, record_count: int) -> str:
    nav = [
        ("dashboard", "Dashboard", "dashboard", "search"),
        ("investigations", "Investigations", "security", "search"),
        ("registry", "Merchant Registry", "storefront", "batch"),
        ("rule", "Rule Engine", "rule", "reconcile"),
        ("health", "System Health", "analytics", "quality"),
    ]

    def _active(key: str, page: str) -> bool:
        if key == "investigations":
            return page in ("search", "entity")
        if key == "registry":
            return page == "batch"
        if key == "rule":
            return page == "reconcile"
        if key == "health":
            return page == "quality"
        return False

    items = ""
    for key, label, sym, target in nav:
        is_active = _active(key, active_page)
        cls = "side-item active" if is_active else "side-item"
        filled = "true" if is_active else "false"
        items += (f'<a class="{cls}" href="?page={target}">'
                  f'<span class="material-symbols-outlined" '
                  f'style="font-variation-settings: \'FILL\' {filled}, \'wght\' 500;">{sym}</span>'
                  f'<span>{label}</span></a>')
    return f"""
    <div class="side-brand">
      <h1>Merchant Intelligence</h1>
      <div class="side-records"><span class="side-dot"></span><span>{record_count:,} records</span></div>
      <div class="side-sub">2ISW + NNPC Ecosystem</div>
    </div>
    <nav class="side-nav">{items}</nav>
    <a class="side-btn" href="?page=search">{icon("add")} New Investigation</a>
    <div class="side-foot">
      <a class="side-item" href="#">{icon("help")}<span>Support</span></a>
      <a class="side-item" href="#">{icon("code")}<span>API Docs</span></a>
    </div>"""


def skeleton_table_html(rows: int = 5, show_header: bool = True) -> str:
    head = ""
    if show_header:
        head = ('<div class="mi-table-head">'
                '<span class="sk pill"></span><span class="sk"></span><span class="sk"></span>'
                '<span class="sk"></span><span class="sk"></span><span class="sk"></span><span></span>'
                '</div>')
    body = "".join(f"""
    <div class="skeleton-row">
      <span class="sk circle"></span>
      <span class="sk pill"></span>
      <span class="sk w70"></span>
      <span class="sk w40"></span>
      <span class="sk w40"></span>
      <span class="sk w70"></span>
      <span></span>
    </div>""" for _ in range(rows))
    return f'<div class="mi-table-wrap">{head}{body}</div>'


def skeleton_stat_cards(n: int = 5) -> str:
    cards = "".join(
        '<div class="mi-card stat-card"><span class="sk w40" style="height:10px"></span>'
        '<div class="stat-value"><span class="sk w70" style="height:28px"></span>'
        '<span class="sk circle" style="width:24px;height:24px"></span></div></div>'
        for _ in range(n)
    )
    return f'<div class="stat-row">{cards}</div>'


def gauge_html(percent: float, label: str = "Health Score") -> str:
    pct = max(0.0, min(float(percent), 100.0))
    circ = 282.74
    offset = circ * (1 - pct / 100)
    color = "#2563eb" if pct >= 60 else ("#b45309" if pct >= 35 else "#ba1a1a")
    return f"""
    <div class="gauge">
      <svg width="160" height="160" viewBox="0 0 100 100">
        <circle cx="50" cy="50" r="45" fill="none" stroke="#e6eeff" stroke-width="10"/>
        <circle cx="50" cy="50" r="45" fill="none" stroke="{color}" stroke-width="10"
                stroke-linecap="round" stroke-dasharray="{circ:.2f}" stroke-dashoffset="{offset:.2f}"/>
      </svg>
      <div class="g-label"><span class="g-num">{pct:.0f}%</span><span class="g-sub">{label}</span></div>
    </div>"""
