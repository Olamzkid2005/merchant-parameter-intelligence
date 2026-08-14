import { useState } from 'react'
import { api } from '../api'
import { exportFilename } from '../utils/exportName'
import { scoreTone, pillTone } from '../utils/matches'

/**
 * Report Builder — Phase 9 Merchant Intelligence Report.
 *
 * Paste a merchant list and build the full multi-sheet report:
 * Summary, Exact Matches, High Confidence, Possible Matches, Emails,
 * Phones, Contacts, Addresses, Duplicate Merchants, Not Found.
 * Preview the counts/sheets in the browser, then download the .xlsx.
 */

const SHEET_META = [
  { key: 'exact', label: 'Exact Matches', icon: 'verified', tone: 'bg-green-100 text-green-800' },
  { key: 'high', label: 'High Confidence', icon: 'check_circle', tone: 'bg-orange-100 text-orange-800' },
  { key: 'possible', label: 'Possible Matches', icon: 'help', tone: 'bg-slate-100 text-slate-800' },
  { key: 'emails', label: 'Emails', icon: 'mail', tone: 'bg-primary/10 text-primary' },
  { key: 'phones', label: 'Phone Numbers', icon: 'phone', tone: 'bg-secondary/10 text-secondary' },
  { key: 'contacts', label: 'Contacts', icon: 'person', tone: 'bg-surface-container-high text-on-surface-variant' },
  { key: 'addresses', label: 'Addresses', icon: 'location_on', tone: 'bg-amber-100 text-amber-800' },
  { key: 'duplicates', label: 'Duplicate Merchants', icon: 'content_copy', tone: 'bg-error-container text-error' },
  { key: 'not_found', label: 'Not Found', icon: 'search_off', tone: 'bg-red-100 text-red-800' },
]

const PREVIEW_COLUMNS = {
  exact: ['Merchant (input)', 'Best Match', 'Score', 'Match Type', 'TID', 'MX Code', 'Email'],
  high: ['Merchant (input)', 'Best Match', 'Score', 'Match Type', 'TID', 'MX Code', 'Email'],
  possible: ['Merchant (input)', 'Best Match', 'Score', 'Match Type', 'Sheet'],
  emails: ['Merchant (input)', 'Matched As', 'Email'],
  phones: ['Merchant (input)', 'Matched As', 'Phone'],
  contacts: ['Merchant (input)', 'Matched As', 'Contact Name', 'Phone', 'Address'],
  addresses: ['Merchant (input)', 'Matched As', 'Address'],
  duplicates: ['Merchant', 'Occurrences', 'Locations'],
  not_found: ['Merchant (input)', 'Closest Candidate', 'Score'],
}

function StatCard({ label, value, sub, tone }) {
  return (
    <div className="rounded-xl border border-outline-variant bg-surface-container-lowest p-4 shadow-sm">
      <div className="font-plex text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
        {label}
      </div>
      <div className={`mt-1 text-2xl font-extrabold tracking-tight ${tone || 'text-on-surface'}`}>{value}</div>
      {sub && <div className="text-xs text-on-surface-variant">{sub}</div>}
    </div>
  )
}

