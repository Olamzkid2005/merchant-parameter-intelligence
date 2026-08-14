// Shared match-display helpers (used by SearchPage and BatchPage).

export function scoreTone(score) {
  if (score >= 8.5) return 'bg-green-100 text-green-800'
  if (score >= 7) return 'bg-orange-100 text-orange-800'
  if (score >= 5) return 'bg-slate-100 text-slate-800'
  return 'bg-red-100 text-red-800'
}

export function pillTone(type) {
  const t = String(type || '').toLowerCase()
  if (t.includes('exact')) return 'bg-green-100 text-green-900 border-green-200'
  if (t.includes('high')) return 'bg-orange-50 text-orange-900 border-orange-100'
  if (t.includes('alias')) return 'bg-blue-50 text-blue-900 border-blue-100'
  if (t.includes('possible')) return 'bg-slate-50 text-slate-900 border-slate-100'
  return 'bg-red-50 text-red-900 border-red-100'
}
