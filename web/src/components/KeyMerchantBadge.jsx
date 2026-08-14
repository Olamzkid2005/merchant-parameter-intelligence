/* Key-merchant family badge (shared across Search / Similar / Batch). */

// Same visual language everywhere: a tertiary storefront chip naming the
// family root (MEDPLUS, LAGOON WATERS, JUST CHIPS…). The roots come from the
// backend's key_merchant_matches() (via /api/search -> res.key_merchants,
// /api/similar and /api/batch rows), so every badge agrees with task routing.
//
// Clickable: opens that family's 360° profile (every linked record via
// shared identifiers). Implemented as a role="button" SPAN, not a <button>
// — the badge often lives inside another interactive row (expand/collapse
// button, clickable row), and nested buttons are invalid HTML.
export default function KeyMerchantBadge({ roots, onOpenProfile }) {
  if (!roots?.length) return null
  const family = roots.join(' · ')
  const familyRoot = roots[0] || family
  return (
    <span
      role="button"
      tabIndex={0}
      onClick={(e) => {
        e.stopPropagation()
        e.preventDefault()
        onOpenProfile?.(familyRoot)
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.stopPropagation()
          e.preventDefault()
          onOpenProfile?.(familyRoot)
        }
      }}
      title={`Part of the ${family} key-merchant family — every ${family} record routes together in the engine. Click to open the ${familyRoot} family profile.`}
      className="flex max-w-[200px] shrink-0 cursor-pointer items-center gap-1 rounded-full border border-tertiary/30 bg-tertiary/10 px-2 py-1 font-plex text-[10px] font-bold uppercase tracking-tighter text-tertiary transition-colors hover:border-tertiary hover:bg-tertiary/20 hover:text-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-tertiary/60"
    >
      <span className="msi text-[12px]">storefront</span>
      <span className="truncate">{family}</span>
      <span className="msi text-[12px] opacity-60">open_in_new</span>
    </span>
  )
}
