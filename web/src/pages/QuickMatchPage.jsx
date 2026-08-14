import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import AutocompleteTextarea from '../components/AutocompleteTextarea'
import TablePagination from '../components/TablePagination'
import { exportFilename } from '../utils/exportName'
import { scoreTone } from '../utils/matches'
import { partsOf, sheetOf, sourceOf } from '../utils/source'
import { copyTextToClipboard, rowsCsv, rowTsv, useCopyIndicator } from '../utils/tableClipboard'

const QM_HEADERS = ['Identifier', 'Status', 'Merchant', 'Score', 'Email', 'Phone', 'MX / TID', 'Source']

function qmCell(r, h) {
  return {
    Identifier: r.input,
    Status: r.matched ? 'Found' : 'Not Found',
    Merchant: r.best_match || '',
    Score: r.score != null ? r.score.toFixed(1) : '',
    Email: r.email || '',
    Phone: r.phone || '',
    'MX / TID': r.mxcode || r.tid || '',
    Source: r.sheet || '',
  }[h]
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

/* ── CSV parsing + identifier column detection ─────────────────────────── */

// Minimal CSV parser: handles quoted fields, commas inside quotes, CRLF.
function parseCSV(text) {
  const rows = []
  let row = []
  let field = ''
  let inQuotes = false
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i++ }
        else inQuotes = false
      } else field += ch
    } else if (ch === '"') {
      inQuotes = true
    } else if (ch === ',' || ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++
      row.push(field)
      field = ''
      if (ch !== ',') {
        if (row.some((c) => c.trim() !== '')) rows.push(row)
        row = []
      }
    } else {
      field += ch
    }
  }
  if (field.trim() !== '' || row.length) {
    row.push(field)
    if (row.some((c) => c.trim() !== '')) rows.push(row)
  }
  return rows
}

const IDENTIFIER_PATTERNS = [
  /^\+?\d{9,15}$/,       // phone / account number (digits)
  /^MX\s?\d+$/i,         // MX code
  /^2ISW/i,              // TID / 2ISW codes
  /^TID/i,               // TID-prefixed
  /^[A-Z0-9]{6,16}$/i,   // alphanumeric terminal IDs (e.g. 2103O166)
  /^\S+@\S+\.\S+$/,      // email
]

function looksLikeIdentifier(value) {
  const v = String(value || '').trim()
  if (!v) return false
  return IDENTIFIER_PATTERNS.some((re) => re.test(v))
}

function detectIdentifierColumn(rows) {
  // Score each column by how many of its cells look like identifiers.
  if (!rows.length) return 0
  const width = Math.max(...rows.map((r) => r.length))
  let bestCol = 0
  let bestScore = -1
  for (let col = 0; col < width; col++) {
    let score = 0
    for (let r = 0; r < rows.length; r++) {
      const cell = rows[r][col] || ''
      if (looksLikeIdentifier(cell)) score++
    }
    // Header hint: prefer columns named phone/MX/TID/terminal/code
    const header = String(rows[0][col] || '').toLowerCase()
    if (/mx|phone|tid|terminal|mobile|code|email|account/i.test(header)) score += 2
    if (score > bestScore) {
      bestScore = score
      bestCol = col
    }
  }
  return bestCol
}

/* ── Results table row ─────────────────────────────────────────────────── */

