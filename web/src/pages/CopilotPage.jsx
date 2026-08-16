import { useState } from 'react'
import { api } from '../api'
import CopyButton from '../components/CopyButton'

const SOURCE_LABEL = {
  whole: 'whole request',
  clause: 'clause',
  llm: 'LLM-proposed',
}

function StepTable({ step }) {
  const rows = step.kind === 'search'
    ? (step.result?.rows || [])
    : (step.result?.rows || [])
  const columns = step.kind === 'search'
    ? (rows[0] ? Object.keys(rows[0]).filter((k) => !['id'].includes(k)) : [])
    : (step.columns || [])
  if (!rows.length) {
    return (
      <p className="px-4 py-3 font-plex text-[11px] text-on-surface-variant">
        No rows returned
        {step.not_found ? ` · ${step.not_found} not found` : ''}
      </p>
    )
  }
  return (
    <div className="max-h-64 overflow-auto">
      <table className="w-full text-left font-mono text-[10.5px]">
        <thead className="sticky top-0 bg-surface-container-low text-on-surface-variant">
          <tr>
            {columns.map((c) => (
              <th key={c} className="whitespace-nowrap px-3 py-1.5 font-bold">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-outline-variant/50 hover:bg-surface-container-low/60">
              {columns.map((c) => (
                <td key={c} className="max-w-[260px] truncate px-3 py-1.5 text-on-surface-variant">
                  {r[c] != null && r[c] !== '' ? String(r[c]) : '—'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function CopilotPage() {
  const [text, setText] = useState('')
  const [useLlm, setUseLlm] = useState(true)
  const [res, setRes] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)

  async function run() {
    if (!text.trim()) return
    setLoading(true)
    setErr(null)
    setRes(null)
    try {
      setRes(await api.copilot(text.trim(), useLlm))
    } catch (e) {
      setErr(String(e.message || e))
    } finally {
      setLoading(false)
    }
  }

  const plan = res?.plan || []
  const steps = res?.steps || []

  return (
    <div className="space-y-5">
      <div>
        <h2 className="font-plex text-lg font-bold text-on-surface">Merchant Copilot</h2>
        <p className="max-w-3xl font-plex text-[12px] text-on-surface-variant">
          Paste a compound investigation request — the copilot decomposes it into an
          ordered, re-runnable plan and executes every step through the deterministic
          engine. Try:{' '}
          <span className="text-primary">"find MEDPLUS then get the tids for the above merchant"</span>{' '}
          or a multi-line merchant list followed by an instruction.
        </p>
      </div>

      <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
        <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
          <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
            <span className="msi text-[18px] text-primary">auto_awesome</span>
            Investigation request
          </h3>
          <label className="flex cursor-pointer items-center gap-2 font-plex text-[11px] font-bold text-on-surface-variant">
            <input
              type="checkbox"
              checked={useLlm}
              onChange={(e) => setUseLlm(e.target.checked)}
              className="h-3.5 w-3.5 accent-primary"
            />
            Use LLM to propose the plan (rule engine validates &amp; executes)
          </label>
        </div>
        <div className="p-5">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={6}
            placeholder={'Paste a compound request, e.g.\n\nfind MEDPLUS\n\nthen get the tids for the above merchant\n\nthen the static account and beneficiary for those'}
            className="w-full resize-y rounded-lg border border-outline-variant bg-surface-container-lowest px-3.5 py-3 font-plex text-[13px] text-on-surface shadow-inner outline-none placeholder:text-outline focus:border-primary"
          />
          <div className="mt-3 flex items-center gap-3">
            <button
              onClick={run}
              disabled={loading || !text.trim()}
              className="flex items-center gap-2 rounded-xl bg-primary px-5 py-2.5 font-plex text-[13px] font-bold text-on-primary shadow-sm transition-all hover:opacity-90 active:scale-95 disabled:opacity-40"
            >
              <span className="msi text-[18px]">{loading ? 'hourglass_top' : 'play_arrow'}</span>
              {loading ? 'Running plan…' : 'Run investigation'}
            </button>
            {err && (
              <p className="rounded-lg bg-error-container/40 px-4 py-2 font-plex text-[12px] font-bold text-error">
                {err}
              </p>
            )}
          </div>
        </div>
      </div>

      {res && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <span className={`rounded-full px-3 py-1 font-plex text-[11px] font-bold ${
              res.mode === 'llm' ? 'bg-violet-100 text-violet-800' : 'bg-surface-container-high text-on-surface-variant'
            }`}>
              {res.mode === 'llm' ? 'LLM-proposed plan' : 'Rule-engine plan'}
              {res.model ? ` · ${res.model}` : ''}
            </span>
            <span className="rounded-full bg-surface-container-high px-3 py-1 font-plex text-[11px] font-bold text-on-surface-variant">
              {steps.length} step{steps.length === 1 ? '' : 's'}
            </span>
            <span className="rounded-full bg-surface-container-high px-3 py-1 font-plex text-[11px] font-bold text-on-surface-variant">
              {res.elapsed_ms} ms
            </span>
            {res.llm_error && (
              <span className="rounded-full bg-amber-100 px-3 py-1 font-plex text-[11px] font-bold text-amber-800">
                {res.llm_error}
              </span>
            )}
            <p className="font-plex text-[12px] font-bold text-on-surface">{res.summary}</p>
          </div>

          {steps.map((s) => (
            <div key={s.index} className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
              <div className="flex flex-wrap items-center gap-2 border-b border-outline-variant bg-surface-container-low px-5 py-3">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary font-plex text-[11px] font-bold text-on-primary">
                  {s.index}
                </span>
                <span className="font-plex text-[12px] font-medium text-on-surface">{s.text}</span>
                <span className={`rounded-full px-2 py-0.5 font-plex text-[10px] font-bold ${
                  s.kind === 'task' ? 'bg-primary/10 text-primary' : 'bg-surface-container-high text-on-surface-variant'
                }`}>
                  {s.kind === 'task' ? s.intent || 'task' : 'search'}
                </span>
                <span className="rounded-full bg-surface-container-high px-2 py-0.5 font-plex text-[10px] font-bold text-on-surface-variant">
                  {SOURCE_LABEL[plan.find((p) => p.index === s.index)?.source] || 'step'}
                </span>
                {s.context_inherited && (
                  <span className="rounded-full bg-amber-100 px-2 py-0.5 font-plex text-[10px] font-bold text-amber-800">
                    resolved from previous step
                  </span>
                )}
                <span className="ml-auto flex items-center gap-1.5">
                  {s.workflow_executed?.length > 0 && (
                    <span className="hidden font-mono text-[10px] text-outline sm:inline">
                      {s.workflow_executed.join(' → ')}
                    </span>
                  )}
                  <CopyButton value={s.text} label="step" />
                </span>
              </div>
              <StepTable step={s} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
