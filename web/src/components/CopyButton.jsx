import { useState } from 'react'

/* Copy-to-clipboard button with a brief 'copied' confirmation state. */
function fallbackCopy(text, done) {
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  try {
    document.execCommand('copy')
  } catch {
    /* ignore */
  }
  document.body.removeChild(ta)
  done()
}

export default function CopyButton({ value, label }) {
  const [copied, setCopied] = useState(false)
  if (!value) return null
  const doCopy = (e) => {
    e.stopPropagation()
    e.preventDefault()
    const text = String(value)
    const done = () => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done))
    } else {
      fallbackCopy(text, done)
    }
  }
  return (
    <button
      type="button"
      onClick={doCopy}
      title={`Copy ${label}`}
      aria-label={`Copy ${label}`}
      className="rounded p-0.5 text-outline transition-colors hover:bg-surface-variant hover:text-primary"
    >
      <span className={`msi text-[15px] ${copied ? 'text-secondary' : ''}`}>
        {copied ? 'check' : 'content_copy'}
      </span>
    </button>
  )
}
