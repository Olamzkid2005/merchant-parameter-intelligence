import { useState } from 'react'
import { api } from '../api'
import { exportFilename } from '../utils/exportName'
import { scoreTone, pillTone } from '../utils/matches'

const VIEWS = ['Matches', 'Not Found', 'Recovered Assets']

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

export default function ReconcilePage() {
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [exporting, setExporting] = useState(false)
  const [view, setView] = useState('Matches')

  const count = text.split('\n').filter((l) => l.trim()).length

  async function run() {
    const merchants = text.split('\n').map((l) => l.trim()).filter(Boolean).slice(0, 1000)
    if (!merchants.length) return
    setLoading(true)
    setError('')
    setData(null)
    setView('Matches')
    try {
      const d = await api.reconcile(merchants)
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
      const res = await api.exportReconcile(data.merchants)
      if (!res.ok) throw new Error('Export failed')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      // Name the report after the first merchant in the list (e.g. the
      // SPAR reconciliation report) instead of a generic file name.
      a.download = exportFilename(data.merchants?.[0], 'reconciliation_report', 'Merchant_Reconciliation_Report')
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="animate-fade-in-up space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[28px] font-extrabold tracking-tight text-on-surface">Reconcile</h1>
          <p className="mt-1 text-sm text-on-surface-variant">
            Turn a merchant list into a verified report with emails &amp; contacts.
          </p>
        </div>
        <button
          onClick={handleDownload}
          disabled={!data || exporting}
          className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 font-plex text-[13px] font-bold text-on-primary shadow-sm transition-all hover:opacity-90 active:scale-95 disabled:opacity-40"
        >
          <span className="msi text-[18px]">download</span>
          {exporting ? 'Building…' : 'Download Excel Report'}
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
              <span className="msi text-[18px]">rule</span>
              {loading ? 'Reconciling…' : 'Re-run Batch'}
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
          <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="h-24 animate-pulse rounded-xl border border-outline-variant bg-white shadow-sm" />
            ))}
          </div>
          <p className="text-center text-[13px] text-on-surface-variant">Reconciling merchants…</p>
        </div>
      )}

      {data && !loading && (
        <div className="mx-auto max-w-6xl">
          {/* Stat row */}
          <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
            <StatCard label="Total Records" value={data.count} sub={`${data.pct}% matched`} />
            <StatCard
              label="Confirmed Matches"
              value={data.found}
              sub={`(${data.pct}% of list)`}
              tone="text-secondary"
            />
            <StatCard label="Unresolved" value={data.missing} sub="no confident match" tone="text-error" />
            <StatCard label="Extracted Emails" value={data.emails} />
            <StatCard label="Unique Contacts" value={data.contacts} />
          </div>

          {/* Segmented control */}
          <div className="mt-6 flex w-fit overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest p-1">
            {VIEWS.map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`rounded-md px-4 py-1.5 font-plex text-[12px] font-bold transition-colors ${
                  view === v ? 'bg-primary text-on-primary shadow-sm' : 'text-on-surface-variant hover:bg-surface-container'
                }`}
              >
                {v}
              </button>
            ))}
          </div>

          {/* Matches */}
          {view === 'Matches' && (
            <div className="mt-4 overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
              <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
                <h3 className="text-sm font-bold text-on-surface">Verified Matches</h3>
                <span className="rounded-md bg-primary/10 px-2 py-0.5 font-plex text-[10px] font-bold text-primary">
                  {data.matches.length} CONFIRMED
                </span>
              </div>
              {data.matches.length === 0 ? (
                <p className="px-5 py-6 text-center text-sm text-on-surface-variant">No confident matches found for this list.</p>
              ) : (
                <div className="max-h-[560px] overflow-y-auto">
                  <div className="grid grid-cols-[170px_1.6fr_70px_150px_150px] gap-3 border-b border-outline-variant bg-surface-container px-5 py-3 font-plex text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">
                    <span>Merchant ID</span><span>Verified Name</span><span>Score</span><span>Status</span><span>Location</span>
                  </div>
                  {data.matches.map((r, i) => (
                    <div key={i} className="grid grid-cols-[170px_1.6fr_70px_150px_150px] items-center gap-3 border-b border-outline-variant px-5 py-3.5 transition-colors last:border-0 hover:bg-surface-container-low/60">
                      <span className="truncate font-mono text-xs text-on-surface-variant">{r['Merchant (input)']}</span>
                      <span className="text-sm font-bold text-on-surface">{r['Best Match']}</span>
                      <span className={`flex h-8 w-11 items-center justify-center rounded-lg text-xs font-bold ${scoreTone(r.Score)}`}>
                        {Number(r.Score || 0).toFixed(1)}
                      </span>
                      <span className={`w-fit rounded-full border px-2 py-1 text-[10px] font-bold uppercase tracking-tighter ${pillTone(r['Match Type'])}`}>
                        {r['Match Type'] || '—'}
                      </span>
                      <span className="truncate text-xs text-on-surface-variant">{r.Sheet || '—'}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Not found */}
          {view === 'Not Found' && (
            <div className="mt-4 space-y-3">
              {data.not_found.length === 0 ? (
                <div className="flex flex-col items-center rounded-xl border border-outline-variant bg-surface-container-lowest p-10 text-center shadow-sm">
                  <div className="mb-4 flex h-20 w-20 items-center justify-center rounded-full bg-secondary/10 text-secondary">
                    <span className="msi text-[44px]">verified</span>
                  </div>
                  <h3 className="mb-1 text-lg font-semibold text-on-surface">All resolved</h3>
                  <p className="max-w-[280px] text-[13px] text-on-surface-variant">Every merchant in the list was matched.</p>
                </div>
              ) : (
                data.not_found.map((r, i) => (
                  <div key={i} className="flex items-center justify-between rounded-xl border border-outline-variant bg-surface-container-lowest px-5 py-4 shadow-sm">
                    <div>
                      <b className="text-sm text-on-surface">{r['Merchant (input)']}</b>
                      <div className="text-xs text-on-surface-variant">No direct match found in Registry.</div>
                    </div>
                    <div className="text-right">
                      <div className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">Closest Candidate</div>
                      <b className="text-[13px] text-on-surface">
                        {r['Closest Candidate'] || '—'}
                        <span className="ml-2 font-plex text-xs text-on-surface-variant">
                          (Score: {Number(r.Score || 0).toFixed(1)})
                        </span>
                      </b>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Recovered assets */}
          {view === 'Recovered Assets' && (
            <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
              <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
                <div className="flex items-center gap-2 border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
                  <span className="msi text-[18px] text-primary">mail</span>
                  <h3 className="text-sm font-bold text-on-surface">Unique Emails</h3>
                  <span className="ml-auto rounded-md bg-surface-container-high px-2 py-0.5 font-plex text-[10px] font-bold text-on-surface-variant">
                    {data.emails_rows.length}
                  </span>
                </div>
                <div className="max-h-[420px] overflow-y-auto">
                  {data.emails_rows.length === 0 ? (
                    <p className="px-5 py-6 text-center text-sm text-on-surface-variant">None recovered.</p>
                  ) : (
                    data.emails_rows.map((r, i) => (
                      <div key={i} className="flex items-center justify-between gap-3 border-b border-outline-variant/60 px-5 py-2.5 last:border-0">
                        <span className="truncate text-[13px] text-on-surface">{r.Email}</span>
                        <span className="truncate text-[11px] text-outline">{String(r['Matched As'] || '').slice(0, 26)}</span>
                      </div>
                    ))
                  )}
                </div>
              </div>
              <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
                <div className="flex items-center gap-2 border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
                  <span className="msi text-[18px] text-secondary">phone</span>
                  <h3 className="text-sm font-bold text-on-surface">Phone Numbers</h3>
                  <span className="ml-auto rounded-md bg-surface-container-high px-2 py-0.5 font-plex text-[10px] font-bold text-on-surface-variant">
                    {data.contacts_rows.length}
                  </span>
                </div>
                <div className="max-h-[420px] overflow-y-auto">
                  {data.contacts_rows.length === 0 ? (
                    <p className="px-5 py-6 text-center text-sm text-on-surface-variant">None recovered.</p>
                  ) : (
                    data.contacts_rows.map((r, i) => (
                      <div key={i} className="flex items-center justify-between gap-3 border-b border-outline-variant/60 px-5 py-2.5 last:border-0">
                        <span className="truncate text-[13px] text-on-surface">{r.Phone || '—'}</span>
                        <span className="truncate text-[11px] text-outline">{String(r['Contact Name'] || '').slice(0, 26)}</span>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {!loading && !data && !error && (
        <div className="mx-auto flex max-w-3xl flex-col items-center rounded-xl border border-outline-variant bg-surface-container-lowest p-10 text-center shadow-sm">
          <div className="mb-4 flex h-20 w-20 items-center justify-center rounded-full bg-surface-container-high text-outline-variant">
            <span className="msi text-[48px]">rule</span>
          </div>
          <h3 className="mb-1 text-lg font-semibold text-on-surface">Reconciliation</h3>
          <p className="max-w-[320px] text-[13px] text-on-surface-variant">
            Paste a list of merchants to reconcile against the parameter files. You'll get verified
            matches, not-found records and recovered emails.
          </p>
        </div>
      )}
    </div>
  )
}
