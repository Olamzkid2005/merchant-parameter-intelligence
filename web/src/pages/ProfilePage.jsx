import { useEffect, useState } from 'react'
import { api } from '../api'
import CopyButton from '../components/CopyButton'
import MerchantAutocomplete from '../components/MerchantAutocomplete'
import RelationshipNetwork from '../components/RelationshipNetwork'
import TablePagination from '../components/TablePagination'
import { scoreTone, pillTone } from '../utils/matches'
import { partsOf } from '../utils/source'
import { rowTsv, rowsCsv, copyTextToClipboard, useCopyIndicator } from '../utils/tableClipboard'

/* ── Linked-records table shape ────────────────────────────────────────── */
const MEMBER_HEADERS = [
  'Merchant Name', 'TID', 'MX Code', 'Email', 'Bank', 'State', 'MCC',
  'Settlement', 'LGA', 'Phone', 'Account Name', 'Slip Header', 'Contact',
  'Address', 'Merchant ID', 'Payable Code', 'Onboarded', 'Sheet', 'Linked By',
]
function memberCell(r, h) {
  const map = {
    'Merchant Name': r.merchant_name, TID: r.tid, 'MX Code': r.mxcode, Email: r.email,
    Bank: r.bank, State: r.state, MCC: r.merchant_category_code,
    Settlement: r.settlement_type, LGA: r.lga,
    Phone: r.phone, 'Account Name': r.account_name, 'Slip Header': r.slip_header,
    Contact: r.contact_name, Address: r.address, 'Merchant ID': r.merchant_id,
    'Payable Code': r.payable_code, Onboarded: r.onboarded_date, Sheet: r.sheet_name,
    'Linked By': (r.link_reasons || []).join('; '),
  }
  return map[h]
}

const PROFILE_EXAMPLES = ['merchant30@example.com', 'LAGOON WATERS', 'MX183544', '08000000000']
const COMPARE_EXAMPLES = [
  ['LAGOON WATERS', 'MX183544'],
  ['THE FILM HOUSE', 'MONEYTRUST'],
  ['ARTEE INDUSTRIES', 'MONEYTRUST MICROFINANACE BANK LTD'],
]

function EmptyState({ icon, title, body }) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-outline-variant bg-surface-container-lowest p-10 text-center shadow-sm">
      <div className="mb-4 flex h-20 w-20 items-center justify-center rounded-full bg-surface-container-high text-outline-variant">
        <span className="msi text-[48px]">{icon}</span>
      </div>
      <h3 className="mb-1 text-lg font-semibold text-on-surface">{title}</h3>
      <p className="max-w-[320px] text-[13px] text-on-surface-variant">{body}</p>
    </div>
  )
}

/* ── Identity value chip ───────────────────────────────────────────────── */
function IdentityChip({ item, label }) {
  return (
    <div className="group flex items-center gap-2 rounded-lg border border-outline-variant bg-surface-container-low px-3 py-2 transition-colors hover:border-primary/40">
      <span className="min-w-0 flex-1">
        <span className="block truncate font-mono text-[13px] font-semibold text-on-surface">
          {item.value || item.canonical}
        </span>
        <span className="flex items-center gap-1.5 text-[10px] text-outline">
          <span className="rounded bg-surface-container-high px-1 font-plex font-bold text-on-surface-variant">
            ×{item.count}
          </span>
          {item.names?.length > 0 && (
            <span className="truncate">{item.names.slice(0, 2).join(', ')}{item.names.length > 2 ? '…' : ''}</span>
          )}
        </span>
      </span>
      <CopyButton value={item.value || item.canonical} label={label} />
    </div>
  )
}

/* ── Identity card (one per field: Emails, Phones, TIDs…) ─────────────── */
function IdentityCard({ field }) {
  return (
    <div className="flex flex-col rounded-xl border border-outline-variant bg-surface-container-lowest p-4 shadow-sm animate-fade-in-up">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="msi fill text-[18px] text-primary">{field.icon}</span>
          <h4 className="text-[13px] font-bold text-on-surface">{field.label}</h4>
        </div>
        <span className="rounded-md bg-primary/10 px-2 py-0.5 font-plex text-[10px] font-bold text-primary">
          {field.total}
        </span>
      </div>
      <div className="flex flex-col gap-2">
        {field.values.slice(0, 12).map((v, i) => (
          <IdentityChip key={`${v.canonical}-${i}`} item={v} label={field.label} />
        ))}
        {field.total > 12 && (
          <p className="pt-1 text-center font-plex text-[10px] text-outline">
            +{field.total - 12} more…
          </p>
        )}
      </div>
    </div>
  )
}

/* ── Source chip ───────────────────────────────────────────────────────── */
function SourceChip({ src }) {
  const { file, sheet } = partsOf(src.sheet)
  return (
    <div className="flex items-center gap-2 rounded-full border border-outline-variant bg-surface-container-lowest px-3 py-1.5">
      <span className="msi text-[14px] text-outline">description</span>
      <span className="font-plex text-[11px] font-semibold text-on-surface-variant">
        {file ? `${file} · ` : ''}{sheet || src.sheet}
      </span>
      <span className="rounded-full bg-surface-container-high px-1.5 font-plex text-[10px] font-bold text-on-surface-variant">
        {src.count}
      </span>
    </div>
  )
}

