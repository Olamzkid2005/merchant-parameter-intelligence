import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'

/**
 * Alias Review Queue.
 *
 * Lists every alias the engine knows (manual from config + auto-learned)
 * and lets the user approve or reject the learned ones. Approving marks the
 * mapping as trusted; rejecting forgets it so it stops matching. Manual
 * aliases are read-only.
 */

function AliasRow({ item, onApprove, onReject, busy }) {
  const isManual = item.status === 'manual'
  const isApproved = item.status === 'approved'
  return (
    <div className="flex items-center justify-between gap-4 border-b border-outline-variant/60 px-5 py-3 transition-colors last:border-0 hover:bg-surface-container-low/60">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate font-plex text-[13px] font-bold text-primary">{item.alias}</span>
          {isManual && (
            <span className="shrink-0 rounded-full border border-outline-variant bg-surface-container px-2 py-0.5 font-plex text-[10px] font-bold uppercase tracking-wider text-on-surface-variant">
              manual
            </span>
          )}
          {isApproved && (
            <span className="shrink-0 rounded-full border border-secondary/25 bg-secondary/10 px-2 py-0.5 font-plex text-[10px] font-bold uppercase tracking-wider text-secondary">
              approved
            </span>
          )}
          {item.status === 'pending' && (
            <span className="shrink-0 rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 font-plex text-[10px] font-bold uppercase tracking-wider text-amber-700">
              pending
            </span>
          )}
        </div>
        <div className="mt-0.5 truncate text-xs text-on-surface-variant">
          → {item.canonical}
        </div>
      </div>
      <div className="flex shrink-0 gap-2">
        {isManual ? (
          <span className="font-plex text-[10px] text-outline">from config</span>
        ) : isApproved ? (
          <button
            onClick={() => onReject(item)}
            disabled={busy}
            className="rounded-lg border border-error/30 px-3 py-1.5 font-plex text-[11px] font-bold text-error transition-colors hover:bg-error-container/40 disabled:opacity-40"
          >
            Reject
          </button>
        ) : (
          <>
            <button
              onClick={() => onApprove(item)}
              disabled={busy}
              className="rounded-lg border border-secondary/30 bg-secondary/10 px-3 py-1.5 font-plex text-[11px] font-bold text-secondary transition-colors hover:bg-secondary/20 disabled:opacity-40"
            >
              Approve
            </button>
            <button
              onClick={() => onReject(item)}
              disabled={busy}
              className="rounded-lg border border-outline-variant px-3 py-1.5 font-plex text-[11px] font-bold text-on-surface-variant transition-colors hover:bg-surface-container disabled:opacity-40"
            >
              Reject
            </button>
          </>
        )}
      </div>
    </div>
  )
}

function StatCard({ label, value, tone }) {
  return (
    <div className="rounded-xl border border-outline-variant bg-surface-container-lowest p-4 shadow-sm">
      <div className="font-plex text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
        {label}
      </div>
      <div className={`mt-1 text-2xl font-extrabold tracking-tight ${tone || 'text-on-surface'}`}>{value}</div>
    </div>
  )
}

export default function AliasReviewPage() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [view, setView] = useState('pending')
  const [filter, setFilter] = useState('')

  async function refresh() {
    try {
      const d = await api.aliases()
      setData(d)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  async function act(fn, item) {
    setBusy(true)
    try {
      await fn(item.alias, item.canonical)
      await refresh()
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  const approve = (item) => act(api.aliasApprove, item)
  const reject = (item) => act(api.aliasReject, item)

  const items = useMemo(() => {
    if (!data) return []
    let list = view === 'manual' ? data.manual || [] : data.learned || []
    if (view === 'pending') list = list.filter((i) => i.status === 'pending')
    if (view === 'approved') list = list.filter((i) => i.status === 'approved')
    if (filter) {
      const f = filter.toLowerCase()
      list = list.filter((i) => i.alias.toLowerCase().includes(f) || i.canonical.toLowerCase().includes(f))
    }
    return list
  }, [data, view, filter])

  const counts = data?.counts || {}

  return (
    <div className="animate-fade-in-up space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-[28px] font-extrabold tracking-tight text-on-surface">Alias Review</h1>
        <p className="mt-1 text-sm text-on-surface-variant">
          Review what the engine has learned. Approve trusted mappings, reject anything that looks wrong.
        </p>
      </div>

      {error && (
        <div className="rounded-xl border border-error/20 bg-error-container/30 p-5 text-center">
          <p className="font-plex text-sm font-semibold text-error">{error}</p>
        </div>
      )}

      {/* Stats */}
      {data && (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <StatCard label="Learned Aliases" value={counts.learned} />
          <StatCard label="Pending Review" value={counts.pending} tone="text-amber-600" />
          <StatCard label="Approved" value={counts.approved} tone="text-secondary" />
          <StatCard label="Manual (config)" value={counts.manual} />
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex w-fit overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest p-1">
          {[
            { key: 'pending', label: 'Pending' },
            { key: 'approved', label: 'Approved' },
            { key: 'learned', label: 'All Learned' },
            { key: 'manual', label: 'Manual' },
          ].map((v) => (
            <button
              key={v.key}
              onClick={() => setView(v.key)}
              className={`rounded-md px-4 py-1.5 font-plex text-[12px] font-bold transition-colors ${
                view === v.key ? 'bg-primary text-on-primary shadow-sm' : 'text-on-surface-variant hover:bg-surface-container'
              }`}
            >
              {v.label}
            </button>
          ))}
        </div>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter aliases…"
          className="w-64 rounded-xl border border-outline-variant bg-surface-container-lowest px-4 py-2 text-sm shadow-sm outline-none transition-all focus:border-primary focus:ring-4 focus:ring-primary/20"
        />
      </div>

      {/* Loading */}
      {loading && (
        <div className="space-y-2">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="h-14 animate-pulse rounded-xl border border-outline-variant bg-white shadow-sm" />
          ))}
        </div>
      )}

      {/* List */}
      {!loading && data && (
        <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
          <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
            <h3 className="text-sm font-bold text-on-surface">
              {view === 'manual' ? 'Manual aliases' : 'Learned aliases'}
            </h3>
            <span className="rounded-md bg-surface-container-high px-2 py-0.5 font-plex text-[10px] font-bold text-on-surface-variant">
              {items.length} items
            </span>
          </div>
          {items.length === 0 ? (
            <p className="px-5 py-8 text-center text-sm text-on-surface-variant">
              {view === 'pending'
                ? 'Nothing pending — every learned alias has been reviewed.'
                : 'No aliases in this view.'}
            </p>
          ) : (
            <div className="max-h-[560px] overflow-y-auto">
              {items.map((item, i) => (
                <AliasRow
                  key={`${item.canonical}|${item.alias}|${i}`}
                  item={item}
                  onApprove={approve}
                  onReject={reject}
                  busy={busy}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