function ResultRow({ r, sourceFilter, sheetFilter, onSheetClick, onOpenProfile, onCopyRow, copied, absRow }) {
  const matched = Boolean(r.matched)
  const { file: srcFile, sheet: srcSheet } = partsOf(r.sheet)
  return (
    <div className={`grid grid-cols-[150px_96px_1.5fr_150px_1.3fr_1.1fr_96px_120px_96px] items-center gap-3 border-b border-outline-variant px-6 py-3.5 last:border-b-0 ${matched ? 'bg-green-50/40' : ''}`}>
      <span className="font-mono text-xs text-on-surface-variant">{r.input}</span>
      {matched ? (
        <span className="flex w-fit items-center gap-1 rounded-full border border-green-200 bg-green-100 px-2 py-1 text-[10px] font-bold text-green-900">
          <span className="msi text-[13px]">check_circle</span>
          {IDENTIFIER_LABELS[r.matched_field] || r.matched_field}
        </span>
      ) : (
        <span className="w-fit rounded-full border border-red-100 bg-red-50 px-2 py-1 text-[10px] font-bold uppercase tracking-tighter text-red-900">
          Not Found
        </span>
      )}
      <span className="text-sm font-bold text-on-surface">{r.best_match || '—'}</span>
      <span className={`flex h-8 w-11 items-center justify-center rounded-lg text-xs font-bold ${scoreTone(r.score)}`}>{r.score.toFixed(1)}</span>
      <span className="truncate text-xs">{r.email || '—'}</span>
      <span className="truncate text-xs">{r.phone || '—'}</span>
      <span className="font-mono text-xs text-outline">{r.mxcode || r.tid || '—'}</span>
      <span
        title={r.sheet ? `Sheet: ${srcSheet || '—'}` : ''}
        className="flex flex-col gap-0.5"
      >
        <span className="max-w-[110px] truncate rounded border border-outline-variant bg-surface-container-low px-1.5 py-0.5 font-plex text-[10px] font-semibold text-on-surface-variant">
          {srcFile || sourceOf(r.sheet) || '—'}
        </span>
        {srcFile && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onSheetClick(srcFile, srcSheet)
            }}
            title={
              sourceFilter === srcFile && sheetFilter === srcSheet
                ? `Clear sheet filter: ${srcSheet}`
                : `Filter to sheet: ${srcSheet}`
            }
            className={`max-w-[110px] truncate text-left text-[10px] transition-colors ${
              sourceFilter === srcFile && sheetFilter === srcSheet
                ? 'font-bold text-primary'
                : 'text-outline hover:text-primary'
            }`}
          >
            {srcSheet || '—'}
          </button>
        )}
      </span>
      <div className="flex items-center justify-end gap-1">
        {r.best_match && onOpenProfile && (
          <button
            onClick={() => onOpenProfile(r.best_match)}
            title={`Open full profile: ${r.best_match}`}
            className="flex h-7 w-7 items-center justify-center rounded-lg border border-primary/25 bg-primary/5 text-primary transition-colors hover:bg-primary/15"
          >
            <span className="msi text-[16px]">person_search</span>
          </button>
        )}
        <button
          onClick={() => onCopyRow(r, absRow)}
          title="Copy row (headers + values, tab-separated)"
          className="flex h-7 w-7 items-center justify-center rounded-lg border border-outline-variant bg-surface-container-lowest text-on-surface-variant transition-colors hover:border-primary hover:text-primary"
        >
          <span className="msi text-[16px]">{copied === `row-${absRow}` ? 'check' : 'content_copy'}</span>
        </button>
      </div>
    </div>
  )
}

/* ── Page ──────────────────────────────────────────────────────────────── */

