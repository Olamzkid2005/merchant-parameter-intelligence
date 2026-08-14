import { useEffect, useState } from 'react'
import { api } from '../api'
import { exportFilename } from '../utils/exportName'

const FIELD_SPECS = [
  { key: 'email', label: 'Email Address', icon: 'email', color: 'blue', severity: 'High' },
  { key: 'phone', label: 'Phone Number', icon: 'phone', color: 'green', severity: 'Medium' },
  { key: 'mxcode', label: 'Tax ID (TIN)', icon: 'badge', color: 'amber', severity: 'High' },
  { key: 'contact_name', label: 'Contact Name', icon: 'person', color: 'slate', severity: 'Medium' },
  { key: 'address', label: 'Physical Address', icon: 'location_on', color: 'red', severity: 'Low' },
  { key: 'account_name', label: 'Account Name', icon: 'account_balance', color: 'slate', severity: 'Low' },
  { key: 'tid', label: 'TID', icon: 'point_of_sale', color: 'slate', severity: 'Low' },
]

const SEV_PILL = {
  High: 'bg-red-100 text-red-800',
  Medium: 'bg-orange-100 text-orange-800',
  Low: 'bg-green-100 text-green-800',
}

const FIELD_ICON = {
  blue: 'bg-primary/10 text-primary',
  green: 'bg-secondary/10 text-secondary',
  amber: 'bg-amber-100 text-amber-800',
  red: 'bg-error-container text-error',
  slate: 'bg-surface-container-high text-on-surface-variant',
}

function StatCard({ icon, label, value, desc, tone, valueTone }) {
  return (
    <div className="rounded-xl border border-outline-variant bg-surface-container-lowest p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-3">
        <span className={`flex h-10 w-10 items-center justify-center rounded-xl ${tone}`}>
          <span className="msi text-[22px]">{icon}</span>
        </span>
        <div>
          <div className="text-[11px] font-plex font-semibold uppercase tracking-wider text-on-surface-variant">
            {label}
          </div>
          <div className={`text-2xl font-extrabold tracking-tight ${valueTone || 'text-on-surface'}`}>{value}</div>
        </div>
      </div>
      <p className="text-xs text-on-surface-variant">{desc}</p>
    </div>
  )
}