/* ── Family member row (expandable) ────────────────────────────────────── */
function MemberRow({ m, index, copiedKey, onCopyRow }) {
  const [open, setOpen] = useState(index === 0)
  return (
    <div className="border-b border-outline-variant transition-colors last:border-b-0">
      {/* The row toggle is a div (role=button) rather than a <button> because
          the MX / Email cells embed CopyButton, which renders a <button> —
          nesting a button inside a button is invalid HTML and triggers the
          React validateDOMNesting warning. */}
      <div
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setOpen(!open)
          }
        }}
        className="grid w-full cursor-pointer grid-cols-[1.4fr_120px_105px_120px_1fr_70px_55px_85px_80px_1fr_32px] items-center gap-3 px-6 py-4 text-left hover:bg-primary/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
      >
        <span className="truncate font-bold text-on-surface">{m.merchant_name || '—'}</span>
        <span className="font-mono text-xs text-outline">{m.tid || '—'}</span>
        <span className="flex items-center gap-1">
          <span className="text-xs font-medium text-on-surface-variant">{m.mxcode || '—'}</span>
          <CopyButton value={m.mxcode} label="MX code" />
        </span>
        <span className="flex items-center gap-1">
          <span className="truncate text-xs font-medium text-on-surface">{m.email || '—'}</span>
          <CopyButton value={m.email} label="email" />
        </span>
        <span className="truncate text-xs text-on-surface-variant" title={m.bank || ''}>{m.bank || '—'}</span>
        <span className="truncate text-xs text-on-surface-variant">{m.state || '—'}</span>
        <span className="font-mono text-[11px] text-on-surface-variant">{m.merchant_category_code || '—'}</span>
        <span className="truncate text-xs text-on-surface-variant">{m.settlement_type || '—'}</span>
        <span className="truncate text-xs text-on-surface-variant">{m.lga || '—'}</span>
        <span className="flex flex-wrap items-center gap-1">
          {m.link_reasons?.length > 0 ? (
            m.link_reasons.slice(0, 2).map((r, i) => (
              <span
                key={i}
                title={r}
                className="max-w-[160px] truncate rounded border border-secondary/25 bg-secondary/10 px-1.5 py-0.5 font-plex text-[10px] font-semibold text-secondary"
              >
                {r}
              </span>
            ))
          ) : (
            <span className="text-[11px] text-outline">seed record</span>
          )}
        </span>
        <span className="flex items-center justify-self-end gap-1 text-outline">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onCopyRow(m, index)
            }}
            title="Copy this row as tab-separated text (pastes into Excel columns)"
            className="flex h-7 w-7 items-center justify-center rounded-lg text-on-surface-variant transition-colors hover:bg-surface-container hover:text-primary"
          >
            <span className="msi text-[18px]">{copiedKey === `row-${index}` ? 'check' : 'content_copy'}</span>
          </button>
          <span className="msi text-[20px]">{open ? 'keyboard_arrow_up' : 'keyboard_arrow_down'}</span>
        </span>
      </div>
      {open && (
        <div className="border-b border-outline-variant bg-surface-container-lowest px-6 py-5">
          <button
            type="button"
            onClick={() => onCopyRow(m, index)}
            title="Copy this row as tab-separated text (pastes into Excel columns)"
            className="mb-4 flex items-center gap-1.5 rounded-lg border border-primary/25 bg-primary/5 px-3 py-1.5 font-plex text-[11px] font-bold text-primary transition-all hover:bg-primary/10 active:scale-95"
          >
            <span className="msi text-[15px]">{copiedKey === `row-${index}` ? 'check' : 'content_copy'}</span>
            {copiedKey === `row-${index}` ? 'Copied!' : 'Copy row'}
          </button>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {[
            ['Slip Header', m.slip_header],
            ['Account Name', m.account_name],
            ['Contact', m.contact_name],
            ['Phone', m.phone],
            ['Address', m.address],
            ['Bank', m.bank],
            ['State', m.state],
            ['MCC', m.merchant_category_code],
            ['Settlement', m.settlement_type],
            ['LGA', m.lga],
            ['Merchant ID', m.merchant_id],
            ['Payable Code', m.payable_code],
            ['Onboarded', m.onboarded_date],
            ['Sheet', m.sheet_name],
          ].map(([label, val]) =>
            val ? (
              <div key={label}>
                <p className="mb-0.5 font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">
                  {label}
                </p>
                <p className="truncate text-[13px] font-medium text-on-surface">{val}</p>
              </div>
            ) : null,
          )}
          </div>
        </div>
      )}
    </div>
  )
}

/* ── Compare verdict banner ────────────────────────────────────────────── */
const STATUS_META = {
  match: { tone: 'bg-green-100 text-green-900 border-green-200', label: 'Match', icon: 'check_circle' },
  overlap: { tone: 'bg-amber-100 text-amber-900 border-amber-200', label: 'Partial', icon: 'compare_arrows' },
  only_a: { tone: 'bg-blue-50 text-blue-900 border-blue-200', label: 'Only A', icon: 'chevron_right' },
  only_b: { tone: 'bg-blue-50 text-blue-900 border-blue-200', label: 'Only B', icon: 'chevron_left' },
  differ: { tone: 'bg-slate-100 text-slate-800 border-slate-200', label: 'Different', icon: 'block' },
}

