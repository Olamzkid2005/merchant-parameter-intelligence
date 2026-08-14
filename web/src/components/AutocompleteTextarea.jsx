import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import HighlightedPrefix from './HighlightedPrefix'

/**
 * Textarea with a live merchant-name autocomplete dropdown for the LINE
 * currently being typed (the text after the last newline), mirroring the
 * Search page's typeahead: debounced 180ms bucket lookups, ↑↓/↵/esc keyboard
 * navigation, prefix highlighting, and suppression so the dropdown never pops
 * back open over fresh results after the current line is completed.
 *
 * Picking a suggestion replaces only the active line, leaving the rest of the
 * pasted block intact — the natural behaviour for batch/paste inputs.
 */
export default function AutocompleteTextarea({
  value,
  onChange,
  rows = 6,
  placeholder = '',
  mono = false,
  className = '',
}) {
  const ref = useRef(null)
  const suppressRef = useRef(false)
  const [acItems, setAcItems] = useState([])
  const [showAc, setShowAc] = useState(false)
  const [acIndex, setAcIndex] = useState(-1)

  // The prefix being completed is the active (last) line of the block.
  const activeLine = String(value || '').split('\n').pop() || ''

  // Debounced typeahead for the active line only.
  useEffect(() => {
    const q = activeLine.trim()
    if (q.length < 2) {
      setAcItems([])
      setShowAc(false)
      return
    }
    const t = setTimeout(() => {
      // A line was just completed from the dropdown — skip this fetch or the
      // list would pop back open over the finished line.
      if (suppressRef.current) {
        suppressRef.current = false
        setAcItems([])
        return
      }
      api.autocomplete(q)
        .then((d) => {
          if (suppressRef.current || document.activeElement !== ref.current) {
            setAcItems([])
            return
          }
          const items = d.suggestions || []
          setAcItems(items)
          setShowAc(items.length > 0)
          setAcIndex(-1)
        })
        .catch(() => {
          setAcItems([])
          setShowAc(false)
        })
    }, 180)
    return () => clearTimeout(t)
  }, [activeLine])

  // Replace only the active line with the picked suggestion, keep the cursor
  // at the end of it, and suppress the immediate re-fetch.
  function pick(s) {
    const lines = String(value || '').split('\n')
    lines[lines.length - 1] = s
    const next = lines.join('\n')
    suppressRef.current = true
    setShowAc(false)
    setAcIndex(-1)
    onChange(next)
    requestAnimationFrame(() => {
      const el = ref.current
      if (!el) return
      el.focus()
      const pos = next.length
      el.setSelectionRange(pos, pos)
    })
  }

  // Keyboard navigation for the dropdown. Without a highlighted item, Enter
  // falls through to the textarea's default (newline).
  function handleKeyDown(e) {
    if (!showAc || acItems.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setAcIndex((i) => (i + 1) % acItems.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setAcIndex((i) => (i <= 0 ? acItems.length - 1 : i - 1))
    } else if (e.key === 'Enter' && acIndex >= 0 && acItems[acIndex]) {
      e.preventDefault()
      pick(acItems[acIndex])
    } else if (e.key === 'Escape') {
      setShowAc(false)
      setAcIndex(-1)
    }
  }

  return (
    <div className="relative">
      <textarea
        ref={ref}
        value={value}
        onChange={(e) => {
          suppressRef.current = false // typing re-enables autocomplete
          onChange(e.target.value)
        }}
        onFocus={() => {
          if (acItems.length) setShowAc(true)
        }}
        onBlur={() => {
          setTimeout(() => {
            setShowAc(false)
            setAcIndex(-1)
          }, 150)
        }}
        onKeyDown={handleKeyDown}
        rows={rows}
        placeholder={placeholder}
        className={`w-full resize-none rounded-t-2xl bg-transparent p-5 text-sm outline-none ${mono ? 'font-mono' : ''} ${className}`}
      />

      {/* Live autocomplete dropdown */}
      {showAc && acItems.length > 0 && (
        <div className="absolute left-0 right-0 top-full z-30 mt-1.5 overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-xl animate-fade-in-up">
          <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-4 py-2">
            <span className="font-plex text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
              Suggestions
            </span>
            <span className="font-plex text-[10px] text-outline">{acItems.length}</span>
          </div>
          {acItems.map((s, i) => (
            <button
              key={s}
              type="button"
              onMouseDown={(e) => {
                // preventDefault keeps focus so blur doesn't hide the list
                // before the click registers
                e.preventDefault()
                pick(s)
              }}
              onMouseEnter={() => setAcIndex(i)}
              className={`flex w-full items-center gap-3 px-4 py-2.5 text-left text-[13px] transition-colors ${
                i === acIndex
                  ? 'bg-surface-container text-primary'
                  : 'text-on-surface hover:bg-surface-container'
              }`}
            >
              <span className="msi text-[16px] text-outline">auto_awesome</span>
              <span className="truncate font-semibold">
                <HighlightedPrefix text={s} prefix={activeLine} />
              </span>
              <span className="ml-auto font-plex text-[10px] text-outline">↵</span>
            </button>
          ))}
          <div className="flex items-center gap-3 border-t border-outline-variant bg-surface-container-low px-4 py-1.5">
            <span className="font-plex text-[10px] text-outline">
              <b>↑↓</b> navigate · <b>↵</b> insert · <b>esc</b> close
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
