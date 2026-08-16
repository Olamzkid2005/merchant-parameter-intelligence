import { useEffect, useState } from 'react'
import { api } from '../api'

function fmtTime(iso) {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    })
  } catch {
    return String(iso)
  }
}

function fmtBytes(n) {
  if (!n) return '—'
  const mb = n / (1024 * 1024)
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.round(n / 1024)} KB`
}

export default function IngestionLedgerCard() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  async function load() {
    setErr(null)
    try {
      setData(await api.ingest(15))
    } catch (e) {
      setErr(String(e.message || e))
    }
  }

  useEffect(() => {
    load()
  }, [])

  const runs = data?.runs || []
  const stats = data?.stats || {}
  const fresh = data?.freshness || {}
  const stale = fresh.stale_sources || []
  const lastRun = fresh.last_ok_run

  return (
    <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
      <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
        <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
          <span className="msi text-[18px] text-primary">database</span>
          Data freshness &amp; ingestion ledger
        </h3>
        <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
          roadmap item #2 · append-only
        </span>
      </div>

      <div className="space-y-4 px-5 py-4">
        {err && (
          <p className="rounded-lg bg-error-container/40 px-4 py-2 font-plex text-[12px] font-bold text-error">
            {err}
          </p>
        )}

        {/* Freshness banner */}
        <div className={`flex flex-wrap items-center gap-3 rounded-lg px-4 py-3 ${
          stale.length === 0 ? 'bg-green-100/70' : 'bg-amber-100/80'
        }`}>
          <span className={`msi text-[20px] ${stale.length === 0 ? 'text-green-700' : 'text-amber-700'}`}>
            {stale.length === 0 ? 'check_circle' : 'warning'}
          </span>
          <div className="min-w-0 flex-1">
            <p className={`font-plex text-[12px] font-bold ${stale.length === 0 ? 'text-green-900' : 'text-amber-900'}`}>
              {stale.length === 0
                ? 'All Excel sources are current — the database matches every file in data/.'
                : `${stale.length} source file${stale.length === 1 ? ' is' : 's are'} newer than the last rebuild — run a rebuild to pick them up.`}
            </p>
            <p className="font-plex text-[11px] text-on-surface-variant">
              Last good build: <b>{fmtTime(lastRun?.finished_at)}</b>
              {lastRun?.row_count ? ` · ${lastRun.row_count.toLocaleString()} rows in db` : ''}
              {fresh.source_count ? ` · ${fresh.source_count} source files` : ''}
            </p>
          </div>
          {stats.runs > 0 && (
            <div className="flex items-center gap-2">
              <span className="rounded-full bg-surface-container-high px-3 py-1 font-plex text-[11px] font-bold text-on-surface-variant">
                {stats.runs} runs · {stats.ok} ok · {stats.failed} failed
              </span>
            </div>
          )}
        </div>

        {/* Stale sources */}
        {stale.length > 0 && (
          <div>
            <p className="mb-1.5 font-plex text-[11px] font-bold uppercase tracking-wider text-on-surface-variant">
              Changed / new since last build
            </p>
            <div className="space-y-1">
              {stale.map((s) => (
                <div key={s.name} className="flex items-center justify-between gap-3 rounded-md bg-surface-container-low px-3 py-1.5 font-mono text-[11px]">
                  <span className="min-w-0 truncate text-on-surface">{s.name.split('/').pop()}</span>
                  <span className="flex shrink-0 items-center gap-2">
                    <span className="text-outline">{fmtBytes(s.size)}</span>
                    <span className={`rounded-full px-2 py-0.5 font-plex text-[10px] font-bold ${
                      s.status === 'new' ? 'bg-blue-100 text-blue-800' : 'bg-amber-100 text-amber-800'
                    }`}>
                      {s.status}
                    </span>
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Recent runs */}
        <div>
          <p className="mb-1.5 font-plex text-[11px] font-bold uppercase tracking-wider text-on-surface-variant">
            Recent rebuild runs
          </p>
          {runs.length === 0 ? (
            <p className="rounded-md bg-surface-container-low px-3 py-3 text-center font-plex text-[12px] text-on-surface-variant">
              No rebuilds recorded yet — run <code className="font-mono">python app.start rebuild</code> and it will appear here.
            </p>
          ) : (
            <div className="max-h-56 overflow-auto rounded-md border border-outline-variant">
              <table className="w-full text-left font-mono text-[11px]">
                <thead className="sticky top-0 bg-surface-container-low text-on-surface-variant">
                  <tr>
                    <th className="px-3 py-1.5 font-bold">finished</th>
                    <th className="px-2 py-1.5 font-bold">status</th>
                    <th className="px-2 py-1.5 font-bold">pipeline</th>
                    <th className="px-2 py-1.5 font-bold">rows</th>
                    <th className="px-3 py-1.5 font-bold">detail</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.id} className="border-t border-outline-variant/60">
                      <td className="whitespace-nowrap px-3 py-1.5 text-on-surface">
                        {fmtTime(r.finished_at)}
                      </td>
                      <td className="px-2 py-1.5">
                        <span className={`rounded-full px-2 py-0.5 font-plex text-[10px] font-bold ${
                          r.status === 'ok' ? 'bg-green-100 text-green-800' : 'bg-error-container/60 text-error'
                        }`}>
                          {r.status}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-2 py-1.5 text-on-surface-variant">{r.pipeline}</td>
                      <td className="px-2 py-1.5 text-on-surface">{r.row_count || '—'}</td>
                      <td className="max-w-[260px] truncate px-3 py-1.5 text-outline">{r.detail || ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <p className="font-mono text-[10px] text-outline">
          {data?.file || ''}
        </p>
      </div>
    </div>
  )
}
