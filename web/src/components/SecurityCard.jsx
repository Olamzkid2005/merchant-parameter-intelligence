import { useEffect, useState } from 'react'
import { api } from '../api'

const ROLES = ['viewer', 'analyst', 'administrator']

export default function SecurityCard() {
  const [cfg, setCfg] = useState(null)
  const [enabled, setEnabled] = useState(false)
  const [ttl, setTtl] = useState('12')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [uName, setUName] = useState('')
  const [uPass, setUPass] = useState('')
  const [uRole, setURole] = useState('viewer')

  async function load() {
    try {
      const d = await api.authConfig()
      setCfg(d)
      setEnabled(d.enabled)
      setTtl(String(d.session_ttl_hours ?? 12))
    } catch { /* non-critical */ }
  }

  useEffect(() => { load() }, [])

  function say(kind, text) {
    setMsg({ kind, text })
    setTimeout(() => setMsg(null), 4000)
  }

  async function toggle() {
    setBusy(true)
    try {
      const d = await api.authSaveConfig(!enabled, Number(ttl))
      setEnabled(d.enabled)
      setCfg((c) => ({ ...c, enabled: d.enabled, session_ttl_hours: d.session_ttl_hours }))
      say('success', `Access control ${d.enabled ? 'enabled' : 'disabled'}.`)
    } catch (e) {
      say('error', String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  async function addUser(e) {
    e.preventDefault()
    setBusy(true)
    try {
      const d = await api.authAddUser(uName.trim(), uPass, uRole)
      setCfg((c) => ({ ...c, users: d.users }))
      setUName(''); setUPass(''); setURole('viewer')
      say('success', `Added ${uName.trim()}.`)
    } catch (ex) {
      say('error', String(ex.message || ex))
    } finally {
      setBusy(false)
    }
  }

  async function removeUser(username) {
    if (!window.confirm(`Remove user ${username}?`)) return
    setBusy(true)
    try {
      const d = await api.authRemoveUser(username)
      setCfg((c) => ({ ...c, users: d.users, enabled: d.enabled }))
      setEnabled(d.enabled)
      say('success', `Removed ${username}.`)
    } catch (ex) {
      say('error', String(ex.message || ex))
    } finally {
      setBusy(false)
    }
  }

  async function resetPassword(username) {
    const pw = window.prompt(`New password for ${username} (min 8 chars):`)
    if (!pw) return
    setBusy(true)
    try {
      await api.authResetPassword(username, pw)
      say('success', `Password reset for ${username}.`)
    } catch (ex) {
      say('error', String(ex.message || ex))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
        <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
          <span className="msi text-[18px] text-primary">security</span>
          Access control
        </h3>
        <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
          roadmap #1 · opt-in · data/security_config.json
        </span>
      </div>
      <div className="p-5">
        <p className="mb-4 font-plex text-[12px] leading-relaxed text-on-surface-variant">
          Off by default — the app behaves exactly as before. When on, every request needs a
          session and roles gate the routes: <b>viewer</b> (masked reads) ·{' '}
          <b>analyst</b> (full reads, no exports) · <b>administrator</b> (exports, settings,
          audit, users).
        </p>

        {msg && (
          <p className={`mb-4 rounded-lg px-3 py-2 font-plex text-[12px] font-bold ${
            msg.kind === 'success' ? 'bg-green-100 text-green-800' : 'bg-error-container/40 text-error'
          }`}>
            {msg.text}
          </p>
        )}

        {!cfg ? (
          <p className="py-2 text-center font-plex text-[12px] text-on-surface-variant">Loading…</p>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <button
                onClick={toggle}
                disabled={busy}
                className={`flex items-center gap-2 rounded-lg px-4 py-2 font-plex text-[12px] font-bold shadow-sm transition-all active:scale-95 disabled:opacity-40 ${
                  enabled ? 'bg-green-600 text-white hover:opacity-90' : 'bg-primary text-on-primary hover:opacity-90'
                }`}
              >
                <span className="msi text-[15px]">{enabled ? 'lock_open' : 'lock'}</span>
                {enabled ? 'Access control is ON — click to disable' : 'Enable access control'}
              </button>
              <label className="flex items-center gap-2 font-plex text-[11px] font-bold text-on-surface-variant">
                Session TTL (h)
                <input
                  type="number"
                  min={1}
                  max={168}
                  value={ttl}
                  onChange={(e) => setTtl(e.target.value)}
                  className="w-20 rounded-lg border border-outline-variant bg-surface-container-lowest px-2 py-1.5 font-mono text-[12px] text-on-surface outline-none focus:border-primary"
                />
              </label>
            </div>

            <div className="overflow-hidden rounded-xl border border-outline-variant">
              <div className="bg-surface-container-high px-4 py-2 font-plex text-[11px] font-bold text-on-surface-variant">
                Users
              </div>
              <table className="w-full text-left font-mono text-[11px]">
                <thead className="bg-surface-container-low text-on-surface-variant">
                  <tr>
                    <th className="px-4 py-1.5 font-bold">username</th>
                    <th className="px-2 py-1.5 font-bold">role</th>
                    <th className="px-4 py-1.5 text-right font-bold">actions</th>
                  </tr>
                </thead>
                <tbody>
                  {(cfg.users || []).map((u) => (
                    <tr key={u.username} className="border-t border-outline-variant/60">
                      <td className="px-4 py-1.5 font-bold text-primary">{u.username}</td>
                      <td className="px-2 py-1.5">
                        <span className="rounded bg-primary/10 px-1.5 py-0.5 font-bold text-primary">{u.role}</span>
                      </td>
                      <td className="px-4 py-1.5 text-right">
                        <button onClick={() => resetPassword(u.username)} disabled={busy}
                          className="mr-2 rounded border border-outline-variant px-2 py-0.5 font-plex text-[10px] font-bold text-on-surface-variant hover:border-primary hover:text-primary disabled:opacity-40">
                          reset password
                        </button>
                        <button onClick={() => removeUser(u.username)} disabled={busy}
                          className="rounded border border-error/30 px-2 py-0.5 font-plex text-[10px] font-bold text-error hover:bg-error/10 disabled:opacity-40">
                          remove
                        </button>
                      </td>
                    </tr>
                  ))}
                  {!cfg.users?.length && (
                    <tr className="border-t border-outline-variant/60">
                      <td colSpan={3} className="px-4 py-3 text-center text-outline">
                        No users yet — add one before enabling access control.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
              <form onSubmit={addUser} className="flex flex-wrap items-center gap-2 border-t border-outline-variant bg-surface-container-low px-4 py-2.5">
                <input value={uName} onChange={(e) => setUName(e.target.value)} placeholder="username"
                  className="rounded-lg border border-outline-variant bg-surface-container-lowest px-2.5 py-1.5 font-mono text-[11px] text-on-surface outline-none focus:border-primary" />
                <input type="password" value={uPass} onChange={(e) => setUPass(e.target.value)} placeholder="password (min 8)"
                  className="rounded-lg border border-outline-variant bg-surface-container-lowest px-2.5 py-1.5 font-mono text-[11px] text-on-surface outline-none focus:border-primary" />
                <select value={uRole} onChange={(e) => setURole(e.target.value)}
                  className="rounded-lg border border-outline-variant bg-surface-container-lowest px-2 py-1.5 font-plex text-[11px] font-bold text-on-surface outline-none focus:border-primary">
                  {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
                <button type="submit" disabled={busy || !uName || uPass.length < 8}
                  className="rounded-lg bg-primary px-3 py-1.5 font-plex text-[11px] font-bold text-on-primary transition-all hover:opacity-90 active:scale-95 disabled:opacity-40">
                  Add user
                </button>
              </form>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
