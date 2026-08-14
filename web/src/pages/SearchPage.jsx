import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import ConfirmButtons from '../components/ConfirmButtons'
import CopyButton from '../components/CopyButton'
import HighlightedPrefix from '../components/HighlightedPrefix'
import KeyMerchantBadge from '../components/KeyMerchantBadge'
import TablePagination from '../components/TablePagination'
import { scoreTone, pillTone } from '../utils/matches'
import { intentIcon, intentLabel, intentTone } from '../utils/intents'
import { exportFilename } from '../utils/exportName'
import { partsOf, sheetOf, sourceOf } from '../utils/source'
import { copyTextToClipboard, rowsCsv, rowTsv, useCopyIndicator } from '../utils/tableClipboard'

const HISTORY_KEY = 'mi.search.history'
const PAGE_SIZE = 20

function loadHistory() {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]')
  } catch {
    return []
  }
}

function saveHistory(h) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(h.slice(0, 8)))
  } catch {
    /* ignore */
  }
}

/* ── Small building blocks ─────────────────────────────────────────────── */

function FieldBar({ label, value, secondary }) {
  const w = Math.max(0, Math.min(100, value))
  return (
    <div className="rounded-lg border border-outline-variant bg-surface-container-low p-3">
      <p className="mb-1 text-[10px] text-outline">{label}</p>
      <div className="flex items-center justify-between">
        <span className="text-lg font-bold">{Math.round(w)}</span>
        <div className="h-1.5 w-16 overflow-hidden rounded-full bg-outline-variant">
          <div
            className={`h-full rounded-full ${secondary ? 'bg-secondary' : 'bg-primary'}`}
            style={{ width: `${w}%` }}
          />
        </div>
      </div>
    </div>
  )
}

const IDENTIFIER_LABELS = {
  phone: 'Phone',
  email: 'Email',
  tid: 'TID',
  mxcode: 'MX Code',
  payable_code: 'Payable Code',
  merchant_id: 'MID',
  account_number: 'Account No.',
}

const SEARCH_EXAMPLES = ['LAGOON WATERS', '08000000000', 'MX183544', 'merchant30@example.com', 'get me all the information on medplus']

/* ── Natural-request detection (frontend gate) ─────────────────────────── */

// Cheap gate used ONLY to decide whether to try the /api/task endpoint first.
// The backend is the source of truth: if it says is_task:false, we fall
// through to a normal search with the same text.
const IDENTIFIER_RE = /(\b\d{4}[A-Z]\d{3}\b|\bMX\d{4,8}\b|\b(?:\+?234|0)[789]\d{9}\b|\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|\b\d{6}\b|\b\d{7}\b|\b\d{10}\b|\b[2-9]\d{10,11}\b|\b2ISW[A-Z0-9]{4,}\b)/i
// Global copy so looksLikeTask can COUNT identifier tokens (2+ = batch resolve).
const IDENTIFIER_RE_G = new RegExp(IDENTIFIER_RE.source, 'gi')
const INSTRUCTION_RE = /\b(pls|please|get|find|lookup|look up|show|give me|use the|then use|extract|retrieve|search for|pull|static account|static acct|beneficiary|mxcode|mx code)\b/i
// "get me all the information on X" — no identifier, but a clear request.
const PROFILE_PHRASE_RE = /\b(all( the)? information|everything about|full profile|details? (of|for|on|about|regarding)|profile of|info on)\b/i
// Ambiguous account/bank/detail phrasing — the backend may ask which
// interpretation the user meant ("account details" could be the profile,
// the static account, or the change history), so it MUST reach the task
// interpreter even without an identifier or a strong collection marker.
const AMBIGUOUS_PHRASE_RE = /\b(account details|acct details|account info|acct info|bank details|bank info|merchant details)\b|\bdetails?\s+(of|for|on|about|regarding)\b/i
// Collection / analytics phrasing (the backend owns the authoritative gate
// and falls back to a normal search when the phrase is not actually a task):
//   "all the addresses of all nnpc stations"  (strong marker + field word)
//   "show me all nnpc station addresses"      (weak 'all' + instruction)
//   "how many nnpc merchants" / "find duplicates" / "summarize the file"
const TASK_MARKER_STRONG = /\b(all the|list of|list all|all of|every|each|how many|find duplicate|duplicates?|summarize|summary|overview|breakdown|top \d+|top ten|most common|ranking|per state|by state)\b/i
const TASK_MARKER_WEAK = /\b(all|count|stats?)\b/i
const TASK_FIELD_WORD = /\b(address(es)?|location(s)?|e[- ]?mail(s)?|phone(s)?|telephone(s)?|tid(s)?|mx ?code(s)?|mxcode(s)?|merchant(s)?|station(s)?|outlet(s)?|store(s)?|branch(es)?|account(s)?|bank(s)?|state(s)?|contact(s)?|onboard(ed|ing)?|alias(es)?|payable(s)?|beneficiar(ies|y)?|source(s)?|file(s)?|information|profile|details?)\b/i
// count/duplicates/summary always carry their intent verb — "count of monte
// cristo" routes to the interpreter but the backend rejects it and falls
// back to a normal name search.
const TASK_INTENT_VERB = /\b(how many|count|duplicates?|summarize|summary|overview|breakdown)\b/i
// The newer analytical intents carry their own unambiguous verbs — no field
// word or identifier needed, so they route straight to the interpreter:
//   "top 10 banks in the NNPC file"   (top)
//   "is 2103O338 in the registry"     (verify)
//   "compare X vs Y"                  (compare)
//   "who else is linked to MX..."     (related)
//   "which nnpc stations have no email" (coverage)
//   "what was JUST CHIPS formerly called" (formerly)
const TASK_ANALYTIC_VERB = /\b(compare|versus|\bvs\b|side[- ]by[- ]side|difference between|verify|in the registry|registered|who else|linked to|related to|associated with|connected to|formerly|renamed|previously known|known as|have no|has no|with no|without|missing|no email|no phone|no address)\b/i
// Field-request pattern: "get me the TID for nnpc apata", "show me the email
// of medplus", "what is the bank on X". The field word between an article
// and a preposition is the OUTPUT the user wants, not a search keyword —
// these MUST reach the task interpreter, which resolves the name through the
// engine and returns the exact field (a plain /api/search would treat "TID"
// as a keyword, match stored TID values, and bury the real record).
const FIELD_REQUEST_RE = /\b(?:the|a|my|your|our|this|that|these|those)\s+(tids?|terminal ids?|e[- ]?mails?|emails?|phones?|telephones?|mobiles?|mx ?codes?|mxcodes?|\bmx\b|addresses?|locations?|banks?|accounts?|accts?|payables?|aliases?|contacts?|serial|bvn|mid|beneficiar(?:ies|y)|settlement|static|merchant ids?|states?|codes?)\s+(?:for|of|on|from|to|about|regarding)\b/i

function looksLikeTask(text) {
  const t = String(text || '').trim()
  if (!t) return false
  if (t.includes('\n')) return true                 // multi-line paste
  // A batch of 2+ identifiers ("2ISW2587 2ISW2586", "MX183544 MX183545")
  // is a batch-resolve request — mirrors the backend's is_task rule: one
  // bare identifier is a normal search, several means "resolve all of
  // these". Without this, the whole string goes to plain /api/search and
  // FTS fuzzy-matches unrelated rows instead of the pasted TIDs' records.
  if ((t.match(IDENTIFIER_RE_G) || []).length >= 2) return true
  const hasIdent = IDENTIFIER_RE.test(t)
  const hasInstr = INSTRUCTION_RE.test(t)
  // A bare identifier alone is a normal search (MX183544); an identifier
  // PLUS an instruction word is a request ("get the static account for MX...").
  if (hasIdent && hasInstr) return true
  // A name-only request ("get me all the information on medplus") also goes
  // through the task interpreter, which resolves the name via the engine.
  if (hasInstr && PROFILE_PHRASE_RE.test(t)) return true
  // Field-request phrasing ("get me the TID for X", "show the email of Y")
  // goes through the task interpreter so the exact field is returned — the
  // backend rejects anything that isn't really a task and we fall through
  // to a normal search.
  if (hasInstr && FIELD_REQUEST_RE.test(t)) return true
  // Ambiguous account/bank/detail phrasing goes through the task interpreter
  // too so the backend can ask which interpretation before running anything.
  if (hasInstr && AMBIGUOUS_PHRASE_RE.test(t)) return true
  // Collection / analytics phrasing goes through the task interpreter too.
  if (TASK_INTENT_VERB.test(t)) return true
  if (TASK_MARKER_STRONG.test(t) && TASK_FIELD_WORD.test(t)) return true
  if (hasInstr && TASK_MARKER_WEAK.test(t) && TASK_FIELD_WORD.test(t)) return true
  // Analytical intents route without needing an identifier or field word —
  // but still require some request signal so plain merchant names ("BANK OF
  // INDUSTRY", "VERIFY ME LOGISTICS") stay normal searches. The backend is
  // the final authority: it rejects anything that isn't really a task.
  if (TASK_ANALYTIC_VERB.test(t) && (hasInstr || hasIdent)) return true
  return false
}

