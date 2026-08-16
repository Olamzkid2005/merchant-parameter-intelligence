import { useEffect, useState } from 'react'
import { api } from '../api'
import IngestionLedgerCard from '../components/IngestionLedgerCard'

export default function AuditPage() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)
  const [action, setAction] = useState('')

  async function load(act = action) {
    setLoading(true)
    setErr(null)
    try {
      setData(await api.audit(300, act))
    } catch (e) {
      setErr(String(e.message || e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const entries = data?.entries || []
  const stats = data?.stats || {}
  const actions = Object.keys(stats.by_action || {}).sort()

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-plex text-lg font-bold text-on-surface">Audit Trail</h2>
          <p className="font-plex text-[12px] text-on-surface-variant">
            Append-only log of searches, profile views, exports, and intent executions
            (roadmap item #1 — no update/delete path exists).
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={action}
            onChange={(e) => { setAction(e.target.value); load(e.target.value) }}
            className="rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-2 font-plex text-[12px] font-bold text-on-surface shadow-sm outline-none focus:border-primary"
          >
            <option value="">all actions</option>
            {actions.map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
          <button
            onClick={() => load()}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-2 font-plex text-[12px] font-bold text-on-surface-variant transition-all hover:border-primary hover:text-primary active:scale-95 disabled:opacity-40"
          >
            <span className="msi text-[15px]">{loading ? 'hourglass_top' : 'refresh'}</span>
            Refresh
          </button>
        </div>
      </div>

      {err && (
        <p className="rounded-lg bg-error-container/40 px-4 py-2 font-plex text-[12px] font-bold text-error">
          {err}
        </p>
      )}

      <IngestionLedgerCard />

      {data && (
        <div className="flex flex-wrap items-center gap-3">
          <span className="rounded-full bg-surface-container-high px-3 py-1 font-plex text-[11px] font-bold text-on-surface-variant">
            {stats.total || 0} entries
          </span>
          <span className={`rounded-full px-3 py-1 font-plex text-[11px] font-bold ${
            (stats.last_24h || 0) > 0 ? 'bg-green-100 text-green-800' : 'bg-surface-container-high text-on-surface-variant'
          }`}>
            {stats.last_24h || 0} in last 24h
          </span>
          {Object.entries(stats.by_action || {}).map(([a, n]) => (
            <span key={a} className="rounded-full bg-surface-container-high px-3 py-1 font-plex text-[11px] font-bold text-on-surface-variant">
              {a}: <b className="text-primary">{n}</b>
            </span>
          ))}
          <span className="font-mono text-[10px] text-outline">
            {data.file}
          </span>
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
        <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
          <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
            <span className="msi text-[18px] text-primary">history</span>
            Recent entries
          </h3>
          <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
            newest first · append-only
          </span>
        </div>
        <div className="max-h-[62vh] overflow-auto">
          {entries.length === 0 ? (
            <p className="py-10 text-center font-plex text-[12px] text-on-surface-variant">
              {loading ? 'Loading…' : 'No audit entries yet — run a search or open a profile.'}
            </p>
          ) : (
            <table className="w-full text-left font-mono text-[11px]">
              <thead className="sticky top-0 bg-surface-container-low text-on-surface-variant">
                <tr>
                  <th className="px-4 py-2 font-bold">time (UTC)</th>
                  <th className="px-2 py-2 font-bold">action</th>
                  <th className="px-2 py-2 font-bold">actor</th>
                  <th className="px-4 py-2 font-bold">scope</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e) => {
                  let scopeText = e.scope || ''
                  try {
                    const s = JSON.parse(scopeText)
                    if (s && typeof s === 'object') {
                      scopeText = Object.entries(s)
                        .map(([k, v]) => `${k}=${String(v).slice(0, 80)}`)
                        .join(' · ')
                    }
                  } catch { /* keep raw */ }
                  return (
                    <tr key={e.id} className="border-t border-outline-variant/60 hover:bg-surface-container-low/60">
                      <td className="whitespace-nowrap px-4 py-1.5 text-outline">{e.ts}</td>
                      <td className="px-2 py-1.5">
                        <span className="rounded bg-primary/10 px-1.5 py-0.5 font-bold text-primary">{e.action}</span>
                      </td>
                      <td className="px-2 py-1.5">{e.actor}</td>
                      <td className="px-4 py-1.5 text-on-surface-variant">{scopeText || '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}
