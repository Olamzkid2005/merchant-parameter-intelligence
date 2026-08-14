import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import HighlightedPrefix from './HighlightedPrefix'

/* Reusable merchant search input with live typeahead suggestions.
   Used by the Profile page (single + compare modes).
   Props: value, onChange(text), onSearch(text), placeholder, icon, size. */
export default function MerchantAutocomplete({
  value,
  onChange,
  onSearch,
  placeholder = 'Search by name, email, phone, TID or MX code…',
  icon = 'person_search',
  size = 'lg',
}) {
  const inputRef = useRef(null)
  const [acItems, setAcItems] = useState([])
  const [showAc, setShowAc] = useState(false)
  const [acIndex, setAcIndex] = useState(-1)
  const suppressAcRef = useRef(false)

  // Live typeahead
  useEffect(() => {
    const q = String(value || '').trim()
    if (q.length < 2) {
      setAcItems([])
      setShowAc(false)
      return
    }
    const t = setTimeout(() => {
      if (suppressAcRef.current) {
        suppressAcRef.current = false
        setAcItems([])
        return
      }
      api.autocomplete(q)
        .then((d) => {
          if (suppressAcRef.current || document.activeElement !== inputRef.current) {
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
  }, [value])

  function pickAc(s) {
    suppressAcRef.current = true
    onChange(s)
    setShowAc(false)
    setAcIndex(-1)
    onSearch(s)
  }

  function handleKeyDown(e) {
    if (showAc && acItems.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setAcIndex((i) => (i + 1) % acItems.length)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setAcIndex((i) => (i <= 0 ? acItems.length - 1 : i - 1))
        return
      }
      if (e.key === 'Escape') {
        setShowAc(false)
        setAcIndex(-1)
        return
      }
      if (e.key === 'Enter' && acIndex >= 0 && acItems[acIndex]) {
        e.preventDefault()
        pickAc(acItems[acIndex])
        return
      }
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      if (String(value || '').trim()) onSearch(String(value).trim())
    }
  }

  const lg = size === 'lg'

  return (
    <div className="relative">
      <div className="relative">
        <span className="pointer-events-none absolute inset-y-0 left-4 flex items-center text-outline">
          <span className={`msi ${lg ? 'text-[24px]' : 'text-[20px]'}`}>{icon}</span>
        </span>
        <input
          ref={inputRef}
          value={value || ''}
          onChange={(e) => {
            suppressAcRef.current = false
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
          placeholder={placeholder}
          className={`w-full rounded-2xl border border-outline-variant bg-surface-container-lowest shadow-sm outline-none transition-all focus:border-primary focus:ring-4 focus:ring-primary/20 ${
            lg ? 'py-4 pl-12 pr-28 text-base' : 'py-3 pl-11 pr-24 text-sm'
          }`}
        />
        <div className="absolute inset-y-0 right-4 flex items-center">
          <button
            type="button"
            onClick={() => {
              if (String(value || '').trim()) onSearch(String(value).trim())
            }}
            className="rounded-xl bg-primary px-4 py-2 font-plex text-[13px] font-bold text-on-primary transition-opacity hover:opacity-90 active:scale-95"
          >
            Search
          </button>
        </div>
      </div>

      {showAc && acItems.length > 0 && (
        <div className="absolute left-0 right-0 top-[calc(100%+6px)] z-30 overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-xl animate-fade-in-up">
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
                e.preventDefault()
                pickAc(s)
              }}
              onMouseEnter={() => setAcIndex(i)}
              className={`flex w-full items-center gap-3 px-4 py-2.5 text-left text-[13px] transition-colors ${
                i === acIndex ? 'bg-surface-container text-primary' : 'text-on-surface hover:bg-surface-container'
              }`}
            >
              <span className="msi text-[16px] text-outline">auto_awesome</span>
              <span className="truncate font-semibold">
                <HighlightedPrefix text={s} prefix={String(value || '')} />
              </span>
              <span className="ml-auto font-plex text-[10px] text-outline">↵</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