function EmptyState({ icon, title, body }) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-outline-variant bg-surface-container-lowest p-10 text-center shadow-sm">
      <div className="mb-4 flex h-20 w-20 items-center justify-center rounded-full bg-surface-container-high text-outline-variant">
        <span className="msi text-[48px]">{icon}</span>
      </div>
      <h3 className="mb-1 text-lg font-semibold text-on-surface">{title}</h3>
      <p className="max-w-[280px] text-[13px] text-on-surface-variant">{body}</p>
    </div>
  )
}

/* Did-you-mean suggestions rendered as clickable chips. */
function SuggestionChips({ suggestions, onPick }) {
  if (!suggestions?.length) return null
  return (
    <div className="mb-4 flex flex-wrap items-center justify-center gap-2">
      <span className="font-plex text-[11px] font-semibold uppercase tracking-wider text-outline">
        Did you mean:
      </span>
      {suggestions.map((s) => (
        <button
          key={s.query}
          onClick={() => onPick(s.query)}
          title={`Best match: ${s.best_match || ''}`}
          className="rounded-full border border-primary/30 bg-primary/5 px-3 py-1 font-plex text-[11px] font-bold text-primary transition-colors hover:border-primary hover:bg-primary/10"
        >
          {s.query}
          <span className="ml-1 opacity-70">({s.score})</span>
        </button>
      ))}
    </div>
  )
}

/* ── Data-quality badge (build-time quality_score / quality_flags) ─────── */

const QUALITY_FLAG_LABELS = {
  missing_email: 'no email address',
  missing_phone: 'no phone number',
  missing_account: 'no settlement account number',
  missing_address: 'no physical address',
  name_conflict: 'files name this terminal differently',
  shared_identifier: 'identifier shared with an unrelated merchant',
}

function QualityBadge({ score, flags }) {
  if (typeof score !== 'number') return null
  let issues = []
  try {
    issues = JSON.parse(flags || '[]')
  } catch {
    issues = []
  }
  const labels = issues
    .map((f) => QUALITY_FLAG_LABELS[f] || f.replace(/_/g, ' '))
    .join(', ')
  const tone =
    score >= 90
      ? 'border-green-200 bg-green-50 text-green-800'
      : score >= 70
        ? 'border-amber-200 bg-amber-50 text-amber-800'
        : 'border-red-200 bg-red-50 text-red-800'
  return (
    <span
      title={
        labels
          ? `Data quality ${score}/100 — ${labels}`
          : `Data quality ${score}/100 — complete record`
      }
      className={`flex shrink-0 items-center gap-1 rounded-full border px-2 py-1 font-plex text-[10px] font-bold ${tone}`}
    >
      <span className="msi text-[12px]">verified_user</span>
      {score}
    </span>
  )
}

/* ── Result row (collapsible) ──────────────────────────────────────────── */

