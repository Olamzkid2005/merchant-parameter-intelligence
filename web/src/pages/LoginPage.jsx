import { useState } from 'react'
import { api } from '../api'

export default function LoginPage({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setErr(null)
    try {
      await api.authLogin(username.trim(), password)
      onLogin()
    } catch (ex) {
      setErr(String(ex.message || ex))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm overflow-hidden rounded-2xl border border-outline-variant bg-surface-container-lowest shadow-xl">
        <div className="border-b border-outline-variant bg-surface-container-low px-6 py-5">
          <h1 className="flex items-center gap-2 font-plex text-lg font-bold text-on-surface">
            <span className="msi text-[22px] text-primary">lock</span>
            Merchant Intelligence
          </h1>
          <p className="mt-0.5 font-plex text-[12px] text-on-surface-variant">
            Access control is enabled — sign in to continue.
          </p>
        </div>
        <form onSubmit={submit} className="space-y-4 px-6 py-6">
          <div>
            <label className="mb-1 block font-plex text-[11px] font-bold uppercase tracking-wider text-on-surface-variant">
              Username
            </label>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
              autoComplete="username"
              className="w-full rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-2 font-plex text-[13px] text-on-surface shadow-sm outline-none focus:border-primary focus:ring-4 focus:ring-primary-container"
            />
          </div>
          <div>
            <label className="mb-1 block font-plex text-[11px] font-bold uppercase tracking-wider text-on-surface-variant">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              className="w-full rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-2 font-plex text-[13px] text-on-surface shadow-sm outline-none focus:border-primary focus:ring-4 focus:ring-primary-container"
            />
          </div>
          {err && (
            <p className="rounded-lg bg-error-container/40 px-3 py-2 font-plex text-[12px] font-bold text-error">
              {err}
            </p>
          )}
          <button
            type="submit"
            disabled={busy || !username || !password}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 font-plex text-[13px] font-bold text-on-primary shadow-sm transition-all hover:opacity-90 active:scale-95 disabled:opacity-40"
          >
            <span className="msi text-[16px]">{busy ? 'hourglass_top' : 'login'}</span>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