// DB-rooted identifier classifier debug — self-contained so it works even
// when the quality report itself fails to load (it only talks to
// /api/idclass/debug, which is independent of /api/quality).
function ClassifierDebugCard() {
  const [dbgInput, setDbgInput] = useState('')
  const [dbgData, setDbgData] = useState(null)
  const [dbgLoading, setDbgLoading] = useState(false)
  const [dbgError, setDbgError] = useState('')

  async function runDebug() {
    const v = dbgInput.trim()
    if (!v) return
    setDbgLoading(true)
    setDbgError('')
    try {
      // Pass the raw text so the endpoint tokenizes exactly like the task
      // engine does (whitespace/comma/semicolon delimited).
      const data = await api.idclassDebug('', v)
      setDbgData(data)
    } catch (e) {
      setDbgError(String(e.message || e))
      setDbgData(null)
    } finally {
      setDbgLoading(false)
    }
  }

  return (
    <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
      <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
        <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
          <span className="msi text-[18px] text-primary">manage_search</span>
          Identifier Classifier Debug
        </h3>
        <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
          db-rooted · idclass.py
        </span>
      </div>
      <div className="p-5">
        <p className="mb-3 text-xs text-on-surface-variant">
          Paste any identifiers (or a full request) to see exactly which registry column each
          value classified into — <b>db_membership</b> means it exists in the DB index,
          <b> shape_rule</b> means it fell back to a shape pattern, <b>rejected</b> means it
          didn't look like an identifier.
        </p>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            value={dbgInput}
            onChange={(e) => setDbgInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && runDebug()}
            placeholder="e.g. MX184380 2103O338 5180857349 zkP5u7JM9 FELIX"
            className="flex-1 rounded-xl border border-outline-variant bg-surface-container-lowest px-4 py-2.5 font-mono text-[13px] text-on-surface shadow-sm outline-none transition-all focus:border-primary focus:ring-4 focus:ring-primary-container"
          />
          <button
            onClick={runDebug}
            disabled={!dbgInput.trim() || dbgLoading}
            className="flex items-center justify-center gap-2 rounded-xl bg-primary px-5 py-2.5 font-plex text-[13px] font-bold text-on-primary shadow-sm transition-all hover:opacity-90 active:scale-95 disabled:opacity-40"
          >
            <span className="msi text-[18px]">{dbgLoading ? 'hourglass_top' : 'manage_search'}</span>
            {dbgLoading ? 'Classifying…' : 'Classify'}
          </button>
        </div>

        {dbgError && (
          <p className="mt-3 rounded-lg bg-error-container/30 px-4 py-2 font-plex text-xs font-semibold text-error">
            {dbgError}
          </p>
        )}

        {dbgData && (
          <>
            {/* Index diagnostics */}
            <div className="mt-4 flex flex-wrap gap-2">
              <span className="rounded-md bg-surface-container-high px-2.5 py-1 font-plex text-[11px] font-semibold text-on-surface-variant" title={dbgData.index.db_path}>
                DB: {dbgData.index.db_path.split(/[\\/]/).pop()}
              </span>
              <span className="rounded-md bg-surface-container-high px-2.5 py-1 font-plex text-[11px] font-semibold text-on-surface-variant">
                {dbgData.index.distinct_tokens.toLocaleString()} distinct identifier tokens indexed
              </span>
              {Object.entries(dbgData.index.kinds_in_db || {}).map(([kind, n]) => (
                <span key={kind} className="rounded-md bg-primary/10 px-2.5 py-1 font-plex text-[11px] font-semibold text-primary">
                  {kind}: {n.toLocaleString()}
                </span>
              ))}
            </div>

            {/* Per-token results */}
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-left">
                <thead className="bg-surface-container">
                  <tr className="font-plex text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">
                    <th className="px-4 py-2.5">Value</th>
                    <th className="px-4 py-2.5">Classified As</th>
                    <th className="px-4 py-2.5">Source</th>
                    <th className="px-4 py-2.5">DB Columns</th>
                    <th className="px-4 py-2.5">Shape Rule</th>
                  </tr>
                </thead>
                <tbody>
                  {dbgData.results.map((r, i) => {
                    const srcTone =
                      r.source === 'db_membership' ? 'bg-green-100 text-green-800'
                      : r.source === 'shape_rule' ? 'bg-amber-100 text-amber-800'
                      : r.source === 'rejected' ? 'bg-red-100 text-red-800'
                      : 'bg-slate-100 text-slate-700'
                    return (
                      <tr key={i} className="border-b border-outline-variant/60 align-top transition-colors last:border-0 hover:bg-surface-container-low/60">
                        <td className="px-4 py-2.5 font-mono text-[12px] font-bold text-on-surface">
                          {r.token || '—'}
                          <span className="ml-2 font-plex text-[10px] font-normal text-outline" title={r.reason}>
                            {r.reason}
                          </span>
                        </td>
                        <td className="px-4 py-2.5">
                          {r.kinds && r.kinds.length > 0 ? (
                            <div className="flex flex-wrap gap-1">
                              {r.kinds.map((k) => (
                                <span key={k} className={`rounded-full px-2 py-0.5 font-plex text-[10px] font-bold ${k === r.primary ? 'bg-primary text-on-primary' : 'bg-surface-container-high text-on-surface-variant'}`}>
                                  {k}
                                </span>
                              ))}
                            </div>
                          ) : (
                            <span className="font-plex text-[11px] text-outline">—</span>
                          )}
                        </td>
                        <td className="px-4 py-2.5">
                          <span className={`rounded-full px-2 py-0.5 font-plex text-[10px] font-bold ${srcTone}`}>
                            {r.source}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 font-plex text-[11px] text-on-surface-variant">
                          {r.in_db_columns && r.in_db_columns.length > 0
                            ? r.in_db_columns.join(', ')
                            : '—'}
                        </td>
                        <td className="max-w-[260px] truncate px-4 py-2.5 font-mono text-[11px] text-outline" title={r.shape_rule}>
                          {r.shape_rule || '—'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export default function QualityPage() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [exporting, setExporting] = useState(false)
  const [selfImp, setSelfImp] = useState(null)

  useEffect(() => {
    api.quality().then(setData).catch((e) => setError(String(e.message || e))).finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    // Engine-health card: last alias-free harness run + baseline (feature #10).
    api.selfImprove().then(setSelfImp).catch(() => setSelfImp(null))
  }, [])

  async function handleDownload() {
    setExporting(true)
    try {
      const res = await api.exportQuality()
      if (!res.ok) throw new Error('Export failed')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      // Registry-wide scan (no merchant input) — date-stamp the report so
      // each download is uniquely identifiable: Data_Quality_2026_08_11.xlsx
      // (en-CA formats YYYY-MM-DD in LOCAL time, not UTC).
      const today = new Date().toLocaleDateString('en-CA')
      a.download = exportFilename('data quality', today, 'Data_Quality')
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setExporting(false)
    }
  }

  const total = data?.total || 0
  const missing = data?.missing || {}
  const dupTids = data?.duplicate_tids || []
  const mxMulti = data?.mx_multiname || []
  const sheets = data?.sheets || {}

  const pctMissingNames = total ? Math.round((data?.code_names || 0) / total * 100) : 0
  const pctOrphan = total ? Math.round((data?.orphans || 0) / total * 100) : 0

  return (
    <div className="animate-fade-in-up space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[28px] font-extrabold tracking-tight text-on-surface">Data Quality</h1>
          <p className="mt-1 text-sm text-on-surface-variant">
            Real-time health assessment across active merchant registries.
          </p>
        </div>
        <button
          onClick={handleDownload}
          disabled={!data || exporting}
          className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 font-plex text-[13px] font-bold text-on-primary shadow-sm transition-all hover:opacity-90 active:scale-95 disabled:opacity-40"
        >
          <span className="msi text-[18px]">download</span>
          {exporting ? 'Building…' : 'Download Data Health Report'}
        </button>
      </div>

      {error && (
        <div className="rounded-xl border border-error/20 bg-error-container/30 p-5 text-center">
          <p className="font-plex text-sm font-semibold text-error">{error}</p>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-32 animate-pulse rounded-xl border border-outline-variant bg-white shadow-sm" />
          ))}
        </div>
      )}

      {data && (
        <>
          {/* Row 1 — stat cards */}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <StatCard
              icon="database"
              label="Total Records"
              value={total.toLocaleString()}
              desc={<span><span className="inline-block h-1.5 w-1.5 rounded-full bg-secondary align-middle" /> Synchronized</span>}
              tone="bg-primary/10 text-primary"
            />
            <StatCard
              icon="person_off"
              label="Missing Names"
              value={(data.code_names || 0).toLocaleString()}
              desc={`~${pctMissingNames}% of total records`}
              tone="bg-error-container text-error"
              valueTone="text-error"
            />
            <StatCard
              icon="link_off"
              label="Orphan Records"
              value={(data.orphans || 0).toLocaleString()}
              desc="Disconnected entities"
              tone="bg-amber-100 text-amber-800"
              valueTone="text-amber-700"
            />
          </div>

          {/* Row 2 — missing fields + right column */}
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[3fr_2fr]">
            {/* Missing critical fields */}
            <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
              <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
                <h3 className="text-sm font-bold text-on-surface">Missing Critical Fields</h3>
                <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                  {FIELD_SPECS.length} fields
                </span>
              </div>
              <table className="w-full text-left">
                <thead className="bg-surface-container">
                  <tr className="font-plex text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">
                    <th className="px-5 py-2.5">Field Name</th>
                    <th className="px-5 py-2.5">Missing Count</th>
                    <th className="px-5 py-2.5">Percentage</th>
                    <th className="px-5 py-2.5">Severity</th>
                  </tr>
                </thead>
                <tbody>
                  {FIELD_SPECS.map((f) => {
                    const cnt = missing[f.key] || 0
                    const pctNum = total ? cnt / total * 100 : 0
                    const pct = pctNum.toFixed(1)
                    return (
                      <tr key={f.key} className="border-b border-outline-variant/60 transition-colors last:border-0 hover:bg-surface-container-low/60">
                        <td className="px-5 py-3">
                          <span className="flex items-center gap-2.5 font-medium text-on-surface">
                            <span className={`flex h-7 w-7 items-center justify-center rounded-lg ${FIELD_ICON[f.color]}`}>
                              <span className="msi text-[16px]">{f.icon}</span>
                            </span>
                            {f.label}
                          </span>
                        </td>
                        <td className="px-5 py-3 font-bold text-on-surface">{cnt.toLocaleString()}</td>
                        <td className="px-5 py-3">
                          <div className="flex items-center gap-2">
                            <div className="h-1.5 w-20 overflow-hidden rounded-full bg-outline-variant">
                              <div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, pctNum)}%` }} />
                            </div>
                            <span className="font-plex text-xs text-on-surface-variant">{pct}%</span>
                          </div>
                        </td>
                        <td className="px-5 py-3">
                          <span className={`rounded-full px-2.5 py-0.5 font-plex text-[11px] font-bold ${SEV_PILL[f.severity]}`}>
                            {f.severity}
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {/* Right column */}
            <div className="flex flex-col gap-4">
              {/* Duplicate TIDs */}
              <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
                <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
                  <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
                    <span className="msi fill text-[18px] text-error">warning</span>
                    Duplicate TIDs
                  </h3>
                  <span className="rounded-md bg-surface-container-high px-2 py-0.5 font-plex text-[10px] font-bold text-on-surface-variant">
                    {dupTids.length} clusters
                  </span>
                </div>
                <div className="max-h-[220px] overflow-y-auto">
                  {dupTids.length === 0 && (
                    <p className="px-5 py-4 text-xs text-on-surface-variant">No duplicates found</p>
                  )}
                  {dupTids.slice(0, 5).map((r, i) => (
                    <div key={i} className="flex items-center justify-between border-b border-outline-variant/60 px-5 py-3 last:border-0">
                      <span className="font-mono text-[13px] font-bold text-on-surface">{r.TID || '—'}</span>
                      <span className="text-xs text-on-surface-variant">
                        Shared by <b className="text-primary">{r.Records}</b> entities
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              {/* MX codes w/ multiple names */}
              <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
                <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
                  <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
                    <span className="msi text-[18px] text-primary">dns</span>
                    MX Codes w/ Multiple Names
                  </h3>
                  <span className="rounded-md bg-surface-container-high px-2 py-0.5 font-plex text-[10px] font-bold text-on-surface-variant">
                    {mxMulti.length} flags
                  </span>
                </div>
                <div className="max-h-[220px] overflow-y-auto">
                  {mxMulti.length === 0 && (
                    <p className="px-5 py-4 text-xs text-on-surface-variant">No multi-name MX codes</p>
                  )}
                  {mxMulti.slice(0, 5).map((r, i) => (
                    <div key={i} className="flex items-center justify-between border-b border-outline-variant/60 px-5 py-3 last:border-0">
                      <span className="font-mono text-[13px] font-bold text-on-surface">{r['MX Code'] || '—'}</span>
                      <span className="text-xs text-on-surface-variant">
                        <b className="text-primary">{r['Distinct Names']}</b> names · {r.Records} records
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Engine health — self-improving harness (feature #10) */}
          <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
            <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
              <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
                <span className="msi text-[18px] text-primary">psychology</span>
                Engine Health — Alias-Free Strength
              </h3>
              <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                self-improving harness
              </span>
            </div>
            {selfImp?.report?.aggregate ? (
              <div className="grid grid-cols-2 gap-4 p-5 md:grid-cols-4">
                <div>
                  <p className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">Alias-free recall@1</p>
                  <p className="mt-1 text-2xl font-extrabold text-primary">
                    {(selfImp.report.aggregate.recall1 * 100).toFixed(1)}%
                  </p>
                  <p className="text-[11px] text-on-surface-variant">
                    {selfImp.report.aggregate.found1}/{selfImp.report.aggregate.n} merchants found without aliases
                  </p>
                </div>
                <div>
                  <p className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">Avg accuracy</p>
                  <p className="mt-1 text-2xl font-extrabold text-on-surface">{selfImp.report.aggregate.avg_score}/10</p>
                  <p className="text-[11px] text-on-surface-variant">raw engine, no hand-added mappings</p>
                </div>
                <div>
                  <p className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">Baseline recall@1</p>
                  <p className="mt-1 text-2xl font-extrabold text-on-surface">
                    {selfImp.baseline ? `${(selfImp.baseline.recall1 * 100).toFixed(1)}%` : '—'}
                  </p>
                  <p className="text-[11px] text-on-surface-variant">
                    {selfImp.baseline ? 'regression gate target' : 'first run not recorded yet'}
                  </p>
                </div>
                <div>
                  <p className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">Auto-suggested aliases</p>
                  <p className="mt-1 text-2xl font-extrabold text-on-surface">
                    {selfImp.report.suggested_count || 0}
                  </p>
                  <p className="text-[11px] text-on-surface-variant">dropped into the Alias Review queue</p>
                </div>
              </div>
            ) : (
              <p className="px-5 py-4 text-xs text-on-surface-variant">
                No harness run yet — run <code className="rounded bg-surface-container-high px-1.5 py-0.5 font-mono">python scripts/self_improve.py</code> (or <code className="rounded bg-surface-container-high px-1.5 py-0.5 font-mono">app.start rebuild</code>) to measure the raw engine.
              </p>
            )}
          </div>

          {/* Row 3 — records per sheet */}
          <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
            <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
              <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
                <span className="msi text-[18px] text-secondary">table_rows</span>
                Records per Sheet
              </h3>
              <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                {Object.keys(sheets).length} sources
              </span>
            </div>
            <div className="max-h-[320px] overflow-y-auto">
              <table className="w-full text-left">
                <thead className="sticky top-0 bg-surface-container">
                  <tr className="font-plex text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">
                    <th className="px-5 py-2.5">Source</th>
                    <th className="px-5 py-2.5">Records</th>
                    <th className="px-5 py-2.5">Share</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(sheets).map(([sheet, n]) => {
                    const pct = total ? (n / total * 100) : 0
                    return (
                      <tr key={sheet} className="border-b border-outline-variant/60 transition-colors last:border-0 hover:bg-surface-container-low/60">
                        <td className="px-5 py-2.5 text-[13px] font-medium text-on-surface">{sheet || '—'}</td>
                        <td className="px-5 py-2.5 font-bold text-on-surface">{n.toLocaleString()}</td>
                        <td className="px-5 py-2.5">
                          <div className="flex items-center gap-2">
                            <div className="h-1.5 w-24 overflow-hidden rounded-full bg-outline-variant">
                              <div className="h-full rounded-full bg-secondary" style={{ width: `${Math.min(100, pct)}%` }} />
                            </div>
                            <span className="font-plex text-xs text-on-surface-variant">{pct.toFixed(1)}%</span>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {/* Identifier classifier debug — always available, independent of /api/quality */}
      <ClassifierDebugCard />
    </div>
  )
}