function ResultRow({ res, index, query, sourceFilter, sheetFilter, onSheetClick, onOpenProfile }) {
  const [open, setOpen] = useState(index === 0)
  const score = res.overall_score / 10
  const fs = res.field_scores || {}
  const { file: srcFile, sheet: srcSheet } = partsOf(res.sheet)
  const order = ['merchant_name', 'slip_header', 'email', 'account_name']
  const bars = order
    .filter((f) => fs[f] !== undefined)
    .map((f) => (
      <FieldBar
        key={f}
        label={f.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
        value={fs[f]}
        secondary={f === 'email'}
      />
    ))

  return (
    <div className="border-b border-outline-variant transition-colors last:border-b-0">
      {/* Main row: score, match type, full merchant name, expand arrow */}
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-4 px-6 py-4 text-left hover:bg-primary/5"
      >
        <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-sm font-bold ${scoreTone(score)}`}>
          {score.toFixed(1)}
        </span>
        <span className="flex shrink-0 items-center gap-1.5">
          <span className={`rounded-full border px-2.5 py-1 text-[11px] font-bold uppercase tracking-tighter ${pillTone(res.match_type)}`}>
            {res.match_type || '—'}
          </span>
          {res.matched_field && (
            <span
              title={`Matched by ${IDENTIFIER_LABELS[res.matched_field] || res.matched_field}: ${res.matched_value || ''}`}
              className="rounded-full border border-primary/25 bg-primary/5 px-2 py-1 text-[10px] font-bold text-primary"
            >
              Found by {IDENTIFIER_LABELS[res.matched_field] || res.matched_field}
            </span>
          )}
          <QualityBadge score={res.quality_score} flags={res.quality_flags} />
          <KeyMerchantBadge roots={res.key_merchants} onOpenProfile={onOpenProfile} />
        </span>
        <span className="min-w-0 flex-1 truncate font-bold text-on-surface">
          {res.merchant_name || '—'}
        </span>
        <span className="shrink-0 text-outline">
          <span className="msi text-[20px]">{open ? 'keyboard_arrow_up' : 'keyboard_arrow_down'}</span>
        </span>
      </button>

      {/* Detail chips row: identifiers, contact, source — always visible, not truncated */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-outline-variant/40 bg-surface-container-low/40 px-6 py-2.5">
        {/* TID */}
        {res.tid && (
          <span className="flex items-center gap-1">
            <span className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">TID</span>
            <span className="font-mono text-xs font-medium text-on-surface">{res.tid}</span>
            <CopyButton value={res.tid} label="TID" />
          </span>
        )}
        {/* MX Code */}
        {res.mxcode && (
          <span className="flex items-center gap-1">
            <span className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">MX</span>
            <span className="font-mono text-xs font-medium text-on-surface">{res.mxcode}</span>
            <CopyButton value={res.mxcode} label="MX code" />
          </span>
        )}
        {/* Email */}
        {res.email && (
          <span className="flex items-center gap-1">
            <span className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">Email</span>
            <span className="text-xs font-medium text-primary">{res.email}</span>
            <CopyButton value={res.email} label="email" />
          </span>
        )}
        {/* Phone */}
        {res.phone && (
          <span className="flex items-center gap-1">
            <span className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">Phone</span>
            <span className="font-mono text-xs font-medium text-on-surface">{res.phone}</span>
            <CopyButton value={res.phone} label="phone" />
          </span>
        )}
        {/* Contact Name */}
        {res.contact_name && (
          <span className="flex items-center gap-1">
            <span className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">Contact</span>
            <span className="text-xs font-medium text-on-surface-variant">{res.contact_name}</span>
            <CopyButton value={res.contact_name} label="contact name" />
          </span>
        )}
        {/* Source file */}
        {srcFile && (
          <span className="flex items-center gap-1">
            <span className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">File</span>
            <span
              title={srcFile}
              className="max-w-[200px] truncate rounded border border-outline-variant bg-surface-container-low px-2 py-0.5 font-plex text-[10px] font-semibold text-on-surface-variant"
            >
              {srcFile}
            </span>
            <CopyButton value={srcFile} label="source file" />
          </span>
        )}
        {/* Sheet (clickable filter) */}
        {srcSheet && (
          <span className="flex items-center gap-1">
            <span className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">Sheet</span>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                e.preventDefault()
                onSheetClick(srcFile, srcSheet)
              }}
              title={
                sourceFilter === srcFile && sheetFilter === srcSheet
                  ? `Clear sheet filter: ${srcSheet}`
                  : `Filter to sheet: ${srcSheet}`
              }
              className={`max-w-[200px] truncate rounded border px-2 py-0.5 font-plex text-[10px] font-semibold transition-colors ${
                sourceFilter === srcFile && sheetFilter === srcSheet
                  ? 'border-primary bg-primary/10 text-primary'
                  : 'border-outline-variant bg-surface-container-low text-on-surface-variant hover:border-primary hover:text-primary'
              }`}
            >
              {srcSheet}
            </button>
            <CopyButton value={srcSheet} label="sheet name" />
          </span>
        )}
        {!srcFile && !srcSheet && res.sheet && (
          <span className="flex items-center gap-1">
            <span className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">Source</span>
            <span title={res.sheet} className="max-w-[200px] truncate rounded border border-outline-variant bg-surface-container-low px-2 py-0.5 font-plex text-[10px] font-semibold text-on-surface-variant">
              {res.sheet}
            </span>
            <CopyButton value={res.sheet} label="source" />
          </span>
        )}
        {/* Onboarded date if present */}
        {res.onboarded_date && (
          <span className="flex items-center gap-1">
            <span className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">Onboarded</span>
            <span className="font-plex text-xs font-medium text-on-surface-variant">
              {String(res.onboarded_date).slice(0, 10)}
            </span>
            <CopyButton value={String(res.onboarded_date).slice(0, 10)} label="onboarded date" />
          </span>
        )}
        {/* Account name if present */}
        {res.account_name && (
          <span className="flex items-center gap-1">
            <span className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">Account</span>
            <span className="text-xs font-medium text-on-surface-variant">{res.account_name}</span>
            <CopyButton value={res.account_name} label="account name" />
          </span>
        )}
      </div>
      {open && (
        <div className="border-b border-outline-variant bg-surface-container-lowest px-6 py-6">
          <div className="flex flex-col gap-8 md:flex-row">
            <div className="flex-1">
              <h4 className="mb-4 font-plex text-[10px] uppercase tracking-wider text-on-surface-variant">
                Why matched? (Deep Analysis)
              </h4>
              <div className="grid grid-cols-2 gap-4 md:grid-cols-3">{bars}</div>
              {res.matched_field && (
                <div className="mt-4 flex items-center gap-3 rounded-lg border border-primary/25 bg-primary/5 px-4 py-3">
                  <span className="msi fill text-primary">verified</span>
                  <div className="flex flex-col">
                    <span className="font-plex text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
                      Matched by {IDENTIFIER_LABELS[res.matched_field] || res.matched_field}
                    </span>
                    <span className="font-mono text-sm font-bold text-primary">
                      {res.matched_value || '—'}
                    </span>
                  </div>
                </div>
              )}
              <p className="mt-3 text-xs text-on-surface-variant">
                Matched tokens: <b>{res.matched_tokens?.join(', ') || '—'}</b> · Sheet:{' '}
                {res.sheet || '—'}
              </p>
            </div>
            <div className="flex w-full flex-col justify-end gap-2 md:w-64">
              {onOpenProfile && (
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    e.preventDefault()
                    onOpenProfile(res.merchant_name)
                  }}
                  className="flex w-full items-center justify-center gap-2 rounded-lg border border-primary/30 bg-primary/5 py-2 font-plex text-[13px] font-bold text-primary transition-colors hover:bg-primary/10"
                >
                  <span className="msi text-[18px]">person_search</span>
                  View full profile
                </button>
              )}
              <ConfirmButtons query={query} merchantName={res.merchant_name} />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/* ── Similar merchants panel ───────────────────────────────────────────── */

function SimilarPanel({ query, onClose, onOpenProfile }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    api.similar(query, 15)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(String(e.message || e)))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [query])

  return (
    <div className="mb-6 overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
      <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
        <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
          <span className="msi fill text-[18px] text-primary">hub</span>
          Similar / Related Merchants
          {data && <span className="rounded-md bg-primary/10 px-2 py-0.5 font-plex text-[10px] font-bold text-primary">{data.count}</span>}
        </h3>
        <button onClick={onClose} className="rounded-full p-1 text-outline transition-colors hover:bg-surface-container hover:text-on-surface">
          <span className="msi text-[18px]">close</span>
        </button>
      </div>
      {loading && (
        <div className="space-y-2 p-5">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-10 animate-pulse rounded-lg bg-surface-container-highest" />
          ))}
        </div>
      )}
      {error && <p className="p-5 text-sm text-error">{error}</p>}
      {!loading && !error && (
        <div className="divide-y divide-outline-variant/60">
          {!data?.similar?.length && (
            <p className="p-5 text-center text-sm text-on-surface-variant">
              No related merchants share identifiers with this record.
            </p>
          )}
          {data?.similar?.map((m, i) => (
            <div key={i} className="flex items-center justify-between gap-4 px-5 py-3 transition-colors hover:bg-surface-container-low/60">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="truncate text-[13px] font-bold text-on-surface">{m.merchant_name}</span>
                  <KeyMerchantBadge roots={m.key_merchants} onOpenProfile={onOpenProfile} />
                </div>
                <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-on-surface-variant">
                  <span className="text-outline">{m.sheet || '—'}</span>
                  {m.email && <span className="text-primary">{m.email}</span>}
                  {m.phone && <span>{m.phone}</span>}
                </div>
              </div>
              {m.link_reasons?.length > 0 && (
                <span className="shrink-0 rounded-full border border-secondary/25 bg-secondary/10 px-2 py-0.5 font-plex text-[10px] font-bold text-secondary">
                  {m.link_reasons.length} link{m.link_reasons.length === 1 ? '' : 's'}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/* ── Natural-request task results panel ───────────────────────────────── */

// Maps the backend's column headers to the actual row keys — the backend
// uses compact keys (mxcode, static_acc_no, sheet) that don't match the
// human-readable headers, so deriving the key from the header alone would
// render those columns blank. Covers every pipeline's column set:
// static_account / field (email·phone·mx) / profile / change_details /
// segment / count / duplicates / summary — plus merged compound tables.
const TASK_COLUMN_KEYS = {
  TID: 'tid',
  Merchant: 'merchant',
  'Merchant Name': 'merchant',             // duplicates pipeline
  'MX Code': 'mxcode',
  'Static Account Number': 'static_acc_no',
  Beneficiary: 'beneficiary',
  'Payable Code': 'payable_code',
  Payable: 'payable',                      // change-details pipeline
  Alias: 'alias',
  Bank: 'bank',
  Status: 'status',
  Identifier: 'identifier',
  Input: 'identifier',                     // related pipeline
  Phone: 'phone',
  Email: 'email',
  'Slip Header': 'slip_header',
  Source: 'sheet',                         // segment overrides below
  Sheet: 'sheet',
  'Account Name': 'account_name',
  'Account Number': 'account_number',
  Contact: 'contact',
  Address: 'address',
  Onboarded: 'onboarded',                  // segment rows; field pipeline uses header text
  Found: 'found',                          // verify pipeline (Yes/No)
  Row: 'row',
  Metric: 'metric',
  Count: 'count',
  Value: 'value',                          // top pipeline (value = ranked bucket)
  Rows: 'rows',                            // duplicates pipeline
  Sources: 'sources',                      // duplicates pipeline
  Field: 'field',                          // compare pipeline
  'Entity A': 'entity_a',                  // compare pipeline
  'Entity B': 'entity_b',                  // compare pipeline
  // change-details old/new pairs: the backend stores these under the exact
  // header text (reconstructed from the sheet's raw_data), not snake_case.
  'Old Bank Acc No': 'Old Bank Acc No',
  'New Bank Acc No': 'New Bank Acc No',
  'Old Bank Code': 'Old Bank Code',
  'New Bank Code': 'New Bank Code',
  'Old Address': 'Old Address',
  'New Address': 'New Address',
  'Old Account Name': 'Old Account Name',
  'New Account Name': 'New Account Name',
  'Current Account': 'current_acc',
  'Current Bank': 'current_bank',
  Changed: 'change_detected',
}

// Headers that are NOT the row key for every pipeline. For everything else
// the row is keyed by the header text itself (field pipelines put the
// resolved value under the column header: row['Bank'], row['Onboarded']…),
// so a missing TASK_COLUMN_KEYS entry falls back to the header text.
const TASK_COLUMN_NOT_HEADER = new Set([
  'Merchant', 'Merchant Name', 'Source', 'Static Account Number',
  'Payable Code', 'Account Name', 'Account Number', 'Slip Header',
])

// Some headers collide across pipelines:
//   'State'  — segment rows carry a real 'state'; the profile pipeline
//              stores the resolved row's STATE under 'bank' (no state key)
//   'Source' — segment rows use 'source'; every other pipeline uses 'sheet'
// The intent-aware pick handles the primary case, and taskCellValue falls
// back to the alternate key per-row so merged compound tables still render
// when the row's origin differs from the primary intent.
function taskRowKey(column, intent) {
  if (column === 'State') return intent === 'profile' ? 'bank' : 'state'
  if (column === 'Source') return intent === 'segment' ? 'source' : 'sheet'
  // Top/ranking pipeline: the ranked-field header is dynamic ('Bank',
  // 'State', …) but every row stores the bucket under 'value'.
  if (intent === 'top' && column !== 'Count') return 'value'
  if (column === 'Count') return 'count'
  return TASK_COLUMN_KEYS[column]
    || column.toLowerCase().replace(/[^a-z0-9]+/g, '_')
}

const TASK_KEY_ALTERNATES = {
  State: ['state', 'bank'],   // profile row inside a merged table
  Source: ['source', 'sheet'],
}

function taskCellValue(row, column, intent) {
  const key = taskRowKey(column, intent)
  let val = row[key]
  if (val === undefined || val === null) {
    // Field pipelines key rows by the column header text itself
    // (row['Bank'], row['Onboarded']…), so try the header verbatim.
    if (!TASK_COLUMN_NOT_HEADER.has(column) && row[column] !== undefined && row[column] !== null) {
      val = row[column]
    }
  }
  if (val === undefined || val === null) {
    for (const alt of TASK_KEY_ALTERNATES[column] || []) {
      if (row[alt] !== undefined && row[alt] !== null) {
        val = row[alt]
        break
      }
    }
  }
  return val
}

// Row keys rendered in mono (identifiers / codes). Everything else is
// proportional text — the old regex (/mx|tid|acc|…/) wrongly put 'Account
// Name', 'Address', 'Current Bank' etc. into mono too.
const TASK_MONO_KEYS = new Set([
  'tid', 'mxcode', 'static_acc_no', 'payable', 'payable_code', 'alias',
  'account_number', 'current_acc', 'bvn', 'merchant_id', 'identifier',
  'row', 'old bank acc no', 'new bank acc no', 'old bank code',
  'new bank code', 'old bank acc', 'new bank acc', 'old account no',
  'new account no',
])

// Long free-text columns wrap instead of truncating so full values stay
// readable (addresses, sources lists, beneficiary names, emails…).
const TASK_WIDE_COLS = new Set([
  'address', 'sources', 'merchant', 'email', 'account_name', 'slip_header',
  'beneficiary', 'bank', 'state', 'current_bank', 'old address',
  'new address', 'old account name', 'new account name', 'contact',
])


const STATUS_TONE = {
  found: 'border-green-200 bg-green-100 text-green-900',
  name_mismatch: 'border-red-200 bg-red-50 text-red-900',
  no_static_account: 'border-orange-200 bg-orange-50 text-orange-900',
  no_name: 'border-slate-200 bg-slate-50 text-slate-700',
  address_match: 'border-sky-200 bg-sky-50 text-sky-900',
}
const STATUS_LABEL = {
  found: 'Found',
  name_mismatch: 'Name mismatch',
  no_static_account: 'No static acct',
  no_name: 'Found',
  address_match: 'Address match',
}

const TASK_PAGE_SIZE = 50

/* ── Clarification card (ambiguous request -> user picks an interpretation) */

function ClarificationCard({ data, onPick, onReset }) {
  const options = data?.options || []
  // "Remember my choice": on by default — picking an interpretation saves
  // the phrase -> intent so the next identical request auto-runs it.
  const [remember, setRemember] = useState(true)
  return (
    <div className="mb-6 animate-fade-in-up overflow-hidden rounded-xl border border-secondary/30 bg-surface-container-lowest shadow-sm">
      <div className="border-b border-outline-variant bg-secondary/10 px-6 py-4">
        <div className="flex items-center gap-2">
          <span className="msi fill text-[20px] text-secondary">help</span>
          <h3 className="text-sm font-bold text-on-surface">Which did you mean?</h3>
          <span className="ml-auto rounded-full border border-secondary/25 bg-secondary/10 px-2.5 py-1 font-plex text-[10px] font-bold uppercase tracking-wider text-secondary">
            <span className="msi align-[-3px] text-[14px]">rule</span> Ambiguous request
          </span>
        </div>
        <p className="mt-2 text-[13px] text-on-surface-variant">{data?.question}</p>
      </div>
      <div className="grid gap-3 p-6 sm:grid-cols-2">
        {options.map((o) => (
          <button
            key={o.intent}
            onClick={() => onPick(o.intent, remember)}
            className="group flex items-start gap-3 rounded-xl border border-outline-variant bg-surface-container-lowest p-4 text-left transition-all hover:border-secondary hover:bg-secondary/5 active:scale-[0.98]"
          >
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-secondary/15 text-secondary">
              <span className="msi text-[18px]">auto_awesome</span>
            </span>
            <span className="min-w-0">
              <span className="flex items-center gap-2 font-plex text-[13px] font-bold text-on-surface">
                {o.label}
                <span
                  title={`Intent: ${String(o.intent).replace(/_/g, ' ')}`}
                  className="rounded border border-outline-variant bg-surface-container px-1.5 py-0.5 font-plex text-[9px] font-semibold uppercase tracking-wider text-on-surface-variant transition-colors group-hover:border-secondary/40 group-hover:text-secondary"
                >
                  {intentLabel(o.intent)}
                </span>
              </span>
              <span className="mt-1 block text-xs text-on-surface-variant">{o.description}</span>
            </span>
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-outline-variant bg-surface-container-low px-6 py-3">
        <label className="flex cursor-pointer items-center gap-2 font-plex text-[11px] text-on-surface-variant transition-colors hover:text-on-surface">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
            className="h-4 w-4 rounded border-outline-variant accent-secondary"
          />
          <span className="msi text-[16px] text-secondary">bookmark</span>
          Remember my choice — run this automatically next time
        </label>
        <button
          onClick={onReset}
          className="flex items-center gap-1.5 rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-1.5 font-plex text-xs font-bold text-on-surface-variant transition-colors hover:border-primary hover:text-primary"
        >
          <span className="msi text-[15px]">close</span>
          Clear
        </button>
      </div>
    </div>
  )
}

function TaskResultsPanel({ task, onExport, onReset, exporting, onSuggestion, onOpenProfile }) {
  const cols = task?.columns || []
  const rows = task?.rows || []
  const notFound = task?.not_found || []
  const intent = task?.detected?.intent || task?.intent || 'task'
  const intents = task?.detected?.intents || [intent]
  const pipeline = task?.pipeline || []
  const suggestions = task?.suggestions || []
  // Pagination keeps 1000+ row segment results light on the DOM.
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(TASK_PAGE_SIZE)
  const { copied, indicate } = useCopyIndicator()

  // A new task result resets pagination (and any stale copy label).
  useEffect(() => {
    setPage(0)
  }, [task])

  const cellOf = (r, h) => taskCellValue(r, h, intent)

  async function copyAll() {
    if (await copyTextToClipboard(rowsCsv(rows, cols, cellOf))) indicate('all')
  }

  async function copyRow(row, i) {
    if (await copyTextToClipboard(rowTsv(cols, (h) => taskCellValue(row, h, intent)))) indicate(`row-${i}`)
  }

  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize))
  const safePage = Math.min(page, pageCount - 1)
  const pageRows = rows.slice(safePage * pageSize, safePage * pageSize + pageSize)
  return (
    <div className="mb-6 animate-fade-in-up">
      {/* Intent + summary card */}
      <div className="mb-4 rounded-xl border border-outline-variant bg-surface-container-lowest p-5 shadow-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-primary/25 bg-primary/10 px-2.5 py-1 font-plex text-[10px] font-bold uppercase tracking-wider text-primary">
            <span className="msi align-[-3px] text-[14px]">auto_awesome</span> Pasted request
          </span>
          {intents.map((i) => (
            <span
              key={i}
              title={`Intent: ${String(i).replace(/_/g, ' ')}`}
              className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-plex text-[10px] font-bold uppercase tracking-wider ${intentTone(i)}`}
            >
              <span className="msi align-[-3px] text-[14px]">{intentIcon(i)}</span>
              {intentLabel(i)}
            </span>
          ))}
          {pipeline.map((step) => (
            <span key={step} className="rounded-full border border-outline-variant bg-surface-container-low px-2.5 py-1 font-plex text-[10px] font-bold text-on-surface-variant">
              step: {step.replace(/_/g, ' ')}
            </span>
          ))}
          {task?.detected?.llm_refined && (
            <span className="rounded-full border border-secondary/30 bg-secondary/10 px-2.5 py-1 font-plex text-[10px] font-bold uppercase tracking-wider text-secondary" title="The engine used the configured LLM to pin down this request">
              <span className="msi align-[-3px] text-[14px]">auto_awesome</span> LLM refined
            </span>
          )}
          {task?.used_preference && (
            <span className="rounded-full border border-secondary/40 bg-secondary/15 px-2.5 py-1 font-plex text-[10px] font-bold uppercase tracking-wider text-secondary" title="You picked this interpretation before and asked the app to remember it — this request auto-ran it.">
              <span className="msi align-[-3px] text-[14px]">bookmark</span>
              Using saved choice: {intentLabel(task.used_preference)}
            </span>
          )}
          <span className="ml-auto text-xs text-on-surface-variant">{rows.length} result{rows.length === 1 ? '' : 's'}</span>
        </div>
        <p className="mt-3 text-[13px] text-on-surface-variant">{task?.summary}</p>
      </div>

      {/* Results table */}
      {rows.length > 0 ? (
        <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
          <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container px-6 py-3.5">
            <span className="font-plex text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">
              Task results
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={copyAll}
                disabled={rows.length === 0}
                className="flex items-center gap-1.5 rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-1.5 font-plex text-[11px] font-bold text-on-surface-variant transition-all hover:border-primary hover:text-primary active:scale-95 disabled:opacity-40"
              >
                <span className="msi text-[15px]">{copied === 'all' ? 'check' : 'content_copy'}</span>
                {copied === 'all' ? 'Copied!' : 'Copy all'}
              </button>
              <button
                onClick={onExport}
                disabled={exporting}
                className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 font-plex text-[11px] font-bold text-on-primary transition-all hover:opacity-90 active:scale-95 disabled:opacity-40"
              >
                <span className="msi text-[15px]">download</span>
                {exporting ? 'Building…' : 'Export Excel'}
              </button>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-outline-variant bg-surface-container px-6 py-3 font-plex text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">
                  {cols.map((c) => (
                    <th key={c} className="px-6 py-3">{c}</th>
                  ))}
                  <th className="px-6 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((r, i) => {
                  const merchant = taskCellValue(r, 'Merchant', intent)
                  const absRow = safePage * pageSize + i
                  return (
                  <tr key={absRow} className="border-b border-outline-variant/60 transition-colors last:border-b-0 hover:bg-primary/5">
                    {cols.map((c) => {
                      const key = taskRowKey(c, intent)
                      const val = taskCellValue(r, c, intent)
                      if (key === 'status') {
                        return (
                          <td key={c} className="px-6 py-3">
                            <span className={`rounded-full border px-2 py-0.5 font-plex text-[10px] font-bold uppercase tracking-tighter ${STATUS_TONE[val] || 'border-slate-200 bg-slate-50 text-slate-700'}`}>
                              {STATUS_LABEL[val] || val?.replace(/_/g, ' ') || '—'}
                            </span>
                          </td>
                        )
                      }
                      if (key === 'change_detected') {
                        return (
                          <td key={c} className="px-6 py-3">
                            <span className={`rounded-full border px-2 py-0.5 font-plex text-[10px] font-bold uppercase tracking-tighter ${
                              val
                                ? 'border-green-200 bg-green-100 text-green-900'
                                : 'border-slate-200 bg-slate-50 text-slate-600'
                            }`}>
                              {val ? 'Yes' : 'No'}
                            </span>
                          </td>
                        )
                      }
                      if (key === 'found') {
                        const yes = String(val).toLowerCase() === 'yes'
                        return (
                          <td key={c} className="px-6 py-3">
                            <span className={`rounded-full border px-2 py-0.5 font-plex text-[10px] font-bold uppercase tracking-tighter ${
                              yes
                                ? 'border-green-200 bg-green-100 text-green-900'
                                : 'border-red-200 bg-red-50 text-red-900'
                            }`}>
                              {yes ? 'Found' : 'Not found'}
                            </span>
                          </td>
                        )
                      }
                      const mono = TASK_MONO_KEYS.has(key) || TASK_MONO_KEYS.has(key.toLowerCase())
                      const wide = TASK_WIDE_COLS.has(key.toLowerCase())
                      const numeric = key === 'count' || key === 'value' || key === 'rows'
                      return (
                        <td key={c} className={`px-6 py-3 ${wide ? 'max-w-[340px]' : ''}`}>
                          <span
                            className={`flex items-center gap-1 ${
                              mono ? 'font-mono text-xs text-on-surface-variant' : 'text-[13px] font-medium text-on-surface'
                            } ${wide ? 'whitespace-normal break-words' : ''}`}
                          >
                            {numeric && val !== undefined && val !== null && val !== '' ? (
                              <b className="text-[15px] font-extrabold text-primary">{val}</b>
                            ) : (val || '—')}
                            {val && <CopyButton value={val} label={c} />}
                          </span>
                        </td>
                      )
                    })}
                    <td className="px-6 py-3 text-right">
                      <div className="flex items-center justify-end gap-1">
                        {merchant && onOpenProfile && (
                          <button
                            onClick={() => onOpenProfile(merchant)}
                            title={`Open full profile: ${merchant}`}
                            className="flex h-7 w-7 items-center justify-center rounded-lg border border-primary/25 bg-primary/5 text-primary transition-colors hover:bg-primary/15"
                          >
                            <span className="msi text-[16px]">person_search</span>
                          </button>
                        )}
                        <button
                          onClick={() => copyRow(r, absRow)}
                          title="Copy row (headers + values, tab-separated)"
                          className="flex h-7 w-7 items-center justify-center rounded-lg border border-outline-variant bg-surface-container-lowest text-on-surface-variant transition-colors hover:border-primary hover:text-primary"
                        >
                          <span className="msi text-[16px]">{copied === `row-${absRow}` ? 'check' : 'content_copy'}</span>
                        </button>
                      </div>
                    </td>
                  </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <TablePagination
            total={rows.length}
            page={page}
            setPage={setPage}
            pageSize={pageSize}
            setPageSize={setPageSize}
          />
        </div>
      ) : (
        <div className="rounded-xl border border-outline-variant bg-surface-container-lowest p-8 text-center shadow-sm">
          <p className="text-sm text-on-surface-variant">No rows produced by this request.</p>
        </div>
      )}

      {/* Not-found identifiers */}
      {notFound.length > 0 && (
        <div className="mt-4 rounded-xl border border-error/20 bg-error-container/20 p-4">
          <p className="mb-2 font-plex text-[11px] font-bold uppercase tracking-wider text-error">
            Could not resolve ({notFound.length})
          </p>
          <div className="flex flex-wrap gap-2">
            {notFound.map((nf, i) => (
              <span key={i} className="rounded-lg border border-error/25 bg-surface-container-lowest px-2 py-1 font-mono text-[11px] text-error" title={nf.reason}>
                {nf.id}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Auto-suggested next steps */}
      {suggestions.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="font-plex text-[11px] font-semibold uppercase tracking-wider text-outline">
            Also get:
          </span>
          {suggestions.map((s) => (
            <button
              key={s.intent}
              onClick={() => onSuggestion(s.prompt)}
              className="flex items-center gap-1.5 rounded-full border border-secondary/30 bg-secondary/10 px-3 py-1 font-plex text-[11px] font-bold text-secondary transition-colors hover:border-secondary hover:bg-secondary/20"
            >
              <span className="msi text-[14px]">add</span>
              {s.label}
            </button>
          ))}
        </div>
      )}

      {/* Reset */}
      <div className="mt-4 flex justify-center">
        <button
          onClick={onReset}
          className="flex items-center gap-1.5 rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-1.5 font-plex text-xs font-bold text-on-surface-variant transition-colors hover:border-primary hover:text-primary"
        >
          <span className="msi text-[15px]">close</span>
          Clear request view
        </button>
      </div>
    </div>
  )
}

/* ── Search page ───────────────────────────────────────────────────────── */

export default function SearchPage({ onOpenProfile }) {
  const [query, setQuery] = useState('')
  const [searched, setSearched] = useState('')
  const [results, setResults] = useState([])
  const [total, setTotal] = useState(0)
  const [limit, setLimit] = useState(PAGE_SIZE)
  const [elapsed, setElapsed] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [history, setHistory] = useState(loadHistory)
  const [showHistory, setShowHistory] = useState(false)
  // Live typeahead (autocomplete) state — suggestions from the normalized
  // name_buckets table, populated while the user types.
  const inputRef = useRef(null)
  // Suppresses the next debounced autocomplete fetch after a search was just
  // run from a suggestion/history click. Without this, the input keeps focus
  // (mousedown preventDefault) and the 180ms debounce would reopen the
  // dropdown over the fresh results. Cleared on the next keystroke.
  const suppressAcRef = useRef(false)
  const [acItems, setAcItems] = useState([])   // suggestion list from the API
  const [showAc, setShowAc] = useState(false)  // dropdown visible
  const [acIndex, setAcIndex] = useState(-1)   // keyboard-highlighted item
  const [showSimilar, setShowSimilar] = useState(false)
  const [exporting, setExporting] = useState(false)
  // Natural-request task state: when the pasted text is detected as a task
  // (multi-line identifiers + instruction), the results render inline here
  // instead of the normal search table.
  const [taskResult, setTaskResult] = useState(null)
  const [taskExporting, setTaskExporting] = useState(false)
  const [taskLoading, setTaskLoading] = useState(false)
  // Clarification state: the backend read the request as ambiguous ("account
  // details" could be profile / static account / change history) and asks
  // which one. Picking an option re-runs the task with that intent forced.
  const [clarification, setClarification] = useState(null)
  // Filters are initialized straight from the URL so the very first render
  // already carries any shared/bookmarked file/sheet params. This avoids a
  // sync-effect transient that would otherwise wipe them before the search
  // completes (a refresh during that window would lose the params).
  const [sourceFilter, setSourceFilter] = useState(
    () => new URLSearchParams(window.location.search).get('file'), // null = All files
  )
  const [sheetFilter, setSheetFilter] = useState(
    () => new URLSearchParams(window.location.search).get('sheet'), // null = All sheets
  )

  // Restore the query from the URL on first load so a filtered view can be
  // shared / bookmarked.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const q = params.get('q')
    if (q) {
      setQuery(q)
      // A shared/bookmarked pasted-request URL must be re-interpreted as a
      // task, not searched as one giant name query.
      if (looksLikeTask(q)) runTask(q)
      else runSearch(q, false) // keep any filters initialized from the URL
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Keep the URL in sync so a filtered view can be shared / bookmarked.
  useEffect(() => {
    const url = new URL(window.location.href)
    if (searched) url.searchParams.set('q', searched)
    // q is never deleted: searched only transitions '' -> non-empty (no
    // clear-search path exists), so it always reflects the latest query.
    if (sourceFilter) url.searchParams.set('file', sourceFilter)
    else url.searchParams.delete('file')
    if (sheetFilter) url.searchParams.set('sheet', sheetFilter)
    else url.searchParams.delete('sheet')
    window.history.replaceState({}, '', url)
  }, [searched, sourceFilter, sheetFilter])

  // Live typeahead: debounced so every keystroke doesn't hammer the API.
  // Only shows once the input has focus AND at least 2 characters — short
  // fragments would flood the bucket LIKE query with noise.
  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) {
      setAcItems([])
      setShowAc(false)
      return
    }
    const t = setTimeout(() => {
      // A search was just triggered from the dropdown (or history) — the
      // input kept focus, so skip this fetch or the list would pop back open
      // over the results. Also drop any stale items so a later refocus can't
      // resurrect suggestions outdated for the new query.
      if (suppressAcRef.current) {
        suppressAcRef.current = false
        setAcItems([])
        return
      }
      api.autocomplete(q)
        .then((d) => {
          // If the input lost focus, or a search ran while this request was
          // in flight, don't pop the dropdown back open — and drop the
          // stale items so a later refocus can't resurrect them.
          if (suppressAcRef.current || document.activeElement !== inputRef.current) {
            setAcItems([])
            return
          }
          const items = d.suggestions || []
          setAcItems(items)
          setShowAc(items.length > 0)
          setAcIndex(-1)
        })
        .catch(() => {
          setAcItems([])
          setShowAc(false)
        })
    }, 180)
    return () => clearTimeout(t)
  }, [query])

  function pickAc(s) {
    setQuery(s)
    setShowAc(false)
    setAcIndex(-1)
    runSearch(s)
  }

  // Keyboard navigation for the autocomplete dropdown. Without a highlighted
  // item, Enter falls through to the normal form submit (search what's typed).
  function handleAcKeyDown(e) {
    if (!showAc || acItems.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setAcIndex((i) => (i + 1) % acItems.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setAcIndex((i) => (i <= 0 ? acItems.length - 1 : i - 1))
    } else if (e.key === 'Enter' && acIndex >= 0 && acItems[acIndex]) {
      e.preventDefault()
      pickAc(acItems[acIndex])
    } else if (e.key === 'Escape') {
      setShowAc(false)
      setAcIndex(-1)
    }
  }

  async function runTask(text) {
    setTaskLoading(true)
    setError('')
    setShowSimilar(false)
    setShowAc(false)
    setAcIndex(-1)
    suppressAcRef.current = true
    try {
      const data = await api.task(text)
      if (data?.needs_clarification) {
        // Ambiguous request — no pipeline ran. Show the question + options;
        // the user picks one and we re-run with that intent forced.
        setTaskResult(null)
        setClarification(data)
        setResults([])
        setTotal(0)
        setSearched(text)
        setSuggestions([])
        setHistory((prev) => {
          const next = [text, ...prev.filter((x) => x.toLowerCase() !== text.toLowerCase())]
          saveHistory(next)
          return next.slice(0, 8)
        })
      } else if (data?.is_task) {
        setClarification(null)
        setTaskResult(data)
        setResults([])      // task view replaces the normal table
        setTotal(0)
        setSearched(text)
        setSuggestions([])
        // Push into search history like a normal query.
        setHistory((prev) => {
          const next = [text, ...prev.filter((x) => x.toLowerCase() !== text.toLowerCase())]
          saveHistory(next)
          return next.slice(0, 8)
        })
      } else {
        // Backend says it's not a task — fall through to a normal search.
        setTaskResult(null)
        setClarification(null)
        await runSearch(text, true)
      }
    } catch (e) {
      // Task interpretation failed — fall back to a normal search (runSearch
      // clears any stale error itself).
      setTaskResult(null)
      setClarification(null)
      await runSearch(text, true)
    } finally {
      setTaskLoading(false)
    }
  }

  // The user picked one interpretation from a clarification prompt — re-run
  // the exact request with that intent forced on the backend. `remember`
  // saves the phrase -> intent so next time it auto-runs (no card).
  async function runTaskWithIntent(intent, remember = false) {
    if (!searched) return
    setTaskLoading(true)
    setClarification(null)
    setError('')
    try {
      const data = await api.task(searched, intent, remember)
      if (data?.needs_clarification) {
        // Rare: the forced intent still read as ambiguous — show the card again.
        setClarification(data)
      } else if (data?.is_task) {
        setTaskResult(data)
        setResults([])
        setTotal(0)
      } else {
        setTaskResult(null)
        await runSearch(searched, true)
      }
    } catch (e) {
      setTaskResult(null)
      await runSearch(searched, true)
    } finally {
      setTaskLoading(false)
    }
  }

  async function handleTaskExport() {
    if (!taskResult || !searched) return
    setTaskExporting(true)
    try {
      const res = await api.exportTask(searched)
      if (!res.ok) throw new Error('Export failed')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const detected = taskResult?.detected || {}
      const firstMerchant = (taskResult?.rows || []).find((r) => r.merchant)?.merchant || ''
      const base = String(
        detected?.key_merchants?.[0] || firstMerchant || detected?.intent || taskResult?.intent || 'task'
      )
      const suffix = String(detected?.intent || taskResult?.intent || 'results')
      a.download = exportFilename(base, suffix, 'Task')
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setTaskExporting(false)
    }
  }

  function resetTaskView() {
    setTaskResult(null)
    setClarification(null)
    setResults([])
    setTotal(0)
    setSearched('')
    setQuery('')
    setSourceFilter(null)
    setSheetFilter(null)
  }

  async function runSearch(q, resetFilters = true, pageSize = PAGE_SIZE) {
    setLoading(true)
    setError('')
    setShowSimilar(false)
    setShowAc(false)
    setAcIndex(-1)
    suppressAcRef.current = true // don't reopen the dropdown for this query
    try {
      const t0 = performance.now()
      const data = await api.search(q, pageSize, 0)
      setResults(data.results || [])
      setTotal(data.total || 0)
      setLimit(pageSize)
      if (resetFilters) {
        setSourceFilter(null)
        setSheetFilter(null)
      }
      setElapsed(performance.now() - t0)
      setSearched(q)
      // Did-you-mean: only when the result set is thin (fewer than 3 hits).
      if ((data.results || []).length < 3) {
        api.suggest(q).then((d) => setSuggestions(d.suggestions || [])).catch(() => setSuggestions([]))
      } else {
        setSuggestions([])
      }
      // Push into local search history (most recent first, deduped).
      setHistory((prev) => {
        const next = [q, ...prev.filter((x) => x.toLowerCase() !== q.toLowerCase())]
        saveHistory(next)
        return next.slice(0, 8)
      })
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setLoading(false)
    }
  }

  function onSubmit(e) {
    e.preventDefault()
    const t = query.trim()
    if (!t) return
    if (looksLikeTask(t)) {
      runTask(t) // pasted block / instruction — try the task interpreter first
    } else {
      runSearch(t)
    }
  }

  function loadMore() {
    if (!searched) return
    // API caps the limit at 100 (pydantic le=100) — never exceed it.
    runSearch(searched, false, Math.min(limit + PAGE_SIZE, 100))
  }

  // Clicking a Sheet cell in the table filters to that exact file + sheet
  // (matching the contextual chip behaviour); clicking the active cell clears.
  function handleSheetClick(file, sheet) {
    if (sourceFilter === file && sheetFilter === sheet) {
      setSourceFilter(null)
      setSheetFilter(null)
    } else {
      setSourceFilter(file)
      setSheetFilter(sheet)
    }
  }

  async function handleExport() {
    if (!searched) return
    setExporting(true)
    try {
      // The API caps the export limit at 100 — request the full cap.
      const res = await api.exportSearch(searched, 100, 0)
      if (!res.ok) throw new Error('Export failed')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = exportFilename(searched, 'search', 'Search')
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setExporting(false)
    }
  }

  // File chips are derived from ALL results; sheet chips are derived from the
  // file-filtered subset (contextual), so picking a file narrows the sheets.
  const sources = useMemo(() => {
    const counts = {}
    for (const r of results) {
      const s = sourceOf(r.sheet) || 'Unknown'
      counts[s] = (counts[s] || 0) + 1
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1])
  }, [results])

  const fileBase = useMemo(() => {
    if (sourceFilter === null) return results
    return results.filter((r) => (sourceOf(r.sheet) || 'Unknown') === sourceFilter)
  }, [results, sourceFilter])

  const sheets = useMemo(() => {
    const counts = {}
    for (const r of fileBase) {
      const s = sheetOf(r.sheet) || 'Unknown'
      counts[s] = (counts[s] || 0) + 1
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1])
  }, [fileBase])

  const visible = results.filter((r) => {
    if (sourceFilter !== null && (sourceOf(r.sheet) || 'Unknown') !== sourceFilter) return false
    if (sheetFilter !== null && (sheetOf(r.sheet) || 'Unknown') !== sheetFilter) return false
    return true
  })

  return (
    <>
      <div className="mb-6">
        <h1 className="text-[28px] font-extrabold tracking-tight">Search</h1>
        <p className="mt-1 text-sm text-on-surface-variant">
          Single merchant lookup with confidence scoring &amp; deep analysis.
        </p>
      </div>

      {/* Search bar */}
      <form onSubmit={onSubmit} className="relative mx-auto mb-4 max-w-2xl">
        <div className="relative">
          <span className="pointer-events-none absolute inset-y-0 left-4 flex items-center text-outline">
            <span className="msi text-[24px]">search</span>
          </span>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              suppressAcRef.current = false // typing re-enables autocomplete
              setQuery(e.target.value)
            }}
            onFocus={() => {
              // Empty query -> recent searches; non-empty -> live typeahead
              if (!query.trim()) setShowHistory(true)
              else if (acItems.length) setShowAc(true)
            }}
            onBlur={() => {
              setTimeout(() => {
                setShowHistory(false)
                setShowAc(false)
                setAcIndex(-1)
              }, 150)
            }}
            onKeyDown={handleAcKeyDown}
            placeholder="Search by name, phone, email, TID or MX code — e.g. 08000000000"
            className="w-full rounded-2xl border border-outline-variant bg-surface-container-lowest py-4 pl-12 pr-24 text-base shadow-sm outline-none transition-all focus:border-primary focus:ring-4 focus:ring-primary/20"
          />
          <div className="absolute inset-y-0 right-4 flex items-center gap-2">
            <span className="rounded border border-outline-variant bg-surface-container px-1.5 py-0.5 font-plex text-xs text-outline">
              ⌘K
            </span>
            <button
              type="submit"
              disabled={loading}
              className="rounded-xl bg-primary px-4 py-2 font-plex text-[13px] font-bold text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {loading ? 'Searching…' : 'Search'}
            </button>
          </div>
        </div>

        {/* Recent searches dropdown — only when the box is empty */}
        {showHistory && !query.trim() && history.length > 0 && (
          <div className="absolute left-0 right-0 top-[calc(100%+6px)] z-30 overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-xl animate-fade-in-up">
            <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-4 py-2">
              <span className="font-plex text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
                Recent searches
              </span>
              <button
                type="button"
                onClick={() => {
                  setHistory([])
                  saveHistory([])
                }}
                className="font-plex text-[10px] font-semibold text-outline transition-colors hover:text-error"
              >
                Clear
              </button>
            </div>
            {history.map((h) => (
              <button
                key={h}
                type="button"
                onMouseDown={(e) => {
                  // preventDefault keeps focus so blur doesn't hide first
                  e.preventDefault()
                  setShowHistory(false)
                  setQuery(h)
                  runSearch(h)
                }}
                className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-[13px] text-on-surface transition-colors hover:bg-surface-container"
              >
                <span className="msi text-[16px] text-outline">history</span>
                <span className="truncate">{h}</span>
              </button>
            ))}
          </div>
        )}

        {/* Live autocomplete dropdown */}
        {showAc && acItems.length > 0 && (
          <div className="absolute left-0 right-0 top-[calc(100%+6px)] z-30 overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-xl animate-fade-in-up">
            <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-4 py-2">
              <span className="font-plex text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
                Suggestions
              </span>
              <span className="font-plex text-[10px] text-outline">{acItems.length}</span>
            </div>
            {acItems.map((s, i) => (
              <button
                key={s}
                type="button"
                onMouseDown={(e) => {
                  // preventDefault keeps focus so blur doesn't hide the list
                  // before the click registers
                  e.preventDefault()
                  pickAc(s)
                }}
                onMouseEnter={() => setAcIndex(i)}
                className={`flex w-full items-center gap-3 px-4 py-2.5 text-left text-[13px] transition-colors ${
                  i === acIndex
                    ? 'bg-surface-container text-primary'
                    : 'text-on-surface hover:bg-surface-container'
                }`}
              >
                <span className="msi text-[16px] text-outline">auto_awesome</span>
                <span className="truncate font-semibold">
                  <HighlightedPrefix text={s} prefix={query} />
                </span>
                <span className="ml-auto font-plex text-[10px] text-outline">↵</span>
              </button>
            ))}
            <div className="flex items-center gap-3 border-t border-outline-variant bg-surface-container-low px-4 py-1.5">
              <span className="font-plex text-[10px] text-outline">
                <b>↑↓</b> navigate · <b>↵</b> search · <b>esc</b> close
              </span>
            </div>
          </div>
        )}
      </form>

      {/* Paste-a-request hint (always visible under the search bar) */}
      <div className="mb-4 flex items-center justify-center gap-2 text-center">
        <span className="msi text-[15px] text-secondary">bolt</span>
        <p className="font-plex text-xs text-on-surface-variant">
          You can also <b className="font-bold text-on-surface">paste a request</b> — e.g. a list of
          terminal codes plus{' '}
          <button
            type="button"
            onClick={() => {
              const example =
                '2103O338\tFELIX OKONMAH\n2103O340\tADEBOWALE FESOMADE\nGet the MX code for these merchants, then use the MX code to get their static account and beneficiary name from the static acct manager'
              setQuery(example)
              runTask(example)
            }}
            className="rounded border border-secondary/30 bg-secondary/10 px-1.5 py-0.5 font-plex text-[11px] font-bold text-secondary transition-colors hover:bg-secondary/20"
          >
            “get the MX codes + static accounts for these”
          </button>
        </p>
      </div>

      {/* Task loading skeleton */}
      {taskLoading && (
        <div className="mb-6 animate-pulse space-y-3 rounded-xl border border-outline-variant bg-surface-container-lowest p-5 shadow-sm">
          <div className="h-6 w-64 rounded bg-surface-container-highest" />
          <div className="h-4 w-full rounded bg-surface-container-highest" />
          <div className="h-4 w-3/4 rounded bg-surface-container-highest" />
        </div>
      )}

      {/* Clarification card (ambiguous request — no pipeline ran yet) */}
      {clarification && !taskLoading && !taskResult && (
        <ClarificationCard
          data={clarification}
          onPick={(intent, remember) => runTaskWithIntent(intent, remember)}
          onReset={resetTaskView}
        />
      )}

      {/* Task results (pasted-request interpretation) */}
      {taskResult && !taskLoading && (
        <TaskResultsPanel
          task={taskResult}
          exporting={taskExporting}
          onExport={handleTaskExport}
          onReset={resetTaskView}
          onOpenProfile={onOpenProfile}
          onSuggestion={(prompt) => {
            setQuery(prompt)
            runTask(prompt)
          }}
        />
      )}

      {/* Search-by-anything chips */}
      {!searched && !loading && !taskLoading && !taskResult && (
        <div className="mb-4 flex flex-wrap items-center justify-center gap-2">
          <span className="font-plex text-[11px] font-semibold uppercase tracking-wider text-outline">
            Search by:
          </span>
          {SEARCH_EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => {
                setQuery(ex)
                runSearch(ex)
              }}
              className="rounded-full border border-outline-variant bg-surface-container-lowest px-3 py-1 font-plex text-[11px] font-semibold text-on-surface-variant transition-colors hover:border-primary hover:text-primary"
            >
              {ex}
            </button>
          ))}
        </div>
      )}

      {/* Did-you-mean suggestions */}
      {searched && !loading && !taskResult && suggestions.length > 0 && (
        <SuggestionChips suggestions={suggestions} onPick={(q) => runSearch(q)} />
      )}

      {/* Stats line */}
      {searched && !loading && !taskResult && !clarification && (
        <div className="mb-4 flex items-center justify-center gap-4 text-on-surface-variant">
          <span className="font-plex text-[13px]">
            Query: <strong className="text-on-surface">{searched}</strong>
          </span>
          <span className="h-1 w-1 rounded-full bg-outline-variant" />
          <span className="font-plex text-[13px]">
            <strong className="text-on-surface">{visible.length}</strong> matches
            {total > limit && <span className="text-outline"> of {total}</span>}
          </span>
          <span className="h-1 w-1 rounded-full bg-outline-variant" />
          <span className="font-plex text-[13px] text-outline">{Math.round(elapsed)}ms response time</span>
          <span className="h-1 w-1 rounded-full bg-outline-variant" />
          <button
            onClick={handleExport}
            disabled={exporting || results.length === 0}
            className="flex items-center gap-1.5 rounded-lg border border-outline-variant bg-surface-container-lowest px-2.5 py-1 font-plex text-[11px] font-bold text-on-surface-variant transition-colors hover:border-primary hover:text-primary disabled:opacity-40"
          >
            <span className="msi text-[15px]">download</span>
            {exporting ? 'Exporting…' : 'Export'}
          </button>
        </div>
      )}

      {/* Loading skeleton */}
      {loading && (
        <div className="animate-pulse overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="flex items-center gap-4 border-b border-outline-variant px-6 py-5 last:border-b-0">
              <div className="h-10 w-10 rounded-lg bg-surface-container-highest" />
              <div className="h-4 flex-1 rounded bg-surface-container-highest" />
              <div className="h-4 w-24 rounded bg-surface-container-highest" />
              <div className="h-4 w-16 rounded bg-surface-container-highest" />
            </div>
          ))}
        </div>
      )}

      {error && (
        <div className="rounded-xl border border-error/20 bg-error-container/30 p-5 text-center">
          <p className="font-plex text-sm font-semibold text-error">{error}</p>
        </div>
      )}

      {/* Empty / no results */}
      {!loading && !searched && !error && (
        <EmptyState
          icon="manage_search"
          title="No query yet"
          body="Try searching for a merchant name or Merchant ID (TID) to start your investigation."
        />
      )}
      {!loading && searched && results.length === 0 && !error && !taskResult && !clarification && (
        <EmptyState
          icon="search_off"
          title="No match found"
          body={`No records matched '${searched}'. Check the spelling or try using fewer filters.`}
        />
      )}

      {/* Similar merchants panel (toggled from the table footer) */}
      {showSimilar && <SimilarPanel query={searched} onClose={() => setShowSimilar(false)} onOpenProfile={onOpenProfile} />}

      {/* Results table */}
      {!loading && results.length > 0 && (
        <>
          {/* Source file filter chips */}
          {sources.length > 1 && (
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <span className="font-plex text-[11px] font-semibold uppercase tracking-wider text-outline">
                File:
              </span>
              <button
                onClick={() => {
                  setSourceFilter(null)
                  setSheetFilter(null)
                }}
                className={`rounded-full border px-3 py-1 font-plex text-[11px] font-semibold transition-colors ${
                  sourceFilter === null
                    ? 'border-primary bg-primary text-on-primary'
                    : 'border-outline-variant bg-surface-container-lowest text-on-surface-variant hover:border-primary hover:text-primary'
                }`}
              >
                All <span className="opacity-70">({results.length})</span>
              </button>
              {sources.map(([src, n]) => (
                <button
                  key={src}
                  onClick={() => {
                    setSourceFilter(sourceFilter === src ? null : src)
                    setSheetFilter(null) // sheets are contextual to the file
                  }}
                  className={`rounded-full border px-3 py-1 font-plex text-[11px] font-semibold transition-colors ${
                    sourceFilter === src
                      ? 'border-primary bg-primary text-on-primary'
                      : 'border-outline-variant bg-surface-container-lowest text-on-surface-variant hover:border-primary hover:text-primary'
                  }`}
                >
                  {src} <span className="opacity-70">({n})</span>
                </button>
              ))}
            </div>
          )}

          {/* Sheet filter chips (contextual to selected file) */}
          {sheets.length > 1 && (
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <span className="font-plex text-[11px] font-semibold uppercase tracking-wider text-outline">
                Sheet:
              </span>
              <button
                onClick={() => setSheetFilter(null)}
                className={`rounded-full border px-3 py-1 font-plex text-[11px] font-semibold transition-colors ${
                  sheetFilter === null
                    ? 'border-primary bg-primary text-on-primary'
                    : 'border-outline-variant bg-surface-container-lowest text-on-surface-variant hover:border-primary hover:text-primary'
                }`}
              >
                All <span className="opacity-70">({fileBase.length})</span>
              </button>
              {sheets.map(([sh, n]) => (
                <button
                  key={sh}
                  onClick={() => setSheetFilter(sheetFilter === sh ? null : sh)}
                  className={`rounded-full border px-3 py-1 font-plex text-[11px] font-semibold transition-colors ${
                    sheetFilter === sh
                      ? 'border-primary bg-primary text-on-primary'
                      : 'border-outline-variant bg-surface-container-lowest text-on-surface-variant hover:border-primary hover:text-primary'
                  }`}
                >
                  {sh} <span className="opacity-70">({n})</span>
                </button>
              ))}
            </div>
          )}

          <div className="animate-fade-in-up overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
            <div className="flex items-center gap-4 border-b border-outline-variant bg-surface-container px-6 py-3 font-plex text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">
              <span className="w-[52px] shrink-0">Score</span>
              <span className="w-[140px] shrink-0">Match Type</span>
              <span className="flex-1">Merchant Name</span>
              <span className="w-5 shrink-0"></span>
            </div>
            {visible.map((res, i) => (
              <ResultRow
                key={res.id || i}
                res={res}
                index={i}
                query={searched}
                sourceFilter={sourceFilter}
                sheetFilter={sheetFilter}
                onSheetClick={handleSheetClick}
                onOpenProfile={onOpenProfile}
              />
            ))}
            <div className="flex items-center justify-between bg-surface-container-low px-6 py-3.5 text-xs text-on-surface-variant">
              <span>
                Showing {visible.length} of {total || results.length} records
                {sourceFilter !== null && (
                  <span className="ml-1 text-outline">
                    · file <b>{sourceFilter}</b>
                  </span>
                )}
                {sheetFilter !== null && (
                  <span className="ml-1 text-outline">
                    · sheet <b>{sheetFilter}</b>
                  </span>
                )}
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setShowSimilar((s) => !s)}
                  className="flex items-center gap-1.5 rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-1.5 text-xs font-bold transition-colors hover:border-primary hover:text-primary"
                >
                  <span className="msi text-[15px]">hub</span>
                  Similar merchants
                </button>
                {visible.length < total && limit < 100 && (
                  <button
                    onClick={loadMore}
                    className="rounded-lg border border-primary bg-primary px-3 py-1.5 text-xs font-bold text-on-primary transition-opacity hover:opacity-90"
                  >
                    Load more ({Math.min(total - visible.length, 100 - limit)} more)
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* Bento: recent activity + insights */}
          <div className="mt-6 grid grid-cols-1 gap-6 pb-8 md:grid-cols-3">
            <div className="rounded-xl border border-outline-variant bg-white p-4 shadow-sm">
              <div className="mb-2 flex items-center gap-3">
                <span className="msi fill text-primary">history</span>
                <h3 className="text-sm font-bold">Recent Activity</h3>
              </div>
              <ul className="space-y-3">
                <li className="flex items-center justify-between">
                  <span className="text-xs text-on-surface">Searched "{searched}"</span>
                  <span className="text-[10px] text-outline">now</span>
                </li>
                {history.slice(0, 3).map((h, i) => (
                  <li key={h} className="flex items-center justify-between">
                    <button
                      onClick={() => {
                        setQuery(h)
                        runSearch(h)
                      }}
                      className="max-w-[220px] truncate text-xs text-on-surface-variant transition-colors hover:text-primary"
                    >
                      {h}
                    </button>
                    <span className="text-[10px] text-outline">{i === 0 ? 'earlier' : ''}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="relative col-span-2 overflow-hidden rounded-xl border border-outline-variant bg-white p-4 shadow-sm">
              <div className="relative z-10 mb-2 flex items-center gap-3">
                <span className="msi fill text-secondary">auto_awesome</span>
                <h3 className="text-sm font-bold">System Insights</h3>
              </div>
              <p className="relative z-10 text-xs text-on-surface-variant">
                Search engine is live with compound expansion, phonetic matching and
                alias auto-learning enabled across the <b>2ISW + NNPC</b> ecosystem.
                Load more results with the button below the table, and confirm matches
                to teach the alias engine for next time.
              </p>
              <div className="absolute -bottom-5 -right-5 opacity-10">
                <span className="msi text-[120px] text-primary">analytics</span>
              </div>
            </div>
          </div>
        </>
      )}
    </>
  )
}
