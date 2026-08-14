import { useState } from 'react'
import { api } from '../api'
import AutocompleteTextarea from '../components/AutocompleteTextarea'
import KeyMerchantBadge from '../components/KeyMerchantBadge'
import TablePagination from '../components/TablePagination'
import { exportFilename } from '../utils/exportName'
import { scoreTone, pillTone } from '../utils/matches'
import { copyTextToClipboard, rowsCsv, rowTsv, useCopyIndicator } from '../utils/tableClipboard'

const BATCH_HEADERS = ['Input', 'Best Match', 'Score', 'Match Type', 'Email', 'Phone', 'TID']

function batchCell(r, h) {
  return {
    Input: r.input,
    'Best Match': r.best_match || '',
    Score: r.score != null ? r.score.toFixed(1) : '',
    'Match Type': r.match_type || '',
    Email: r.email || '',
    Phone: r.phone || '',
    TID: r.tid || '',
  }[h]
}

export default function BatchPage({ onOpenProfile }) {
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [exporting, setExporting] = useState(false)
  // Pagination keeps 1000-merchant batches light on the DOM.
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(50)
  const { copied, indicate } = useCopyIndicator()

  const count = text.split('\n').filter((l) => l.trim()).length

  async function run() {
    const merchants = text.split('\n').map((l) => l.trim()).filter(Boolean).slice(0, 1000)
    if (!merchants.length) return
    setLoading(true)
    setError('')
    setData(null)
    try {
      const d = await api.batch(merchants)
      setData({ ...d, merchants })
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
      const res = await api.exportBatch(data.merchants)
      if (!res.ok) throw new Error('Export failed')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      // Name the file after the first input merchant (e.g. the SPAR batch)
      // so exports are identifiable at a glance instead of a generic name.
      a.download = exportFilename(data.merchants?.[0], 'batch_search', 'Batch_Search')
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setExporting(false)
    }
  }

  async function copyAll() {
    if (await copyTextToClipboard(rowsCsv(data?.rows || [], BATCH_HEADERS, batchCell))) indicate('all')
  }

  async function copyRow(r, i) {
    if (await copyTextToClipboard(rowTsv(BATCH_HEADERS, (h) => batchCell(r, h)))) indicate(`row-${i}`)
  }

  const batchRows = data?.rows || []
  const pageCount = Math.max(1, Math.ceil(batchRows.length / pageSize))
  const safePage = Math.min(page, pageCount - 1)
  const pageRows = batchRows.slice(safePage * pageSize, safePage * pageSize + pageSize)

  return (
    <>
      <div className="mb-6">
        <h1 className="text-[28px] font-extrabold tracking-tight">Batch Search</h1>
        <p className="mt-1 text-sm text-on-surface-variant">
          Paste a merchant list — search all, review stats, export Excel.
        </p>
      </div>

      {/* Input */}
      <div className="mx-auto mb-6 max-w-3xl">
        <div className="rounded-2xl border border-outline-variant bg-surface-container-lowest shadow-sm focus-within:border-primary focus-within:ring-4 focus-within:ring-primary/10">
          <AutocompleteTextarea
            value={text}
            onChange={setText}
            rows={7}
            placeholder={'THE FILM HOUSE LIMITED\nSPAR Lekki\nBEACONHEALTH DIAGNOSTICS'}
          />
          <div className="flex items-center justify-between border-t border-outline-variant px-5 py-3">
            <span className="font-plex text-xs text-on-surface-variant">
              {count} merchant{count === 1 ? '' : 's'} · limit 1,000
            </span>
            <button
              onClick={run}
              disabled={loading || !count}
              className="rounded-xl bg-primary px-5 py-2.5 font-plex text-[13px] font-bold text-on-primary transition-all hover:opacity-90 active:scale-95 disabled:opacity-40"
            >
              {loading ? 'Searching…' : `Search ${count || ''} merchants`}
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
        <div className="mx-auto max-w-5xl space-y-4">
          <div className="animate-pulse space-y-3 rounded-xl border border-outline-variant bg-white p-5 shadow-sm">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="flex items-center gap-4">
                <div className="h-8 w-8 rounded-full bg-surface-container-highest" />
                <div className="h-4 flex-1 rounded bg-surface-container-highest" />
                <div className="h-4 w-32 rounded bg-surface-container-highest" />
              </div>
            ))}
          </div>
          <p className="text-center text-[13px] text-on-surface-variant">Searching merchants…</p>
        </div>
      )}

      {/* Results */}
      {data && !loading && (
        <div className="mx-auto max-w-6xl animate-fade-in-up">
          {/* Processing card */}
          <div className="mb-5 rounded-xl border border-outline-variant bg-white p-5 shadow-sm">
            <div className="mb-2.5 flex items-end justify-between">
              <span className="font-plex text-xs font-semibold text-on-surface-variant">Processing complete</span>
              <span className="text-2xl font-extrabold text-primary">{data.pct}%</span>
            </div>
            <div className="mb-5 h-2 overflow-hidden rounded-full bg-surface-container-highest">
              <div className="h-full rounded-full bg-primary transition-all duration-700" style={{ width: `${data.pct}%` }} />
            </div>
            <div className="grid grid-cols-2 gap-4 text-[13px] md:grid-cols-4">
              <div><span className="text-on-surface-variant">Matches Found</span><br /><b>{data.found}</b></div>
              <div><span className="text-on-surface-variant">Missing Info</span><br /><b className="text-error">{data.missing}</b></div>
              <div><span className="text-on-surface-variant">Emails</span><br /><b>{data.emails}</b></div>
              <div><span className="text-on-surface-variant">Time Taken</span><br /><b>{data.elapsed_s}s</b></div>
            </div>
          </div>

          {/* Table */}
          <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
            <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-6 py-4">
              <div className="flex items-center gap-2">
                <b className="text-base">Intelligence Results</b>
                <span className="rounded border border-primary/20 bg-primary/10 px-2 py-0.5 text-[10px] font-bold text-primary">
                  FUZZY MATCH: ON
                </span>
              </div>
            </div>
            <div className="grid grid-cols-[160px_1.6fr_64px_140px_1.3fr_1.1fr_110px_96px] gap-3 border-b border-outline-variant bg-surface-container px-6 py-3 font-plex text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">
              <span>Input</span><span>Best Match</span><span>Score</span><span>Match Type</span><span>Email</span><span>Phone</span><span>TID</span><span className="text-right">Actions</span>
            </div>
            {pageRows.map((r, i) => {
              const absRow = safePage * pageSize + i
              const merchant = r.best_match && r.best_match !== 'NOT FOUND' ? r.best_match : ''
              return (
              <div key={i} className="grid grid-cols-[160px_1.6fr_64px_140px_1.3fr_1.1fr_110px_96px] items-center gap-3 border-b border-outline-variant px-6 py-3.5 last:border-b-0">
                <span className="font-mono text-xs text-on-surface-variant">{r.input}</span>
                <span className="flex min-w-0 items-center gap-2">
                  <span className="truncate text-sm font-bold text-on-surface">{r.best_match || 'NOT FOUND'}</span>
                  <KeyMerchantBadge roots={r.key_merchants} onOpenProfile={onOpenProfile} />
                </span>
                <span className={`flex h-8 w-11 items-center justify-center rounded-lg text-xs font-bold ${scoreTone(r.score)}`}>{r.score.toFixed(1)}</span>
                <span className={`w-fit rounded-full border px-2 py-1 text-[10px] font-bold uppercase tracking-tighter ${pillTone(r.match_type)}`}>{r.match_type}</span>
                <span className="truncate text-xs">{r.email || '—'}</span>
                <span className="truncate text-xs">{r.phone || '—'}</span>
                <span className="font-mono text-xs text-outline">{r.tid || '—'}</span>
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
              </div>
              )
            })}
            <TablePagination
              total={data.rows.length}
              page={page}
              setPage={setPage}
              pageSize={pageSize}
              setPageSize={setPageSize}
            />
            <div className="flex items-center justify-between bg-surface-container-low px-6 py-3.5 text-xs text-on-surface-variant">
              <span>Showing {data.rows.length} of {data.rows.length} entries</span>
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
                  disabled={!data || exporting}
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
            <span className="msi text-[48px]">playlist_add_check</span>
          </div>
          <h3 className="mb-1 text-lg font-semibold">Batch input</h3>
          <p className="max-w-[300px] text-[13px] text-on-surface-variant">
            Paste one merchant per line (name, alias, or TID). Limit: 1,000 entries.
          </p>
        </div>
      )}
    </>
  )
}
