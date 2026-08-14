/* Renders a suggestion with its typed prefix highlighted in bold. Matches
   case-insensitively (bucket keys are canonical/uppercased) and falls back
   to plain text when the prefix isn't found or is empty. */
export default function HighlightedPrefix({ text, prefix }) {
  const t = String(text || '')
  const p = String(prefix || '').trim()
  if (!p) return t
  const idx = t.toLowerCase().indexOf(p.toLowerCase())
  if (idx < 0) return t
  return (
    <>
      {t.slice(0, idx)}
      <mark className="rounded bg-primary/15 px-0.5 font-extrabold text-primary">
        {t.slice(idx, idx + p.length)}
      </mark>
      {t.slice(idx + p.length)}
    </>
  )
}