export default function ReportBuilderPage() {
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [exporting, setExporting] = useState(false)
  const [view, setView] = useState('exact')

  const count = text.split('\n').filter((l) => l.trim()).length

  async function run() {
    const merchants = text.split('\n').map((l) => l.trim()).filter(Boolean).slice(0, 1000)
    if (!merchants.length) return
    setLoading(true)
    setError('')
    setData(null)
    setView('exact')
    try {
      const d = await api.report(merchants)
      setData({ ...d, merchants })
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
      const res = await api.exportReport(data.merchants)
      if (!res.ok) throw new Error('Export failed')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      // Name the report after the first merchant in the list (e.g. the
      // SPAR intelligence report) instead of a generic file name.
      a.download = exportFilename(data.merchants?.[0], 'intelligence_report', 'Merchant_Intelligence_Report')
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setExporting(false)
    }
  }

  const summaryMap = Object.fromEntries((data?.summary || []).map((r) => [r.Metric, r.Value]))

  return (
    <div className="animate-fade-in-up space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[28px] font-extrabold tracking-tight text-on-surface">Report Builder</h1>
          <p className="mt-1 text-sm text-on-surface-variant">
            Build the full Merchant Intelligence report — matches, emails, phones, duplicates &amp; more.
          </p>
        </div>
        <button
          onClick={handleDownload}
          disabled={!data || exporting}
          className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 font-plex text-[13px] font-bold text-on-primary shadow-sm transition-all hover:opacity-90 active:scale-95 disabled:opacity-40"
        >
          <span className="msi text-[18px]">download</span>
          {exporting ? 'Building…' : 'Download Report (.xlsx)'}
        </button>
      </div>

      {/* Input */}
      <div className="mx-auto max-w-3xl">
        <div className="rounded-2xl border border-outline-variant bg-surface-container-lowest shadow-sm focus-within:border-primary focus-within:ring-4 focus-within:ring-primary/10">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={7}
            placeholder={'THE FILM HOUSE LIMITED\nSPAR Lekki\nBEACONHEALTH DIAGNOSTICS'}
            className="w-full resize-none rounded-t-2xl bg-transparent p-5 text-sm outline-none"
          />
          <div className="flex items-center justify-between border-t border-outline-variant px-5 py-3">
            <span className="font-plex text-xs text-on-surface-variant">
              {count} merchant{count === 1 ? '' : 's'} · limit 1,000
            </span>
            <button
              onClick={run}
              disabled={loading || !count}
              className="flex items-center gap-2 rounded-xl bg-primary px-5 py-2.5 font-plex text-[13px] font-bold text-on-primary transition-all hover:opacity-90 active:scale-95 disabled:opacity-40"
            >
              <span className="msi text-[18px]">description</span>
              {loading ? 'Building…' : 'Build Report'}
            </button>
          </div>
        </div>
      </div>

      {error && (
        <div className="mx-auto max-w-3xl rounded-xl border border-error/20 bg-error-container/30 p-5 text-center">
          <p className="font-plex text-sm font-semibold text-error">{error}</p>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="mx-auto max-w-5xl space-y-4">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-24 animate-pulse rounded-xl border border-outline-variant bg-white shadow-sm" />
            ))}
          </div>
          <p className="text-center text-[13px] text-on-surface-variant">Running searches across the registry…</p>
        </div>
      )}

      {data && !loading && (
        <div className="mx-auto max-w-6xl">
          {/* Summary stat row */}
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatCard label="Total Merchants" value={summaryMap['Total merchants'] || data.count} />
            <StatCard label="Match Rate" value={summaryMap['Match rate'] || '—'} tone="text-secondary" />
            <StatCard label="Emails Recovered" value={summaryMap['Emails recovered'] || 0} />
            <StatCard label="Duplicate Clusters" value={summaryMap['Duplicate merchant clusters'] || 0} tone="text-error" />
          </div>

          {/* Sheet selector */}
          <div className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
            {SHEET_META.map((s) => (
              <button
                key={s.key}
                onClick={() => setView(s.key)}
                className={`flex items-center gap-3 rounded-xl border p-3 text-left transition-all ${
                  view === s.key
                    ? 'border-primary bg-primary/5 shadow-sm ring-2 ring-primary/20'
                    : 'border-outline-variant bg-surface-container-lowest hover:border-primary/50'
                }`}
              >
                <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${s.tone}`}>
                  <span className="msi text-[18px]">{s.icon}</span>
                </span>
                <div className="min-w-0">
                  <div className="truncate text-[12px] font-bold text-on-surface">{s.label}</div>
                  <div className="font-plex text-[11px] text-on-surface-variant">
                    {data.sheet_counts[s.key] || 0} rows
                  </div>
                </div>
              </button>
            ))}
          </div>

          {/* Sheet preview table */}
          <div className="mt-6 overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
            <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
              <h3 className="text-sm font-bold text-on-surface">
                {SHEET_META.find((s) => s.key === view)?.label}
              </h3>
              <span className="rounded-md bg-surface-container-high px-2 py-0.5 font-plex text-[10px] font-bold text-on-surface-variant">
                {(data.sheets[view] || []).length} rows
              </span>
            </div>
            {(!data.sheets[view] || data.sheets[view].length === 0) ? (
              <p className="px-5 py-8 text-center text-sm text-on-surface-variant">No records in this sheet.</p>
            ) : (
              <div className="max-h-[480px] overflow-auto">
                <table className="w-full text-left">
                  <thead className="sticky top-0 z-10 bg-surface-container">
                    <tr className="font-plex text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">
                      {(PREVIEW_COLUMNS[view] || []).map((c) => (
                        <th key={c} className="px-5 py-2.5">{c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.sheets[view].slice(0, 100).map((r, i) => (
                      <tr key={i} className="border-b border-outline-variant/60 transition-colors last:border-0 hover:bg-surface-container-low/60">
                        {(PREVIEW_COLUMNS[view] || []).map((c) => {
                          const v = r[c]
                          if (c === 'Score' && typeof v === 'number') {
                            return (
                              <td key={c} className="px-5 py-2.5">
                                <span className={`inline-flex h-7 w-10 items-center justify-center rounded-lg text-xs font-bold ${scoreTone(v)}`}>
                                  {Number(v).toFixed(1)}
                                </span>
                              </td>
                            )
                          }
                          if (c === 'Match Type') {
                            return (
                              <td key={c} className="px-5 py-2.5">
                                <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-tighter ${pillTone(v)}`}>
                                  {v || '—'}
                                </span>
                              </td>
                            )
                          }
                          return (
                            <td key={c} className="max-w-[260px] truncate px-5 py-2.5 text-[13px] text-on-surface">
                              {v === undefined || v === null ? '—' : String(v)}
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {!loading && !data && !error && (
        <div className="mx-auto flex max-w-3xl flex-col items-center rounded-xl border border-outline-variant bg-surface-container-lowest p-10 text-center shadow-sm">
          <div className="mb-4 flex h-20 w-20 items-center justify-center rounded-full bg-surface-container-high text-outline-variant">
            <span className="msi text-[48px]">description</span>
          </div>
          <h3 className="mb-1 text-lg font-semibold text-on-surface">Merchant Intelligence Report</h3>
          <p className="max-w-[340px] text-[13px] text-on-surface-variant">
            Paste a merchant list and get the full report — exact/high/possible matches, recovered
            emails &amp; phones, contacts, duplicate clusters and unresolved names — all in one workbook.
          </p>
        </div>
      )}
    </div>
  )
}