export default function QuickMatchPage() {
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [exporting, setExporting] = useState(false)
  const [fileMsg, setFileMsg] = useState('')
  // Pagination keeps 2000-identifier batches light on the DOM.
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(50)
  const { copied, indicate } = useCopyIndicator()
  // Filters are initialized straight from the URL so the very first render
  // already carries any shared/bookmarked file/sheet params — avoids a sync-
  // effect transient that would wipe them before state settles.
  const [sourceFilter, setSourceFilter] = useState(
    () => new URLSearchParams(window.location.search).get('file'), // null = All files
  )
  const [sheetFilter, setSheetFilter] = useState(
    () => new URLSearchParams(window.location.search).get('sheet'), // null = All sheets
  )
  const fileRef = useRef(null)

  // Keep the URL in sync so a filtered view can be shared / bookmarked.
  useEffect(() => {
    const url = new URL(window.location.href)
    if (sourceFilter) url.searchParams.set('file', sourceFilter)
    else url.searchParams.delete('file')
    if (sheetFilter) url.searchParams.set('sheet', sheetFilter)
    else url.searchParams.delete('sheet')
    window.history.replaceState({}, '', url)
  }, [sourceFilter, sheetFilter])

  const identifiers = text.split('\n').map((l) => l.trim()).filter(Boolean)
  const count = identifiers.length

  function handleFile(file) {
    setError('')
    setFileMsg('')
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      const rows = parseCSV(String(reader.result || ''))
      if (rows.length < 2) {
        setFileMsg('No data rows found in that file.')
        return
      }
      const col = detectIdentifierColumn(rows)
      // Skip header row if it doesn't look like an identifier itself.
      const start = looksLikeIdentifier(rows[0][col]) ? 0 : 1
      const values = []
      for (let r = start; r < rows.length; r++) {
        const v = String(rows[r][col] || '').trim()
        if (v) values.push(v)
      }
      if (!values.length) {
        setFileMsg('Could not detect an identifier column (phone / MX / TID / email).')
        return
      }
      setText(values.join('\n'))
      setFileMsg(
        `Read ${file.name} — detected ${values.length} identifiers from column "${rows[0][col] || col + 1}".`
      )
    }
    reader.readAsText(file)
  }

  async function run() {
    if (!identifiers.length) return
    setLoading(true)
    setError('')
    setData(null)
    try {
      const d = await api.quickmatch(identifiers.slice(0, 2000))
      setData({ ...d, identifiers: identifiers.slice(0, 2000) })
      setSourceFilter(null)
      setSheetFilter(null)
      setPage(0)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setLoading(false)
    }
  }

  async function handleDownload() {
    if (!data) return
    setExporting(true)
    try {
      const res = await api.exportQuickMatch(data.identifiers)
      if (!res.ok) throw new Error('Export failed')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      // Name the file after the first identifier (e.g. MX156725) so exports
      // are identifiable at a glance instead of a generic name.
      a.download = exportFilename(data.identifiers?.[0], 'quick_match', 'Quick_Match')
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setExporting(false)
    }
  }

  // File + sheet filter logic (mirrors SearchPage). File chips come from all
  // rows; sheet chips come from the file-filtered subset (contextual).
  const rows = data?.rows || []
  const sources = useMemo(() => {
    const counts = {}
    for (const r of rows) {
      const s = sourceOf(r.sheet) || 'Unknown'
      counts[s] = (counts[s] || 0) + 1
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1])
  }, [rows])

  const fileBase = useMemo(() => {
    if (sourceFilter === null) return rows
    return rows.filter((r) => (sourceOf(r.sheet) || 'Unknown') === sourceFilter)
  }, [rows, sourceFilter])

  const sheets = useMemo(() => {
    const counts = {}
    for (const r of fileBase) {
      const s = sheetOf(r.sheet) || 'Unknown'
      counts[s] = (counts[s] || 0) + 1
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1])
  }, [fileBase])

  const visible = rows.filter((r) => {
    if (sourceFilter !== null && (sourceOf(r.sheet) || 'Unknown') !== sourceFilter) return false
    if (sheetFilter !== null && (sheetOf(r.sheet) || 'Unknown') !== sheetFilter) return false
    return true
  })

  async function copyAll() {
    if (await copyTextToClipboard(rowsCsv(rows, QM_HEADERS, qmCell))) indicate('all')
  }

  async function copyRow(r, i) {
    if (await copyTextToClipboard(rowTsv(QM_HEADERS, (h) => qmCell(r, h)))) indicate(`row-${i}`)
  }

  const pageCount = Math.max(1, Math.ceil(visible.length / pageSize))
  const safePage = Math.min(page, pageCount - 1)
  const pageRows = visible.slice(safePage * pageSize, safePage * pageSize + pageSize)

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

  return (
    <>
      <div className="mb-6">
        <h1 className="text-[28px] font-extrabold tracking-tight">Quick Match</h1>
        <p className="mt-1 text-sm text-on-surface-variant">
          Upload a CSV of identifiers (phones, MX codes, TIDs, emails) and resolve every one against the registry in one batch.
        </p>
      </div>

      {/* Upload + input */}
      <div className="mx-auto mb-6 max-w-3xl space-y-4">
        {/* Dropzone */}
        <button
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault()
            handleFile(e.dataTransfer.files?.[0])
          }}
          className="w-full rounded-2xl border-2 border-dashed border-outline-variant bg-surface-container-lowest px-6 py-7 text-center transition-colors hover:border-primary hover:bg-primary/5"
        >
          <span className="msi fill text-[32px] text-primary">upload_file</span>
          <p className="mt-2 text-sm font-semibold text-on-surface">
            Drop a CSV here or click to browse
          </p>
          <p className="mt-1 text-xs text-on-surface-variant">
            We auto-detect the identifier column (phone · MX code · TID · email · account no.)
          </p>
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={(e) => {
            handleFile(e.target.files?.[0])
            e.target.value = ''
          }}
        />
        {fileMsg && <p className="text-center text-xs font-medium text-secondary">{fileMsg}</p>}

        <div className="rounded-2xl border border-outline-variant bg-surface-container-lowest shadow-sm focus-within:border-primary focus-within:ring-4 focus-within:ring-primary/10">
          <AutocompleteTextarea
            value={text}
            onChange={setText}
            rows={6}
            mono
            placeholder={'08000000000\nMX183544\n2103O166\nmerchant30@example.com'}
          />
          <div className="flex items-center justify-between border-t border-outline-variant px-5 py-3">
            <span className="font-plex text-xs text-on-surface-variant">
              {Math.min(count, 2000)} identifier{count === 1 ? '' : 's'} · limit 2,000
            </span>
            <button
              onClick={run}
              disabled={loading || !count}
              className="rounded-xl bg-primary px-5 py-2.5 font-plex text-[13px] font-bold text-on-primary transition-all hover:opacity-90 active:scale-95 disabled:opacity-40"
            >
              {loading ? 'Resolving…' : `Resolve ${Math.min(count, 2000) || ''} identifiers`}
            </button>
          </div>
        </div>
      </div>

      {error && (
        <div className="mx-auto mb-6 max-w-3xl rounded-xl border border-error/20 bg-error-container/30 p-5 text-center">
          <p className="font-plex text-sm font-semibold text-error">{error}</p>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="mx-auto max-w-6xl space-y-4">
          <div className="animate-pulse space-y-3 rounded-xl border border-outline-variant bg-white p-5 shadow-sm">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="flex items-center gap-4">
                <div className="h-8 w-8 rounded-full bg-surface-container-highest" />
                <div className="h-4 flex-1 rounded bg-surface-container-highest" />
                <div className="h-4 w-32 rounded bg-surface-container-highest" />
              </div>
            ))}
          </div>
          <p className="text-center text-[13px] text-on-surface-variant">Resolving identifiers…</p>
        </div>
      )}

      {/* Results */}
      {data && !loading && (
        <div className="mx-auto max-w-7xl animate-fade-in-up">
          <div className="mb-5 rounded-xl border border-outline-variant bg-white p-5 shadow-sm">
            <div className="mb-2.5 flex items-end justify-between">
              <span className="font-plex text-xs font-semibold text-on-surface-variant">Resolution complete</span>
              <span className="text-2xl font-extrabold text-primary">{data.pct}%</span>
            </div>
            <div className="mb-5 h-2 overflow-hidden rounded-full bg-surface-container-highest">
              <div className="h-full rounded-full bg-primary transition-all duration-700" style={{ width: `${data.pct}%` }} />
            </div>
            <div className="grid grid-cols-2 gap-4 text-[13px] md:grid-cols-4">
              <div><span className="text-on-surface-variant">Identifiers</span><br /><b>{data.count}</b></div>
              <div><span className="text-on-surface-variant">Matched</span><br /><b className="text-secondary">{data.matched}</b></div>
              <div><span className="text-on-surface-variant">Not Found</span><br /><b className="text-error">{data.missing}</b></div>
              <div><span className="text-on-surface-variant">Time Taken</span><br /><b>{data.elapsed_s}s</b></div>
            </div>
          </div>

          {/* File + sheet filter chips */}
          {(sources.length > 1 || sheets.length > 1) && (
            <div className="mb-4 flex flex-wrap items-center gap-2">
              {sources.length > 1 && (
                <>
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
                    All <span className="opacity-70">({rows.length})</span>
                  </button>
                  {sources.map(([src, n]) => (
                    <button
                      key={src}
                      onClick={() => {
                        setSourceFilter(sourceFilter === src ? null : src)
                        setSheetFilter(null)
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
                  {sheets.length > 1 && <span className="mx-1 h-4 w-px bg-outline-variant" />}
                </>
              )}
              {sheets.length > 1 && (
                <>
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
                </>
              )}
            </div>
          )}

          <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
            <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-6 py-4">
              <div className="flex items-center gap-2">
                <b className="text-base">Resolution Results</b>
                <span className="rounded border border-primary/20 bg-primary/10 px-2 py-0.5 text-[10px] font-bold text-primary">
                  IDENTIFIER MATCH: ON
                </span>
              </div>
              <span className="text-xs text-on-surface-variant">{data.emails} emails found</span>
            </div>
            <div className="grid grid-cols-[150px_96px_1.5fr_150px_1.3fr_1.1fr_96px_120px_96px] gap-3 border-b border-outline-variant bg-surface-container px-6 py-3 font-plex text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">
              <span>Identifier</span><span>Status</span><span>Merchant</span><span>Score</span><span>Email</span><span>Phone</span><span>MX / TID</span><span>Source</span><span className="text-right">Actions</span>
            </div>
            {pageRows.map((r, i) => (
              <ResultRow
                key={i}
                r={r}
                absRow={safePage * pageSize + i}
                sourceFilter={sourceFilter}
                sheetFilter={sheetFilter}
                onSheetClick={handleSheetClick}
                onOpenProfile={onOpenProfile}
                onCopyRow={copyRow}
                copied={copied}
              />
            ))}
            <TablePagination
              total={visible.length}
              page={page}
              setPage={setPage}
              pageSize={pageSize}
              setPageSize={setPageSize}
            />
            <div className="flex items-center justify-between bg-surface-container-low px-6 py-3.5 text-xs text-on-surface-variant">
              <span>
                Showing {visible.length} of {rows.length} entries
                {sourceFilter !== null && (
                  <span className="ml-1 text-outline">· file <b>{sourceFilter}</b></span>
                )}
                {sheetFilter !== null && (
                  <span className="ml-1 text-outline">· sheet <b>{sheetFilter}</b></span>
                )}
              </span>
              <div className="flex items-center gap-2">
                <button
                  onClick={copyAll}
                  className="flex items-center gap-1.5 rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-2 font-plex text-xs font-bold text-on-surface-variant transition-all hover:border-primary hover:text-primary active:scale-95"
                >
                  <span className="msi text-[15px]">{copied === 'all' ? 'check' : 'content_copy'}</span>
                  {copied === 'all' ? 'Copied!' : 'Copy all'}
                </button>
                <button
                  onClick={handleDownload}
                  disabled={exporting}
                  className="rounded-lg bg-primary px-4 py-2 font-plex text-xs font-bold text-on-primary transition-all hover:opacity-90 active:scale-95 disabled:opacity-40"
                >
                  {exporting ? 'Building…' : 'Download Excel'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {!loading && !data && !error && (
        <div className="mx-auto flex max-w-3xl flex-col items-center rounded-xl border border-outline-variant bg-surface-container-lowest p-10 text-center shadow-sm">
          <div className="mb-4 flex h-20 w-20 items-center justify-center rounded-full bg-surface-container-high text-outline-variant">
            <span className="msi text-[48px]">bolt</span>
          </div>
          <h3 className="mb-1 text-lg font-semibold">Identifier quick-match</h3>
          <p className="max-w-[320px] text-[13px] text-on-surface-variant">
            Upload a CSV (we auto-detect the phone / MX / TID / email column) or paste identifiers — one per line. Limit: 2,000.
          </p>
        </div>
      )}
    </>
  )
}
