import { useEffect, useRef, useState } from 'react'

// Shared clipboard + CSV/TSV helpers used by every result table (task
// results, quick match, batch search). Booleans render as TRUE/FALSE in CSV
// and Yes/No in TSV so change-detected flags survive a paste.

export function csvCell(v) {
  if (v === true) return 'TRUE'
  if (v === false) return 'FALSE'
  const s = v === null || v === undefined ? '' : String(v)
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s
}

// Tab-separated row with a header line — pastes straight into Excel columns.
// Tabs inside values are flattened to a space so columns never shift.
export function rowTsv(headers, getValue) {
  const vals = headers.map((h) => {
    const v = getValue(h)
    if (v === true) return 'Yes'
    if (v === false) return 'No'
    return v === null || v === undefined ? '' : String(v).replace(/\t/g, ' ')
  })
  return headers.map((h) => csvCell(h)).join('\t') + '\n' + vals.map(csvCell).join('\t')
}

export function rowsCsv(rows, headers, getValue) {
  const header = headers.map(csvCell).join(',')
  const lines = rows.map((r) => headers.map((h) => csvCell(getValue(r, h))).join(','))
  return [header, ...lines].join('\n')
}

// Copy to the clipboard, falling back to execCommand for browsers that
// reject the async Clipboard API (large payloads, non-secure contexts).
// Resolves true only when the copy actually succeeded.
export async function copyTextToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    let ok = false
    try {
      ok = document.execCommand('copy')
    } catch {
      ok = false
    }
    document.body.removeChild(ta)
    return ok
  }
}

// Transient copy-confirmation state: `copied` holds the last key that was
// flashed ('all' or `row-${i}`) for ~1.6s, then resets. Timer is cleaned up
// on unmount so no stale setState fires.
export function useCopyIndicator() {
  const [copied, setCopied] = useState('')
  const timer = useRef(null)
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current)
  }, [])
  return {
    copied,
    indicate(key) {
      setCopied(key)
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(''), 1600)
    },
  }
}