/* ── Compare column: compact profile for one side ─────────────────────── */
function CompareColumn({ data, label, highlight }) {
  const seed = data?.seed
  const score = seed ? seed.overall_score / 10 : 0
  const identity = data?.identity || {}
  const identityFields = Object.values(identity)
  return (
    <div
      className={`flex flex-col gap-4 rounded-xl border bg-surface-container-lowest p-5 shadow-sm animate-fade-in-up ${
        highlight ? 'border-secondary ring-2 ring-secondary/30' : 'border-outline-variant'
      }`}
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="rounded-full bg-primary px-2.5 py-0.5 font-plex text-[10px] font-bold uppercase tracking-wider text-on-primary">
          {label}
        </span>
        {seed ? (
          <span className={`rounded-lg px-2 py-1 text-xs font-bold ${scoreTone(score)}`}>{score.toFixed(1)}</span>
        ) : null}
      </div>
      {seed ? (
        <div>
          <h4 className="text-base font-extrabold leading-tight text-on-surface">{seed.merchant_name || '—'}</h4>
          <p className="mt-1 flex items-center gap-2 text-[11px] text-on-surface-variant">
            <span className={`rounded-full border px-2 py-0.5 font-plex text-[10px] font-bold uppercase tracking-tighter ${pillTone(seed.match_type)}`}>
              {seed.match_type || '—'}
            </span>
            <span>{data.family_count || 0} rows</span>
          </p>
        </div>
      ) : (
        <p className="text-sm font-medium text-error">No match found for this side</p>
      )}

      {/* Identity summary — the top 5 fields, compact */}
      {identityFields.length > 0 && (
        <div className="flex flex-col gap-2">
          {identityFields.slice(0, 5).map((f) => (
            <div key={f.label} className="flex items-start gap-2">
              <span className="msi fill mt-0.5 text-[16px] text-primary">{f.icon}</span>
              <div className="min-w-0 flex-1">
                <p className="font-plex text-[9px] font-semibold uppercase tracking-wider text-outline">{f.label}</p>
                <p className="truncate font-mono text-[12px] font-semibold text-on-surface">
                  {f.values[0]?.value || '—'}
                  {f.total > 1 ? ` +${f.total - 1}` : ''}
                </p>
              </div>
              <CopyButton value={f.values[0]?.value} label={f.label} />
            </div>
          ))}
        </div>
      )}

      {/* Name variants */}
      {data.name_variants?.length > 1 && (
        <div>
          <p className="mb-1.5 font-plex text-[9px] font-semibold uppercase tracking-wider text-outline">
            Name variants ({data.name_variants.length})
          </p>
          <div className="flex flex-wrap gap-1">
            {data.name_variants.slice(0, 4).map((v) => (
              <span
                key={v.name}
                className="rounded-full border border-outline-variant bg-surface-container-low px-2 py-0.5 text-[10px] font-semibold text-on-surface-variant"
              >
                {v.name}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Sources */}
      {data.sources?.length > 0 && (
        <div>
          <p className="mb-1.5 font-plex text-[9px] font-semibold uppercase tracking-wider text-outline">
            Appears in {data.sources.length} source(s)
          </p>
          <div className="flex flex-wrap gap-1">
            {data.sources.slice(0, 4).map((s, i) => {
              const { file, sheet } = partsOf(s.sheet)
              return (
                <span
                  key={`${s.sheet}-${i}`}
                  className="rounded-full border border-outline-variant bg-surface-container-low px-2 py-0.5 text-[10px] text-on-surface-variant"
                >
                  {file ? `${file}·` : ''}{sheet || s.sheet}
                </span>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

/* ── Profile page ──────────────────────────────────────────────────────── */
export default function ProfilePage({ onOpenGraph }) {
  const [mode, setMode] = useState('single') // 'single' | 'compare'

  // single mode
  const [query, setQuery] = useState('')
  const [searched, setSearched] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // compare mode
  const [queryA, setQueryA] = useState('')
  const [queryB, setQueryB] = useState('')
  const [compareData, setCompareData] = useState(null)
  const [compareLoading, setCompareLoading] = useState(false)
  const [compareError, setCompareError] = useState('')

  // Restore from URL on first load (deep-linkable).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const q = params.get('q')
    const a = params.get('a')
    const b = params.get('b')
    if (a && b) {
      setMode('compare')
      setQueryA(a)
      setQueryB(b)
      runCompare(a, b)
    } else if (q) {
      setQuery(q)
      runProfile(q)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function runProfile(q) {
    setLoading(true)
    setError('')
    try {
      const d = await api.profile(q)
      setData(d)
      setSearched(q)
      const url = new URL(window.location.href)
      url.searchParams.set('q', q)
      url.searchParams.delete('a')
      url.searchParams.delete('b')
      window.history.replaceState({}, '', url)
    } catch (e) {
      setError(String(e.message || e))
      setData(null)
    } finally {
      setLoading(false)
    }
  }

  async function runCompare(a, b) {
    setCompareLoading(true)
    setCompareError('')
    try {
      // cap each side's family at 100 — the diff table/columns don't need
      // 200 rows each and it keeps the compare response snappy
      const d = await api.compare(a, b, 100)
      setCompareData(d)
      const url = new URL(window.location.href)
      url.searchParams.set('a', a)
      url.searchParams.set('b', b)
      url.searchParams.delete('q')
      window.history.replaceState({}, '', url)
    } catch (e) {
      setCompareError(String(e.message || e))
      setCompareData(null)
    } finally {
      setCompareLoading(false)
    }
  }

  function swapSides() {
    const tmp = queryA
    setQueryA(queryB)
    setQueryB(tmp)
    if (queryB && queryA) runCompare(queryB, queryA)
  }

  function switchMode(m) {
    setMode(m)
    if (m === 'single') {
      const url = new URL(window.location.href)
      url.searchParams.delete('a')
      url.searchParams.delete('b')
      window.history.replaceState({}, '', url)
    }
  }

  const seed = data?.seed
  const score = seed ? seed.overall_score / 10 : 0
  const identity = data?.identity || {}
  const identityFields = Object.values(identity)

  // earliest onboarding date across the family (MONTH OF REQUEST column)
  const onboardedValues = (identity.onboarded_date?.values || [])
    .map((v) => v.value)
    .filter(Boolean)
    .sort()
  const earliestOnboarded = onboardedValues[0]

  // linked-records pagination + copy
  const [memberPage, setMemberPage] = useState(0)
  const [memberPageSize, setMemberPageSize] = useState(50)
  const { copied, indicate } = useCopyIndicator()

  // reset to page 0 whenever a new profile is loaded
  useEffect(() => {
    setMemberPage(0)
  }, [data])

  const members = data?.members || []
  const memberPageCount = Math.max(1, Math.ceil(members.length / memberPageSize))
  const safeMemberPage = Math.min(memberPage, memberPageCount - 1)
  const pageMembers = members.slice(
    safeMemberPage * memberPageSize,
    (safeMemberPage + 1) * memberPageSize,
  )

  async function copyAllMembers() {
    if (members.length === 0) return
    if (await copyTextToClipboard(rowsCsv(members, MEMBER_HEADERS, memberCell))) indicate('all')
  }

  async function copyMemberRow(m, i) {
    if (await copyTextToClipboard(rowTsv(MEMBER_HEADERS, (h) => memberCell(m, h)))) indicate(`row-${i}`)
  }

  // investigation brief (single mode)
  const [brief, setBrief] = useState(null)
  const [briefLoading, setBriefLoading] = useState(false)
  const [briefError, setBriefError] = useState('')

  async function runBrief(q) {
    setBriefLoading(true)
    setBriefError('')
    try {
      const d = await api.brief(q)
      setBrief(d)
    } catch (e) {
      setBriefError(String(e.message || e))
      setBrief(null)
    } finally {
      setBriefLoading(false)
    }
  }

  // terminal timeline (build-time merchant_events): first/last seen dates,
  // every name variant with its source file, and old->new account changes
  // parsed from the Change-of-details sheet.
  const [timeline, setTimeline] = useState(null)
  const [timelineLoading, setTimelineLoading] = useState(false)

  useEffect(() => {
    if (!searched) return
    let cancelled = false
    setTimelineLoading(true)
    setTimeline(null)
    api.timeline(searched)
      .then((d) => {
        if (!cancelled) setTimeline(d)
      })
      .catch(() => {
        if (!cancelled) setTimeline(null) // timeline is a nicety — never break the page
      })
      .finally(() => {
        if (!cancelled) setTimelineLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [searched])

  // compare-mode derived values
  const sharedCount = compareData
    ? Object.values(compareData.shared || {}).reduce((n, vals) => n + vals.length, 0)
    : 0
  const sharedTypes = compareData ? Object.keys(compareData.shared || {}).length : 0
  const nameShared = compareData?.name_overlap?.length || 0
  const strongCount = compareData?.strong_count || 0
  // Verdict tiers:
  //   high   — identical rows, >=2 strong identifiers, or the same seed
  //            name (e.g. SPAR -> ARTEE via alias)
  //   mid    — exactly one strong identifier shared (worth investigating)
  //   none   — nothing shared
  const sameEntity = compareData
    ? compareData.overlap_count > 0 ||
      (compareData.a?.found && compareData.b?.found &&
        (strongCount >= 2 || compareData.seed_names_equal || nameShared > 0))
    : false
  const maybeRelated = compareData
    ? !sameEntity && compareData.a?.found && compareData.b?.found && strongCount === 1
    : false

  return (
    <>
      <div className="mb-6">
        <h1 className="text-[28px] font-extrabold tracking-tight">Merchant Profile</h1>
        <p className="mt-1 text-sm text-on-surface-variant">
          Search with any fragment — name, email, phone, TID or MX code — and see everything the registry knows about that merchant in one view.
        </p>

        {/* Mode toggle */}
        <div className="mt-4 inline-flex overflow-hidden rounded-full border border-outline-variant bg-surface-container-lowest shadow-sm">
          <button
            type="button"
            onClick={() => switchMode('single')}
            className={`flex items-center gap-2 px-4 py-2 font-plex text-[12px] font-bold transition-colors ${
              mode === 'single' ? 'bg-primary text-on-primary' : 'text-on-surface-variant hover:bg-surface-container'
            }`}
          >
            <span className="msi text-[16px]">person</span>
            Single
          </button>
          <button
            type="button"
            onClick={() => switchMode('compare')}
            className={`flex items-center gap-2 px-4 py-2 font-plex text-[12px] font-bold transition-colors ${
              mode === 'compare' ? 'bg-primary text-on-primary' : 'text-on-surface-variant hover:bg-surface-container'
            }`}
          >
            <span className="msi text-[16px]">compare</span>
            Compare
          </button>
        </div>
      </div>

      {mode === 'single' ? (
        /* ─────────────────────────── SINGLE MODE ─────────────────────────── */
        <>
          {/* Search bar */}
          <div className="relative mx-auto mb-4 max-w-2xl">
            <MerchantAutocomplete
              value={query}
              onChange={setQuery}
              onSearch={runProfile}
              placeholder="Search by name, email, phone, TID or MX code…"
            />
          </div>

          {!searched && !loading && (
            <div className="mb-4 flex flex-wrap items-center justify-center gap-2">
              <span className="font-plex text-[11px] font-semibold uppercase tracking-wider text-outline">Try:</span>
              {PROFILE_EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  onClick={() => {
                    setQuery(ex)
                    runProfile(ex)
                  }}
                  className="rounded-full border border-outline-variant bg-surface-container-lowest px-3 py-1 font-plex text-[11px] font-semibold text-on-surface-variant transition-colors hover:border-primary hover:text-primary"
                >
                  {ex}
                </button>
              ))}
            </div>
          )}

          {loading && (
            <div className="animate-pulse space-y-4">
              <div className="h-28 rounded-xl bg-surface-container-high" />
              <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
                {[...Array(6)].map((_, i) => (
                  <div key={i} className="h-32 rounded-xl bg-surface-container-high" />
                ))}
              </div>
            </div>
          )}

          {error && (
            <div className="rounded-xl border border-error/20 bg-error-container/30 p-5 text-center">
              <p className="font-plex text-sm font-semibold text-error">{error}</p>
            </div>
          )}

          {!loading && !searched && !error && (
            <EmptyState
              icon="person_search"
              title="Search for a merchant"
              body="Type any fragment you have — an email, phone number, MX code, TID or partial name — and we'll pull together everything we know."
            />
          )}

          {!loading && searched && data && !data.found && !error && (
            <EmptyState
              icon="search_off"
              title="Nothing found"
              body={`No records matched '${searched}'. Check the spelling or try a different fragment.`}
            />
          )}

          {!loading && data?.found && (
            <div className="animate-fade-in-up space-y-6 pb-8">
              {/* Investigation brief — natural-language dossier of this merchant */}
              <section>
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <span className="msi fill text-primary">auto_awesome</span>
                  <h3 className="text-base font-bold text-on-surface">Investigation brief</h3>
                  <span className="font-plex text-[11px] text-outline">
                    natural-language dossier — LLM when configured, offline template otherwise
                  </span>
                  <button
                    type="button"
                    onClick={() => runBrief(searched)}
                    disabled={briefLoading}
                    className="ml-auto flex items-center gap-1.5 rounded-lg border border-primary/30 bg-primary/5 px-3 py-1.5 font-plex text-[12px] font-bold text-primary transition-all hover:bg-primary/10 active:scale-95 disabled:opacity-50"
                  >
                    <span className="msi text-[16px]">{briefLoading ? 'hourglass_top' : 'summarize'}</span>
                    {briefLoading ? 'Writing…' : brief ? 'Regenerate' : 'Generate brief'}
                  </button>
                </div>
                {briefLoading && (
                  <div className="animate-pulse rounded-xl border border-outline-variant bg-surface-container-lowest p-5 shadow-sm">
                    <div className="h-4 w-2/3 rounded bg-surface-container-high" />
                    <div className="mt-3 h-4 w-full rounded bg-surface-container-high" />
                    <div className="mt-2 h-4 w-5/6 rounded bg-surface-container-high" />
                    <div className="mt-2 h-4 w-4/6 rounded bg-surface-container-high" />
                  </div>
                )}
                {briefError && !briefLoading && (
                  <div className="rounded-xl border border-error/20 bg-error-container/30 p-4">
                    <p className="font-plex text-[13px] font-semibold text-error">Brief failed: {briefError}</p>
                  </div>
                )}
                {brief && !briefLoading && !briefError && (
                  <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
                    <div className="flex items-center gap-2 border-b border-outline-variant bg-surface-container-low px-5 py-3">
                      <span className={`rounded-full px-2 py-0.5 font-plex text-[10px] font-bold uppercase tracking-wider ${
                        brief.mode === 'llm' ? 'bg-primary/15 text-primary' : 'bg-surface-container-high text-on-surface-variant'
                      }`}>
                        {brief.mode === 'llm' ? `LLM · ${brief.model || ''}` : 'Offline template'}
                      </span>
                      <span className="font-plex text-[10px] text-outline">{brief.elapsed_ms}ms</span>
                      {!brief.llm_configured && (
                        <span className="font-plex text-[10px] text-outline" title="Set LLM_API_KEY / LLM_BASE_URL / LLM_MODEL env vars to enable LLM briefs">
                          Set LLM_API_KEY for richer briefs
                        </span>
                      )}
                    </div>
                    <div className="space-y-2 px-5 py-4 text-[13px] leading-relaxed text-on-surface">
                      {brief.brief.split(/\n+/).filter(Boolean).map((p, i) => (
                        <p key={i}>{p}</p>
                      ))}
                    </div>
                  </div>
                )}
              </section>

              {/* Hero card */}
              <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
                <div className="flex flex-col gap-4 border-b border-outline-variant bg-surface-container-low px-6 py-5 md:flex-row md:items-center md:justify-between">
                  <div className="flex items-center gap-4">
                    <span className={`flex h-14 w-14 items-center justify-center rounded-xl text-lg font-bold ${scoreTone(score)}`}>
                      {score.toFixed(1)}
                    </span>
                    <div>
                      <div className="flex items-center gap-2">
                        <h2 className="text-xl font-extrabold tracking-tight text-on-surface">
                          {seed?.merchant_name || data.query}
                        </h2>
                        <span className={`rounded-full border px-2 py-1 text-[11px] font-bold uppercase tracking-tighter ${pillTone(seed?.match_type)}`}>
                          {seed?.match_type || '—'}
                        </span>
                      </div>
                      <p className="mt-0.5 flex items-center gap-2 text-xs text-on-surface-variant">
                        <span>Query: <b className="text-on-surface">{data.query}</b></span>
                        <span className="h-1 w-1 rounded-full bg-outline-variant" />
                        <span>{data.elapsed_ms}ms</span>
                        {seed?.matched_field && (
                          <>
                            <span className="h-1 w-1 rounded-full bg-outline-variant" />
                            <span className="text-primary">
                              Matched by {seed.matched_field}: {seed.matched_value}
                            </span>
                          </>
                        )}
                        {earliestOnboarded && (
                          <>
                            <span className="h-1 w-1 rounded-full bg-outline-variant" />
                            <span className="flex items-center gap-1 text-secondary">
                              <span className="msi text-[14px]">event</span>
                              Onboarded {earliestOnboarded}
                            </span>
                          </>
                        )}
                      </p>
                    </div>
                  </div>
                  <div className="flex gap-6">
                    <div className="text-center">
                      <p className="text-2xl font-extrabold text-primary">{data.family_count}</p>
                      <p className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">Linked rows</p>
                    </div>
                    <div className="text-center">
                      <p className="text-2xl font-extrabold text-primary">{identityFields.length}</p>
                      <p className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">Data fields</p>
                    </div>
                    <div className="text-center">
                      <p className="text-2xl font-extrabold text-primary">{data.sources?.length || 0}</p>
                      <p className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">Sources</p>
                    </div>
                  </div>
                </div>
              </div>

              {/* Relationship network — how the linked records connect */}
              {data.members?.length > 1 && (
                <section>
                  <div className="mb-3 flex flex-wrap items-center gap-2">
                    <span className="msi fill text-primary">hub</span>
                    <h3 className="text-base font-bold text-on-surface">Relationship network</h3>
                    <span className="font-plex text-[11px] text-outline">
                      how these {data.family_count} records connect through shared identifiers
                    </span>
                    <button
                      onClick={() => onOpenGraph?.(seed?.merchant_name || data.query)}
                      title="Open the full entity graph for this merchant"
                      className="ml-auto flex items-center gap-1.5 rounded-lg border border-primary/30 bg-primary/5 px-3 py-1.5 font-plex text-[12px] font-bold text-primary transition-all hover:bg-primary/10 active:scale-95"
                    >
                      <span className="msi text-[16px]">account_tree</span>
                      Open in Entity Graph
                    </button>
                  </div>
                  <RelationshipNetwork seed={seed} members={data.members} />
                </section>
              )}

              {/* Terminal timeline — build-time derived history */}
              {(timelineLoading || timeline?.terminals?.length > 0) && (
                <section>
                  <div className="mb-3 flex items-center gap-2">
                    <span className="msi fill text-primary">timeline</span>
                    <h3 className="text-base font-bold text-on-surface">Terminal timeline</h3>
                    <span className="font-plex text-[11px] text-outline">
                      first &amp; last trace, every name this terminal has carried, and account changes
                    </span>
                  </div>
                  {timelineLoading ? (
                    <div className="animate-pulse rounded-xl border border-outline-variant bg-surface-container-lowest p-5 shadow-sm">
                      <div className="h-4 w-1/3 rounded bg-surface-container-high" />
                      <div className="mt-4 space-y-3">
                        {[...Array(4)].map((_, i) => (
                          <div key={i} className="h-8 rounded bg-surface-container-high" />
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-4">
                      {timeline.terminals.map((t) => (
                        <div
                          key={`${t.key_field}-${t.terminal_key}`}
                          className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm"
                        >
                          <div className="flex flex-wrap items-center gap-2 border-b border-outline-variant bg-surface-container-low px-5 py-3">
                            <span className="rounded-md bg-primary/10 px-2 py-0.5 font-plex text-[10px] font-bold uppercase tracking-wider text-primary">
                              {t.key_field}
                            </span>
                            <span className="font-mono text-[13px] font-bold text-on-surface">{t.terminal_key}</span>
                            <span className="ml-auto font-plex text-[10px] text-outline">
                              {t.event_count} event{t.event_count === 1 ? '' : 's'}
                            </span>
                          </div>
                          <ol className="p-4">
                            {t.events.map((ev, i) => {
                              const kind =
                                ev.type === 'first_seen'
                                  ? { icon: 'flag', tone: 'text-green-700', bg: 'bg-green-100', label: 'First seen' }
                                  : ev.type === 'last_seen'
                                    ? { icon: 'schedule', tone: 'text-sky-700', bg: 'bg-sky-100', label: 'Last seen' }
                                    : ev.type === 'name_variant'
                                      ? { icon: 'alternate_email', tone: 'text-primary', bg: 'bg-primary/10', label: 'Known as' }
                                      : { icon: 'swap_horiz', tone: 'text-amber-700', bg: 'bg-amber-100', label: 'Account change' }
                              const file = ev.meta?.source ? String(ev.meta.source).split('::')[0].trim() : ''
                              const detail =
                                ev.type === 'name_variant'
                                  ? `${ev.value} · ${ev.meta?.count ?? ''} row(s)`
                                  : ev.type === 'account_change'
                                    ? ev.value
                                    : ev.value === 'unknown'
                                      ? 'no dates in the source files'
                                      : ev.value
                              return (
                                <li
                                  key={`${ev.type}-${i}`}
                                  className="relative flex gap-3 pb-5 pl-6 last:pb-0"
                                >
                                  <span
                                    className={`absolute left-0 top-0 flex h-6 w-6 items-center justify-center rounded-full ${kind.bg} ${kind.tone}`}
                                  >
                                    <span className="msi fill text-[13px]">{kind.icon}</span>
                                  </span>
                                  <div className="min-w-0 flex-1">
                                    <p className="text-[12px] font-bold text-on-surface">
                                      {kind.label}
                                    </p>
                                    <p className="mt-0.5 break-words font-mono text-[12px] font-semibold text-on-surface-variant">
                                      {detail}
                                    </p>
                                    <p className="mt-1 flex flex-wrap items-center gap-2 font-plex text-[10px] text-outline">
                                      {ev.occurred_at && (
                                        <span className="flex items-center gap-1">
                                          <span className="msi text-[12px]">event</span>
                                          {ev.occurred_at}
                                        </span>
                                      )}
                                      {file && (
                                        <span className="flex items-center gap-1">
                                          <span className="msi text-[12px]">description</span>
                                          {file}
                                        </span>
                                      )}
                                    </p>
                                  </div>
                                </li>
                              )
                            })}
                          </ol>
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              )}

              {/* Identity grid */}
              {identityFields.length > 0 && (
                <section>
                  <div className="mb-3 flex items-center gap-2">
                    <span className="msi fill text-primary">database</span>
                    <h3 className="text-base font-bold text-on-surface">Everything we know</h3>
                    <span className="font-plex text-[11px] text-outline">unique values grouped by field</span>
                  </div>
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
                    {identityFields.map((f) => (
                      <IdentityCard key={f.label} field={f} />
                    ))}
                  </div>
                </section>
              )}

              {/* Name variants */}
              {data.name_variants?.length > 1 && (
                <section>
                  <div className="mb-3 flex items-center gap-2">
                    <span className="msi fill text-primary">alternate_email</span>
                    <h3 className="text-base font-bold text-on-surface">Name variants</h3>
                    <span className="font-plex text-[11px] text-outline">same merchant under different spellings / aliases</span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {data.name_variants.map((v) => (
                      <span
                        key={v.name}
                        className="flex items-center gap-2 rounded-full border border-outline-variant bg-surface-container-lowest px-3 py-1.5 text-[12px] font-semibold text-on-surface"
                      >
                        {v.name}
                        <span className="rounded-full bg-surface-container-high px-1.5 font-plex text-[10px] font-bold text-on-surface-variant">
                          ×{v.count}
                        </span>
                      </span>
                    ))}
                  </div>
                </section>
              )}

              {/* Sources */}
              {data.sources?.length > 0 && (
                <section>
                  <div className="mb-3 flex items-center gap-2">
                    <span className="msi fill text-primary">folder_open</span>
                    <h3 className="text-base font-bold text-on-surface">Where this merchant appears</h3>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {data.sources.map((s, i) => (
                      <SourceChip key={`${s.sheet}-${i}`} src={s} />
                    ))}
                  </div>
                </section>
              )}

              {/* Family records */}
              {members.length > 0 && (
                <section>
                  <div className="mb-3 flex flex-wrap items-center gap-2">
                    <span className="msi fill text-primary">hub</span>
                    <h3 className="text-base font-bold text-on-surface">Linked records</h3>
                    <span className="font-plex text-[11px] text-outline">{members.length} rows sharing an identifier</span>
                    <button
                      type="button"
                      onClick={copyAllMembers}
                      title="Copy all linked records as CSV"
                      className="ml-auto flex items-center gap-1.5 rounded-lg border border-primary/30 bg-primary/5 px-3 py-1.5 font-plex text-[12px] font-bold text-primary transition-all hover:bg-primary/10 active:scale-95"
                    >
                      <span className="msi text-[16px]">{copied === 'all' ? 'check' : 'content_copy'}</span>
                      {copied === 'all' ? 'Copied!' : 'Copy all'}
                    </button>
                  </div>                    <div className="animate-fade-in-up overflow-x-auto rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
                    <div className="grid grid-cols-[1.4fr_120px_105px_120px_1fr_70px_55px_85px_80px_1fr_32px] gap-3 border-b border-outline-variant bg-surface-container px-6 py-4 font-plex text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">
                      <span>Merchant Name</span>
                      <span>TID</span>
                      <span>MX Code</span>
                      <span>Email</span>
                      <span>Bank</span>
                      <span>State</span>
                      <span>MCC</span>
                      <span>Settlement</span>
                      <span>LGA</span>
                      <span>Linked By</span>
                      <span className="text-right"></span>
                    </div>
                    {pageMembers.map((m, i) => (
                      <MemberRow
                        key={m.id || i}
                        m={m}
                        index={safeMemberPage * memberPageSize + i}
                        copiedKey={copied}
                        onCopyRow={copyMemberRow}
                      />
                    ))}
                    <TablePagination
                      total={members.length}
                      page={safeMemberPage}
                      setPage={setMemberPage}
                      pageSize={memberPageSize}
                      setPageSize={setMemberPageSize}
                    />
                  </div>
                </section>
              )}
            </div>
          )}
        </>
      ) : (
        /* ─────────────────────────── COMPARE MODE ────────────────────────── */
        <>
          {/* Two inputs */}
          <div className="mx-auto mb-3 grid max-w-4xl grid-cols-1 items-center gap-3 md:grid-cols-[1fr_auto_1fr]">
            <MerchantAutocomplete
              value={queryA}
              onChange={setQueryA}
              onSearch={(q) => {
                setQueryA(q)
                if (queryB.trim()) runCompare(q, queryB)
              }}
              placeholder="Merchant A — name, email, phone, TID…"
              icon="storefront"
              size="md"
            />
            <button
              type="button"
              onClick={swapSides}
              title="Swap sides"
              className="mx-auto flex h-11 w-11 items-center justify-center rounded-full border border-outline-variant bg-surface-container-lowest text-on-surface-variant shadow-sm transition-all hover:border-primary hover:text-primary active:scale-90"
            >
              <span className="msi text-[22px]">swap_horiz</span>
            </button>
            <MerchantAutocomplete
              value={queryB}
              onChange={setQueryB}
              onSearch={(q) => {
                setQueryB(q)
                if (queryA.trim()) runCompare(queryA, q)
              }}
              placeholder="Merchant B — name, email, phone, TID…"
              icon="storefront"
              size="md"
            />
          </div>

          {/* Compare examples */}
          {!compareData && !compareLoading && (
            <div className="mb-4 flex flex-wrap items-center justify-center gap-2">
              <span className="font-plex text-[11px] font-semibold uppercase tracking-wider text-outline">Try:</span>
              {COMPARE_EXAMPLES.map(([a, b]) => (
                <button
                  key={a + b}
                  onClick={() => {
                    setQueryA(a)
                    setQueryB(b)
                    runCompare(a, b)
                  }}
                  className="rounded-full border border-outline-variant bg-surface-container-lowest px-3 py-1 font-plex text-[11px] font-semibold text-on-surface-variant transition-colors hover:border-primary hover:text-primary"
                >
                  {a} vs {b}
                </button>
              ))}
            </div>
          )}

          {compareLoading && (
            <div className="animate-pulse">
              <div className="h-16 rounded-xl bg-surface-container-high" />
              <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
                {[...Array(4)].map((_, i) => (
                  <div key={i} className="h-44 rounded-xl bg-surface-container-high" />
                ))}
              </div>
            </div>
          )}

          {compareError && (
            <div className="rounded-xl border border-error/20 bg-error-container/30 p-5 text-center">
              <p className="font-plex text-sm font-semibold text-error">{compareError}</p>
            </div>
          )}

          {!compareLoading && !compareError && !compareData && (
            <EmptyState
              icon="compare"
              title="Compare two merchants"
              body="Enter two fragments — names, emails, phones, MX codes or TIDs — and we'll show both profiles side by side, highlight shared identifiers, and tell you if they're likely the same merchant."
            />
          )}

          {!compareLoading && compareData && (
            <div className="animate-fade-in-up space-y-6 pb-8">
              {/* Verdict banner */}
              <div
                className={`flex items-center gap-4 rounded-xl border px-5 py-4 shadow-sm ${
                  sameEntity
                    ? 'border-secondary/40 bg-secondary-container/20'
                    : maybeRelated
                      ? 'border-amber/40 bg-amber-50/40'
                      : compareData.a?.found && compareData.b?.found
                        ? 'border-outline-variant bg-surface-container-low'
                        : 'border-error/30 bg-error-container/20'
                }`}
              >
                <span
                  className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-full ${
                    sameEntity
                      ? 'bg-secondary text-on-secondary'
                      : maybeRelated
                        ? 'bg-amber-500 text-white'
                        : 'bg-surface-container-high text-on-surface-variant'
                  }`}
                >
                  <span className="msi text-[24px]">{sameEntity ? 'verified_user' : maybeRelated ? 'help' : 'manage_search'}</span>
                </span>
                <div className="min-w-0 flex-1">
                  <p className={`text-sm font-extrabold ${sameEntity ? 'text-on-secondary-container' : 'text-on-surface'}`}>
                    {compareData.a?.found && compareData.b?.found
                      ? sameEntity
                        ? 'LIKELY THE SAME MERCHANT'
                        : maybeRelated
                          ? 'POSSIBLY RELATED — one shared identifier'
                          : 'Different merchants — no shared identifiers'
                      : 'One or both sides were not found'}
                  </p>
                  <p className="mt-0.5 text-[12px] text-on-surface-variant">
                    {sameEntity
                      ? [
                          compareData.overlap_count > 0
                            ? `${compareData.overlap_count} identical record(s)`
                            : null,
                          (compareData.strong_count || 0) > 0
                            ? `${compareData.strong_count} strong identifier(s) shared`
                            : null,
                          compareData.seed_names_equal
                            ? 'same seed merchant name'
                            : null,
                          nameShared > 0 ? `${nameShared} name variant(s) shared` : null,
                        ]
                          .filter(Boolean)
                          .join(' · ')
                      : compareData.a?.found && compareData.b?.found
                        ? maybeRelated
                          ? 'One strong identifier is shared — investigate before treating these as the same merchant.'
                          : 'No strong identifier is shared between the two profiles.'
                        : `${compareData.a?.found ? 'A' : 'B'} was found but the other side had no match.`}
                  </p>
                </div>
                <span className="hidden font-plex text-[11px] text-outline md:block">{compareData.elapsed_ms}ms</span>
              </div>

              {/* Shared identifier chips */}
              {sharedCount > 0 && (
                <section>
                  <div className="mb-3 flex items-center gap-2">
                    <span className="msi fill text-secondary">link</span>
                    <h3 className="text-base font-bold text-on-surface">Shared identifiers</h3>
                    <span className="font-plex text-[11px] text-outline">values present on BOTH merchants</span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(compareData.shared || {}).map(([field, vals]) => {
                      const meta = {
                        email: { icon: 'mail', label: 'Email' },
                        phone: { icon: 'call', label: 'Phone' },
                        tid: { icon: 'point_of_sale', label: 'TID' },
                        mxcode: { icon: 'credit_card', label: 'MX Code' },
                        payable_code: { icon: 'tag', label: 'Payable' },
                        account_number: { icon: 'account_balance', label: 'Account' },
                        merchant_id: { icon: 'badge', label: 'MID' },
                      }[field] || { icon: 'link', label: field }
                      return vals.slice(0, 3).map((v, i) => (
                        <span
                          key={`${field}-${i}`}
                          className="flex items-center gap-2 rounded-full border border-secondary/30 bg-secondary/10 px-3 py-1.5 text-[12px] font-semibold text-on-secondary-container"
                        >
                          <span className="msi text-[15px] text-secondary">{meta.icon}</span>
                          <span>{meta.label}:</span>
                          <span className="font-mono">{v}</span>
                        </span>
                      ))
                    })}
                  </div>
                </section>
              )}

              {/* Side-by-side columns */}
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <CompareColumn data={compareData.a} label="Merchant A" highlight={sameEntity} />
                <CompareColumn data={compareData.b} label="Merchant B" highlight={sameEntity} />
              </div>

              {/* Field-by-field comparison table */}
              {compareData.fields?.length > 0 && (
                <section>
                  <div className="mb-3 flex items-center gap-2">
                    <span className="msi fill text-primary">table_rows</span>
                    <h3 className="text-base font-bold text-on-surface">Field comparison</h3>
                    <span className="font-plex text-[11px] text-outline">which identifiers match, differ or are unique to one side</span>
                  </div>
                  <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
                    <div className="grid grid-cols-[180px_1fr_1fr] gap-3 border-b border-outline-variant bg-surface-container px-5 py-3 font-plex text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">
                      <span>Field</span>
                      <span>Merchant A</span>
                      <span>Merchant B</span>
                    </div>
                    {compareData.fields.map((f) => {
                      const meta = STATUS_META[f.status] || STATUS_META.differ
                      return (
                        <div
                          key={f.field}
                          className="grid grid-cols-[180px_1fr_1fr] items-start gap-3 border-b border-outline-variant/60 bg-surface-container-lowest px-5 py-3.5 transition-colors last:border-0 hover:bg-surface-container-low/50"
                        >
                          <div>
                            <p className="flex items-center gap-1.5 text-[12px] font-bold text-on-surface">
                              <span className="msi fill text-[15px] text-primary">{f.icon}</span>
                              {f.label}
                            </p>
                            <span className={`mt-1 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-plex text-[10px] font-bold ${meta.tone}`}>
                              <span className="msi text-[11px]">{meta.icon}</span>
                              {meta.label}
                            </span>
                          </div>
                          <ValueCell values={f.a} shared={f.shared} empty="—" />
                          <ValueCell values={f.b} shared={f.shared} empty="—" />
                        </div>
                      )
                    })}
                  </div>
                </section>
              )}
            </div>
          )}
        </>
      )}
    </>
  )
}

/* Cell in the field-comparison table: shows values, highlighting shared ones */
function ValueCell({ values, shared, empty }) {
  if (!values || values.length === 0) return <span className="text-[12px] text-outline">{empty}</span>
  const sharedSet = new Set(shared || [])
  return (
    <div className="flex flex-wrap gap-1.5">
      {values.map((v, i) => {
        const isShared = sharedSet.has(v)
        return (
          <span
            key={`${v}-${i}`}
            title={isShared ? 'Shared by both merchants' : undefined}
            className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 font-mono text-[11px] font-semibold ${
              isShared
                ? 'border-secondary/40 bg-secondary/15 text-on-secondary-container'
                : 'border-outline-variant bg-surface-container-low text-on-surface-variant'
            }`}
          >
            {isShared && <span className="msi fill text-[12px] text-secondary">check_circle</span>}
            {v}
          </span>
        )
      })}
    </div>
  )
}
