// Shared pagination footer for result tables (task results, quick match,
// batch search). Renders nothing when the result set fits on a single page.
// Props: total, page, setPage, pageSize, setPageSize, sizes (default
// [25, 50, 100]).
export default function TablePagination({ total, page, setPage, pageSize, setPageSize, sizes = [25, 50, 100] }) {
  if (total <= pageSize) return null
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const safe = Math.min(page, pageCount - 1)
  return (
    <div className="flex items-center justify-between bg-surface-container-low px-6 py-3 text-xs text-on-surface-variant">
      <span>
        Showing {safe * pageSize + 1}–{Math.min(total, (safe + 1) * pageSize)} of {total}
      </span>
      <div className="flex items-center gap-2">
        <select
          value={pageSize}
          onChange={(e) => {
            setPageSize(Number(e.target.value))
            setPage(0)
          }}
          className="rounded-md border border-outline-variant bg-surface-container-lowest px-2 py-1 font-plex text-[11px] font-bold outline-none"
          title="Rows per page"
        >
          {sizes.map((n) => (
            <option key={n} value={n}>{n} / page</option>
          ))}
        </select>
        <button
          onClick={() => setPage((p) => Math.max(0, p - 1))}
          disabled={safe === 0}
          className="rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-1 font-plex text-[11px] font-bold transition-colors hover:border-primary hover:text-primary disabled:opacity-40"
        >
          Previous
        </button>
        <button
          onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
          disabled={safe >= pageCount - 1}
          className="rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-1 font-plex text-[11px] font-bold transition-colors hover:border-primary hover:text-primary disabled:opacity-40"
        >
          Next
        </button>
      </div>
    </div>
  )
}
