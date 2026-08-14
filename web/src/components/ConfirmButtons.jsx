import { useState } from 'react'
import { api } from '../api'

/**
 * Build a toast notification using DOM APIs (textContent) so user-supplied
 * strings (query, merchant name) can never be interpreted as HTML.
 */
function showToast(title, msg, kind = 'success') {
  const toast = document.createElement('div')
  toast.className =
    'fixed bottom-6 right-6 z-50 flex items-center gap-4 rounded-xl px-6 py-4 shadow-2xl animate-fade-in-up ' +
    (kind === 'success' ? 'bg-inverse-surface text-inverse-on-surface'
      : kind === 'warn' ? 'bg-amber-600 text-white'
      : 'bg-error text-on-error')

  const iconWrap = document.createElement('div')
  const flag = kind === 'flag'
  const warn = kind === 'warn'
  iconWrap.className =
    `flex h-8 w-8 items-center justify-center rounded-full ` +
    (flag ? 'bg-error' : warn ? 'bg-amber-500' : 'bg-secondary')
  const icon = document.createElement('span')
  icon.className = `msi fill text-[20px] ${flag ? 'text-on-error' : warn ? 'text-white' : 'text-on-secondary'}`
  icon.textContent = flag ? 'flag' : warn ? 'flag' : 'check_circle'
  iconWrap.appendChild(icon)

  const copy = document.createElement('div')
  const t = document.createElement('p')
  t.className = 'text-sm font-bold'
  t.textContent = title
  const m = document.createElement('p')
  m.className = 'text-xs opacity-80'
  m.textContent = msg
  copy.appendChild(t)
  copy.appendChild(m)

  const close = document.createElement('button')
  close.className = 'ml-2 rounded-full p-1 hover:bg-white/10'
  close.setAttribute('aria-label', 'Dismiss')
  const closeIcon = document.createElement('span')
  closeIcon.className = 'msi text-[18px]'
  closeIcon.textContent = 'close'
  close.appendChild(closeIcon)

  toast.appendChild(iconWrap)
  toast.appendChild(copy)
  toast.appendChild(close)
  document.body.appendChild(toast)

  const remove = () => toast.remove()
  close.addEventListener('click', remove)
  setTimeout(remove, 4500)
}

export default function ConfirmButtons({ query, merchantName }) {
  const [busy, setBusy] = useState(false)

  async function confirm() {
    setBusy(true)
    try {
      const d = await api.learn(query, merchantName)
      if (d.learned) {
        showToast(
          'Action Confirmed',
          `Learned: ${query} → ${merchantName} — saved for next run`,
        )
      } else {
        showToast('Already known', 'No new mapping needed.', 'flag')
      }
    } catch (e) {
      showToast('Action Failed', String(e.message || e), 'flag')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <button
        onClick={confirm}
        disabled={busy || !merchantName}
        className="w-full rounded-xl bg-primary py-3 font-plex text-[13px] font-bold text-on-primary shadow-md transition-all hover:opacity-90 active:scale-95 disabled:opacity-50"
      >
        {busy ? 'Saving…' : 'Confirm this merchant'}
      </button>
      <button
        onClick={() => showToast('Flagged for Review', `${merchantName || query} — added to review queue`, 'warn')}
        className="w-full rounded-lg py-2 font-plex text-[13px] text-on-surface-variant transition-colors hover:bg-surface-container"
      >
        Flag for Review
      </button>
    </div>
  )
}
