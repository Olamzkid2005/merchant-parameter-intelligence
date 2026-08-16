import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { intentLabel } from '../utils/intents'

// Verbose display labels for the rule-engine list. Anything not listed here
// falls back to the shared INTENT_META label (utils/intents.js), so the two
// surfaces can never drift when a new intent is added.
const LABEL_OVERRIDES = {
  static_account: 'Static accounts & beneficiaries',
  count: 'Count / how many',
  duplicates: 'Duplicate detection',
  summary: 'Summary / stats',
  segment: 'Segment / collection',
  resolve: 'Resolve identifiers',
}

function labelFor(name) {
  return LABEL_OVERRIDES[name] || intentLabel(name)
}

// Explain WHY a pattern fired in the test-panel intent breakdown. The
// backend labels offline fuzzy hits with a marker so a typo'd or paraphrased
// keyword is distinguishable from a regex phrase:
//   '~fuzzy:0.93'   a request token matched a keyword within ONE char edit
//   '~semantic:0.88' a paraphrase overlapped a keyword phrase (no exact hit)
//   anything else   a configured regex pattern matched the lowercased text
function matchedChip(m) {
  m = String(m ?? '')
  if (m.startsWith('~fuzzy:')) {
    return {
      label: 'typo matched',
      cls: 'border-amber-300 bg-amber-50 text-amber-800',
      icon: 'spellcheck',
      title:
        `“${m}” — a request token was within one character edit (typo) of a ` +
        'keyword for this intent, so it classified despite the misspelling.',
    }
  }
  if (m.startsWith('~semantic:')) {
    return {
      label: 'paraphrase matched',
      cls: 'border-tertiary/30 bg-tertiary/10 text-tertiary',
      icon: 'auto_awesome',
      title:
        `“${m}” — no exact regex phrase fired, but the request strongly ` +
        'overlapped this intent’s keyword set (order/plural-tolerant), so it ' +
        'classified as a paraphrase.',
    }
  }
  return {
    label: m,
    cls: 'border-outline-variant bg-surface-container-high text-on-surface-variant',
    icon: null,
    title: `Regex pattern matched the lowercased request: ${m}`,
  }
}

// One-click sample phrases for the key-merchant intent emphasis — each
// exercises a different path (profile shorthand, field extraction, typo
// tolerance, segment). Clicking one fills + runs the test box.
const KEY_MERCHANT_SAMPLES = [
  { label: 'medplus emails', text: 'medplus emails' },
  { label: 'medpluz emails (typo)', text: 'medpluz emails' },
  { label: 'addide addresses', text: 'addide addresses' },
  { label: 'adide addresses (typo)', text: 'adide addresses' },
  { label: 'spar full profile', text: 'spar full profile' },
  { label: 'spar everything', text: 'spar everything' },
  { label: 'medplus profile', text: 'medplus profile' },
  { label: 'get me everything on spar', text: 'get me everything on spar' },
  { label: 'all addide stores in lagos', text: 'all addide stores in lagos' },
  { label: 'what is the bank for just chips', text: 'what is the bank for just chips' },
  { label: 'lagoon waters address', text: 'lagoon waters address' },
  { label: 'lagoon waters emails', text: 'lagoon waters emails' },
  { label: 'cascades luxe profile', text: 'cascades luxe profile' },
  { label: 'all lagoon waters stations', text: 'all lagoon waters stations' },
  { label: 'bokku mart address', text: 'bokku mart address' },
  { label: 'orient africa emails', text: 'orient africa emails' },
  { label: 'shoprite phone number', text: 'shoprite phone number' },
  { label: 'kongopay emails (typo)', text: 'kongopay emails' },
  { label: 'all bokku mart stores', text: 'all bokku mart stores' },
]

function deepClone(obj) {
  return JSON.parse(JSON.stringify(obj))
}

export default function RuleEnginePage() {
  const [cfg, setCfg] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [selected, setSelected] = useState(null)
  const [filter, setFilter] = useState('')
  const [draft, setDraft] = useState(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState(null)
  const [testText, setTestText] = useState('')
  const [testRes, setTestRes] = useState(null)
  const [testLoading, setTestLoading] = useState(false)
  const [cal, setCal] = useState(null)
  const [calLoading, setCalLoading] = useState(false)
  const [calMsg, setCalMsg] = useState(null)
  // Saved interpretations ("remember my choice") — phrase -> intent pairs
  // the user taught the engine from clarification cards.
  const [prefs, setPrefs] = useState(null)
  // Self-improvement feedback loop — pattern suggestions mined from real
  // corrections (clarification overrides + rephrased requests).
  const [sugs, setSugs] = useState(null)
  const [sugLoading, setSugLoading] = useState(false)
  const [sugMsg, setSugMsg] = useState(null)
  // Runtime engine settings (data/engine_settings.json) — the family-guard
  // threshold and any future tunable knobs.
  const [settings, setSettings] = useState(null)
  const [settingsDraft, setSettingsDraft] = useState('')
  const [modeDraft, setModeDraft] = useState('off')
  const [settingsSaving, setSettingsSaving] = useState(false)
  const [settingsMsg, setSettingsMsg] = useState(null)
  // Tier 1 enrichment — WordNet synonym proposals (propose -> curate -> apply)
  const [syn, setSyn] = useState(null)
  const [synLoading, setSynLoading] = useState(false)
  const [synMsg, setSynMsg] = useState(null)
  const [synSel, setSynSel] = useState(new Set())
  // Tier 2 spot-check — shadow decisions review (Phase-1 auto-run band)
  const [sr, setSr] = useState(null)
  const [srBand, setSrBand] = useState('would_act')
  const [srLoading, setSrLoading] = useState(false)
  const [srMsg, setSrMsg] = useState(null)
  // Shadow-log health (band-independent) — the "today's entry count" chip
  // in the Engine tuning card, so accumulation is visible without opening
  // the spot-check panel. Polled every 60s while this page is open.
  const [health, setHealth] = useState(null)

  useEffect(() => {
    api
      .intents()
      .then((d) => {
        setCfg(d)
        const first = Object.keys(d.intents || {})[0] || null
        setSelected(first)
        if (first) setDraft(deepClone(d.intents[first]))
      })
      .catch((e) => setLoadError(String(e.message || e)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    api
      .calibration()
      .then(setCal)
      .catch(() => setCalMsg({ kind: 'error', text: 'Failed to load calibration data' }))
    api
      .preferences()
      .then(setPrefs)
      .catch(() => { /* non-critical */ })
    api
      .feedbackSuggestions()
      .then(setSugs)
      .catch(() => { /* non-critical */ })
    api
      .settings()
      .then((d) => {
        setSettings(d)
        const th = d.settings?.decisive_match_threshold
        setSettingsDraft(th?.value != null ? String(th.value) : '')
        setModeDraft(d.settings?.semantic_tier_mode?.value ?? 'off')
      })
      .catch(() => { /* non-critical */ })
    api
      .synonyms()
      .then(setSyn)
      .catch(() => { /* non-critical */ })
  }, [])

  // Shadow-log health: load once on mount, then poll every 60s so the chip
  // tracks accumulation live while the page sits open.
  useEffect(() => {
    let alive = true
    const tick = () => {
      api
        .shadowReview('all', 1)
        .then((d) => { if (alive) setHealth(d.health || null) })
        .catch(() => { /* non-critical */ })
    }
    tick()
    const iv = setInterval(tick, 60000)
    return () => { alive = false; clearInterval(iv) }
  }, [])

  async function saveSettingsKnobs() {
    const val = Number(settingsDraft)
    if (!Number.isFinite(val) || val < 0 || val > 100) {
      setSettingsMsg({ kind: 'error', text: 'Threshold must be a number between 0 and 100.' })
      return
    }
    setSettingsSaving(true)
    setSettingsMsg(null)
    try {
      const d = await api.saveSettings({ decisive_match_threshold: val })
      setSettings(d)
      setSettingsDraft(String(d.settings?.decisive_match_threshold?.value))
      setSettingsMsg({ kind: 'success', text: 'Saved & hot-reloaded — the new threshold is live now.' })
    } catch (e) {
      setSettingsMsg({ kind: 'error', text: String(e.message || e) })
    } finally {
      setSettingsSaving(false)
    }
  }

  async function resetSettingsKnobs() {
    if (!window.confirm('Reset all engine settings to built-in defaults?')) return
    setSettingsSaving(true)
    setSettingsMsg(null)
    try {
      const d = await api.resetSettings()
      setSettings(d)
      setSettingsDraft(String(d.settings?.decisive_match_threshold?.value))
      setModeDraft(d.settings?.semantic_tier_mode?.value ?? 'off')
      setSettingsMsg({ kind: 'info', text: 'Reset — every knob is back on its built-in default.' })
    } catch (e) {
      setSettingsMsg({ kind: 'error', text: String(e.message || e) })
    } finally {
      setSettingsSaving(false)
    }
  }

  async function saveModeKnob() {
    const mode = String(modeDraft || '').trim()
    if (!['off', 'shadow', 'enabled'].includes(mode)) {
      setSettingsMsg({ kind: 'error', text: 'Mode must be one of: off, shadow, enabled.' })
      return
    }
    setSettingsSaving(true)
    setSettingsMsg(null)
    try {
      const d = await api.saveSettings({ semantic_tier_mode: mode })
      setSettings(d)
      setModeDraft(d.settings?.semantic_tier_mode?.value ?? 'off')
      setSettingsMsg({
        kind: 'success',
        text: `Saved — Tier 2 is now in ${mode} mode.`,
      })
    } catch (e) {
      setSettingsMsg({ kind: 'error', text: String(e.message || e) })
    } finally {
      setSettingsSaving(false)
    }
  }

  async function forgetPref(key) {
    if (!window.confirm(`Forget the saved choice for “${key}”?`)) return
    try {
      const d = await api.forgetPreference(key)
      setPrefs(d)
      setCalMsg({ kind: 'success', text: `Forgot “${key}” — the next similar request will ask again.` })
    } catch (e) {
      setCalMsg({ kind: 'error', text: String(e.message || e) })
    }
  }

  // ── Tier 1 enrichment: WordNet synonym proposals ────────────────────
  async function refreshSyn() {
    setSynLoading(true)
    setSynMsg(null)
    try {
      setSyn(await api.synonyms())
      setSynMsg({ kind: 'success', text: 'Proposals refreshed.' })
    } catch (e) {
      setSynMsg({ kind: 'error', text: String(e.message || e) })
    } finally {
      setSynLoading(false)
    }
  }

  async function generateSyn() {
    if (!window.confirm('Re-run WordNet expansion? Existing approvals and rejections are kept.')) return
    setSynLoading(true)
    setSynMsg(null)
    try {
      const d = await api.synonymsPropose()
      setSyn(await api.synonyms())
      setSynMsg({ kind: 'success', text: `Added ${d.added} new proposal(s) — ${d.total} total.` })
    } catch (e) {
      setSynMsg({ kind: 'error', text: String(e.message || e) })
    } finally {
      setSynLoading(false)
    }
  }

  function toggleSynSel(id) {
    setSynSel((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function markSyn(status) {
    if (!synSel.size) return
    const n = synSel.size
    setSynLoading(true)
    setSynMsg(null)
    try {
      await api.synonymsStatus([...synSel], status)
      setSynSel(new Set())
      setSyn(await api.synonyms())
      setSynMsg({
        kind: 'success',
        text: `${status === 'approved' ? 'Approved' : 'Rejected'} ${n} candidate(s).`,
      })
    } catch (e) {
      setSynMsg({ kind: 'error', text: String(e.message || e) })
    } finally {
      setSynLoading(false)
    }
  }

  async function applySyn() {
    if (!window.confirm('Merge approved synonym patterns into intents.json? This regenerates vocab.py defaults in lockstep and hot-reloads the engine.')) return
    setSynLoading(true)
    setSynMsg(null)
    try {
      const d = await api.synonymsApply(null)
      setSyn(await api.synonyms())
      setSynMsg({
        kind: d.applied?.length ? 'success' : 'info',
        text: d.applied?.length
          ? `Applied ${d.applied.length} pattern(s) — engine hot-reloaded.`
          : `Nothing applied${d.skipped?.length ? ` (${d.skipped.length} skipped: ${d.skipped[0].reason})` : ''}.`,
      })
    } catch (e) {
      setSynMsg({ kind: 'error', text: String(e.message || e) })
    } finally {
      setSynLoading(false)
    }
  }

  // ── Tier 2 spot-check: shadow-decision review (Phase-1 auto-run band) ──
  async function refreshSr() {
    setSrLoading(true)
    setSrMsg(null)
    try {
      const d = await api.shadowReview(srBand, 200)
      setSr(d)
      if (d.health) setHealth(d.health)
      setSrMsg({ kind: 'success', text: 'Shadow review refreshed.' })
    } catch (e) {
      setSrMsg({ kind: 'error', text: String(e.message || e) })
    } finally {
      setSrLoading(false)
    }
  }

  function switchSrBand(band) {
    setSrBand(band)
    setSrMsg(null)
  }

  async function labelSr(entryId, correct, intent = '') {
    setSrLoading(true)
    setSrMsg(null)
    try {
      await api.shadowReviewLabel(entryId, correct, intent)
      const d = await api.shadowReview(srBand, 200)
      setSr(d)
      if (d.health) setHealth(d.health)
      setSrMsg({ kind: 'success', text: `Marked ${correct ? 'correct' : 'wrong'}.` })
    } catch (e) {
      setSrMsg({ kind: 'error', text: String(e.message || e) })
    } finally {
      setSrLoading(false)
    }
  }

  async function refreshSugs() {
    setSugLoading(true)
    try {
      setSugs(await api.feedbackSuggestions())
      setSugMsg({ kind: 'success', text: 'Suggestions refreshed.' })
    } catch (e) {
      setSugMsg({ kind: 'error', text: String(e.message || e) })
    } finally {
      setSugLoading(false)
    }
  }

  async function applySug(s) {
    try {
      await api.applySuggestion(s.ngram, s.intent, s.weight)
      setSugMsg({ kind: 'success', text: `Pattern “${s.ngram}” → ${s.intent.replace(/_/g, ' ')} added & hot-reloaded.` })
      await refreshSugs()
      // Reload the editor config so the new pattern appears in the list.
      const cfg2 = await api.intents()
      setCfg(cfg2)
      if (selected === s.intent && cfg2.intents?.[s.intent]) {
        setDraft(deepClone(cfg2.intents[s.intent]))
      }
    } catch (e) {
      setSugMsg({ kind: 'error', text: String(e.message || e) })
    }
  }

  async function rejectSug(s) {
    try {
      await api.rejectSuggestion(s.ngram, s.intent)
      setSugMsg({ kind: 'info', text: `Rejected “${s.ngram}” — it won't be suggested again.` })
      await refreshSugs()
    } catch (e) {
      setSugMsg({ kind: 'error', text: String(e.message || e) })
    }
  }

  async function refreshCal() {
    setCalLoading(true)
    setCalMsg(null)
    try {
      setCal(await api.calibration())
      setCalMsg({ kind: 'success', text: 'Calibration refreshed.' })
    } catch (e) {
      setCalMsg({ kind: 'error', text: String(e.message || e) })
    } finally {
      setCalLoading(false)
    }
  }

  async function resetCal() {
    if (!window.confirm('Clear the decision log and start learning fresh?')) return
    setCalLoading(true)
    setCalMsg(null)
    try {
      setCal(await api.resetCalibration())
      setCalMsg({ kind: 'info', text: 'Decision log cleared — thresholds back to defaults until enough new requests log.' })
    } catch (e) {
      setCalMsg({ kind: 'error', text: String(e.message || e) })
    } finally {
      setCalLoading(false)
    }
  }

  const pipelines = cfg?.pipelines || []
  const nameCapable = cfg?.name_capable || []
  const chainable = cfg?.chainable || {}

  const intentList = useMemo(() => {
    if (!cfg) return []
    const q = filter.trim().toLowerCase()
    return Object.keys(cfg.intents || {}).filter(
      (n) => !q || n.includes(q) || labelFor(n).toLowerCase().includes(q),
    )
  }, [cfg, filter])

  // WordNet proposals grouped by intent for the curation panel.
  const synGroups = useMemo(() => {
    if (!syn?.candidates) return []
    const map = {}
    for (const c of syn.candidates) {
      ;(map[c.intent] ||= []).push(c)
    }
    return Object.entries(map)
  }, [syn])

  function selectIntent(key) {
    if (!cfg || !cfg.intents[key]) return
    setSelected(key)
    setDraft(deepClone(cfg.intents[key]))
    setDirty(false)
    setMsg(null)
    setTestRes(null)
  }

  function patchPattern(i, patch) {
    setDraft((d) => ({
      ...d,
      patterns: (d?.patterns || []).map((p, j) => (j === i ? { ...p, ...patch } : p)),
    }))
    setDirty(true)
  }

  function addPattern() {
    setDraft((d) => ({
      ...d,
      patterns: [...(d?.patterns || []), { pattern: '', weight: 5 }],
    }))
    setDirty(true)
  }

  function removePattern(i) {
    setDraft((d) => ({
      ...d,
      patterns: (d?.patterns || []).filter((_, j) => j !== i),
    }))
    setDirty(true)
  }

  function setKeywords(text) {
    const kws = text
      .split(',')
      .map((k) => k.trim())
      .filter(Boolean)
    setDraft((d) => ({ ...d, keywords: kws }))
    setDirty(true)
  }

  async function save() {
    if (!selected || !draft || !draft.patterns?.length) {
      setMsg({ kind: 'error', text: 'Add at least one pattern before saving.' })
      return
    }
    setSaving(true)
    setMsg(null)
    try {
      const res = await api.saveIntent(selected, draft)
      setCfg((c) => ({ ...c, intents: { ...c.intents, [selected]: res.intents } }))
      setDirty(false)
      setMsg({ kind: 'success', text: `Saved & hot-reloaded — "${selected}" is live now.` })
    } catch (e) {
      setMsg({ kind: 'error', text: String(e.message || e) })
    } finally {
      setSaving(false)
    }
  }

  function restoreDefaults() {
    if (!cfg || !selected || !cfg.defaults?.[selected]) return
    setDraft(deepClone(cfg.defaults[selected]))
    setDirty(true)
    setMsg({ kind: 'info', text: 'Built-in defaults loaded — press Save & apply to activate.' })
  }

  function discard() {
    if (!cfg || !selected || !cfg.intents[selected]) return
    setDraft(deepClone(cfg.intents[selected]))
    setDirty(false)
    setMsg(null)
  }

  async function runTest() {
    const t = testText.trim()
    if (!t) return
    setTestLoading(true)
    setTestRes(null)
    try {
      setTestRes(await api.taskAnalyze(t))
    } catch (e) {
      setMsg({ kind: 'error', text: String(e.message || e) })
    } finally {
      setTestLoading(false)
    }
  }

  async function runTestWith(t) {
    setTestLoading(true)
    try {
      setTestRes(await api.taskAnalyze(t))
    } catch (e) {
      setMsg({ kind: 'error', text: String(e.message || e) })
    } finally {
      setTestLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-10 w-64 animate-pulse rounded-xl bg-white shadow-sm" />
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[300px_1fr]">
          <div className="h-[560px] animate-pulse rounded-xl border border-outline-variant bg-white shadow-sm" />
          <div className="h-[560px] animate-pulse rounded-xl border border-outline-variant bg-white shadow-sm" />
        </div>
      </div>
    )
  }

  if (loadError && !cfg) {
    return (
      <div className="rounded-xl border border-error/20 bg-error-container/30 p-8 text-center">
        <p className="font-plex text-sm font-semibold text-error">{loadError}</p>
      </div>
    )
  }

  return (
    <div className="animate-fade-in-up space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[28px] font-extrabold tracking-tight text-on-surface">Rule Engine</h1>
          <p className="mt-1 text-sm text-on-surface-variant">
            Tune the weighted patterns that route natural-language requests — save applies instantly, no restart.
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-xl border border-outline-variant bg-surface-container-lowest px-3 py-2 shadow-sm">
          <span className="msi text-[18px] text-primary">tune</span>
          <span className="font-plex text-[11px] font-semibold text-on-surface-variant">
            {cfg?.source?.split(/[\\/]/).pop() || 'intents.json'}
          </span>
        </div>
      </div>

      {/* Save bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-outline-variant bg-surface-container-lowest px-5 py-3 shadow-sm">
        <div>
          {dirty ? (
            <span className="flex items-center gap-2 font-plex text-[12px] font-bold text-amber-700">
              <span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" />
              Unsaved changes in &quot;{selected}&quot;
            </span>
          ) : (
            <span className="font-plex text-[12px] text-on-surface-variant">
              No unsaved changes · <b className="text-on-surface">{cfg?.intents ? Object.keys(cfg.intents).length : 0}</b> intents
            </span>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={discard}
            disabled={!dirty || saving}
            title="Revert to the last saved version"
            className="flex items-center gap-2 rounded-xl border border-outline-variant bg-surface-container-lowest px-4 py-2.5 font-plex text-[13px] font-bold text-on-surface-variant shadow-sm transition-all hover:bg-surface-container active:scale-95 disabled:opacity-40"
          >
            <span className="msi text-[18px]">undo</span>
            Discard
          </button>
          <button
            onClick={restoreDefaults}
            disabled={!selected || !cfg?.defaults?.[selected] || saving}
            title="Load the built-in fallback patterns for this intent"
            className="flex items-center gap-2 rounded-xl border border-outline-variant bg-surface-container-lowest px-4 py-2.5 font-plex text-[13px] font-bold text-on-surface-variant shadow-sm transition-all hover:bg-surface-container active:scale-95 disabled:opacity-40"
          >
            <span className="msi text-[18px]">restore</span>
            Restore defaults
          </button>
          <button
            onClick={save}
            disabled={!dirty || saving || !draft?.patterns?.length}
            className="flex items-center gap-2 rounded-xl bg-primary px-5 py-2.5 font-plex text-[13px] font-bold text-on-primary shadow-sm transition-all hover:opacity-90 active:scale-95 disabled:opacity-40"
          >
            <span className="msi text-[18px]">{saving ? 'hourglass_top' : 'save'}</span>
            {saving ? 'Saving…' : 'Save & apply'}
          </button>
        </div>
      </div>

      {msg && (
        <div
          className={`rounded-xl border px-5 py-3 font-plex text-[13px] font-semibold ${
            msg.kind === 'error'
              ? 'border-error/20 bg-error-container/30 text-error'
              : msg.kind === 'success'
                ? 'border-secondary/20 bg-secondary-container/30 text-on-secondary-container'
                : 'border-outline-variant bg-surface-container-low text-on-surface-variant'
          }`}
        >
          {msg.text}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[320px_1fr]">
        {/* ── Intent list ── */}
        <div className="self-start overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
          <div className="border-b border-outline-variant bg-surface-container-low px-4 py-3">
            <div className="relative">
              <span className="msi pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[18px] text-outline">search</span>
              <input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter intents…"
                className="w-full rounded-lg border border-outline-variant bg-surface-container-lowest py-2 pl-9 pr-3 font-plex text-[13px] text-on-surface shadow-sm outline-none transition-all focus:border-primary focus:ring-4 focus:ring-primary-container"
              />
            </div>
          </div>
          <div className="max-h-[620px] overflow-y-auto p-2">
            {intentList.map((name) => {
              const active = name === selected
              const hasPipe = pipelines.includes(name)
              return (
                <button
                  key={name}
                  onClick={() => selectIntent(name)}
                  className={`mb-1 w-full rounded-lg px-3 py-2.5 text-left transition-colors ${
                    active ? 'bg-primary-container shadow-sm' : 'hover:bg-surface-container'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className={`font-mono text-[13px] font-bold ${active ? 'text-on-primary-container' : 'text-on-surface'}`}>
                      {name}
                    </span>
                    {active && <span className="msi text-[16px] text-primary">chevron_right</span>}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <span className="truncate text-[10px] text-on-surface-variant">{labelFor(name)}</span>
                    <span
                      className={`rounded-full px-2 py-0.5 font-plex text-[9px] font-bold ${
                        hasPipe ? 'bg-green-100 text-green-800' : 'bg-amber-100 text-amber-800'
                      }`}
                      title={hasPipe ? 'Has a dedicated pipeline' : 'No pipeline — falls back to generic resolution'}
                    >
                      {hasPipe ? 'pipeline' : 'no pipeline'}
                    </span>
                    <span className="rounded-full bg-surface-container-high px-2 py-0.5 font-plex text-[9px] font-bold text-on-surface-variant">
                      {(cfg?.intents?.[name]?.patterns || []).length} pat
                    </span>
                  </div>
                </button>
              )
            })}
            {intentList.length === 0 && (
              <p className="px-3 py-6 text-center text-xs text-on-surface-variant">No intents match &quot;{filter}&quot;</p>
            )}
          </div>
        </div>

        {/* ── Editor ── */}
        <div className="min-w-0 space-y-6">
          {selected && !pipelines.includes(selected) && (
            <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-5 py-3.5">
              <span className="msi mt-0.5 text-[18px] text-amber-700">warning</span>
              <p className="font-plex text-[12px] text-amber-900">
                <b>{selected}</b> has no pipeline registered — requests will fall back to generic
                resolution. Give it a dedicated handler by writing{' '}
                <code className="font-mono">_pipeline_{selected}()</code> in{' '}
                <code className="font-mono">pipelines.py</code> and registering it in{' '}
                <code className="font-mono">_PIPELINES</code> (see the config's _help guide).
              </p>
            </div>
          )}

          {/* Patterns */}
          <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
              <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
                <span className="msi text-[18px] text-primary">rule</span>
                Patterns
                <span className="rounded-md bg-surface-container-high px-2 py-0.5 font-mono text-[11px] font-bold text-primary">
                  {selected}
                </span>
              </h3>
              <button
                onClick={addPattern}
                className="flex items-center gap-1.5 rounded-lg bg-primary/10 px-3 py-1.5 font-plex text-[12px] font-bold text-primary transition-all hover:bg-primary/20 active:scale-95"
              >
                <span className="msi text-[16px]">add</span>
                Add pattern
              </button>
            </div>
            <div className="space-y-2.5 p-5">
              {(draft?.patterns || []).map((p, i) => (
                <div key={i} className="flex items-center gap-2">
                  <span className="w-5 shrink-0 text-center font-plex text-[11px] font-bold text-outline">{i + 1}</span>
                  <input
                    value={p.pattern}
                    onChange={(e) => patchPattern(i, { pattern: e.target.value })}
                    placeholder="\\bstatic account\\b"
                    spellCheck={false}
                    className="min-w-0 flex-1 rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-2 font-mono text-[12px] text-on-surface shadow-sm outline-none transition-all focus:border-primary focus:ring-4 focus:ring-primary-container"
                  />
                  <input
                    type="number"
                    min={1}
                    max={10}
                    value={p.weight}
                    onChange={(e) => patchPattern(i, { weight: Math.max(1, Math.min(10, Number(e.target.value) || 1)) })}
                    title="Weight 1-10 — higher = stronger signal"
                    className="w-16 rounded-lg border border-outline-variant bg-surface-container-lowest px-2 py-2 text-center font-plex text-[12px] font-bold text-on-surface shadow-sm outline-none transition-all focus:border-primary focus:ring-4 focus:ring-primary-container"
                  />
                  <button
                    onClick={() => removePattern(i)}
                    className="shrink-0 rounded-lg p-2 text-error transition-all hover:bg-error-container/40 active:scale-95"
                    title="Delete pattern"
                  >
                    <span className="msi text-[18px]">delete</span>
                  </button>
                </div>
              ))}
              {!draft?.patterns?.length && (
                <p className="rounded-lg border border-dashed border-outline-variant px-4 py-5 text-center text-xs text-on-surface-variant">
                  No patterns — add one above.
                </p>
              )}
              <p className="pt-1 text-[11px] leading-relaxed text-on-surface-variant">
                Patterns are matched against the <b>lowercased</b> request — write them in lowercase with{' '}
                <code className="font-mono">\b</code> word boundaries. Confidence = min(100, matched weight × 12):
                a weight-8 phrase reaches ~96, a weight-3 generic word stays ~36 and never creates a task on its own.
              </p>
            </div>
          </div>

          {/* Keywords */}
          <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
            <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
              <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
                <span className="msi text-[18px] text-primary">tag</span>
                Keywords
              </h3>
              <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                LLM prompt · result validation
              </span>
            </div>
            <div className="p-5">
              <input
                value={(draft?.keywords || []).join(', ')}
                onChange={(e) => setKeywords(e.target.value)}
                placeholder="static account, static acct, beneficiary, alias"
                className="w-full rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-2.5 font-plex text-[13px] text-on-surface shadow-sm outline-none transition-all focus:border-primary focus:ring-4 focus:ring-primary-container"
              />
              <p className="mt-2 text-[11px] text-on-surface-variant">
                Comma-separated plain phrases — used by the LLM refinement prompt and result validation
                (keep them in sync with the patterns above).
              </p>
            </div>
          </div>

          {/* Typo tolerance (fuzzy tier) */}
          <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
            <div className="flex items-center justify-between gap-2 border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
              <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
                <span className="msi text-[18px] text-primary">spellcheck</span>
                Typo tolerance
              </h3>
              <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                per-intent · fuzzy tier
              </span>
            </div>
            <div className="p-5">
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  role="switch"
                  aria-checked={draft?.fuzzy !== false}
                  onClick={() => setDraft((d) => ({ ...d, fuzzy: d?.fuzzy === false }))}
                  className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${
                    draft?.fuzzy === false ? 'bg-outline-variant' : 'bg-primary'
                  }`}
                >
                  <span
                    className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${
                      draft?.fuzzy === false ? 'left-0.5' : 'left-[22px]'
                    }`}
                  />
                </button>
                <div className="min-w-0">
                  <p className="text-sm font-bold text-on-surface">
                    {draft?.fuzzy === false ? 'Off — exact patterns only' : 'On — typo & paraphrase tolerant'}
                  </p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-on-surface-variant">
                    When on, requests that miss every pattern but strongly overlap a keyword
                    (within one character edit — <code className="font-mono">sttic</code> → static, or a
                    close paraphrase) still classify as this intent. Turn off to restrict this
                    intent to exact regex matches only.
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Capability badges */}
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`rounded-full px-3 py-1 font-plex text-[11px] font-bold ${
                nameCapable.includes(selected) ? 'bg-green-100 text-green-800' : 'bg-surface-container-high text-on-surface-variant'
              }`}
              title="Works for name-only requests like 'get me the email for MEDPLUS'"
            >
              name-only requests: {nameCapable.includes(selected) ? 'supported' : 'no'}
            </span>
            <span
              className={`rounded-full px-3 py-1 font-plex text-[11px] font-bold ${
                chainable[selected] ? 'bg-green-100 text-green-800' : 'bg-surface-container-high text-on-surface-variant'
              }`}
              title="Appears in the suggest-next-steps chips and can merge into compound requests"
            >
              chainable: {chainable[selected] ? 'yes' : 'no'}
            </span>
            <span className="rounded-full bg-surface-container-high px-3 py-1 font-plex text-[11px] font-bold text-on-surface-variant">
              weight range 1–10
            </span>
          </div>

          {/* Engine tuning — runtime settings */}
          <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
              <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
                <span className="msi text-[18px] text-primary">tune</span>
                Engine tuning
              </h3>
              <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                data/engine_settings.json · hot-reloaded
              </span>
            </div>
            <div className="p-5">
              {!settings ? (
                <p className="py-2 text-center font-plex text-[12px] text-on-surface-variant">
                  Loading settings…
                </p>
              ) : (
                <div className="space-y-4">
                  {settings.settings?.semantic_tier_mode && (
                    <div className="rounded-xl border border-outline-variant bg-surface-container-low p-4">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div>
                          <p className="text-sm font-bold text-on-surface">
                            Semantic intent tier (Tier 2)
                          </p>
                          <p className="mt-0.5 text-[11px] text-on-surface-variant">
                            Local-embedding fallback for requests that would hit the
                            clarification card. <b>off</b> = never runs (default) ·{' '}
                            <b>shadow</b> = decides in the background and logs to{' '}
                            <code className="font-mono">data/tier2_shadow.jsonl</code>{' '}
                            without acting · <b>enabled</b> = a confident Tier-2 winner
                            auto-picks its intent instead of asking.
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <select
                            value={modeDraft}
                            onChange={(e) => setModeDraft(e.target.value)}
                            title="off | shadow | enabled"
                            className="rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-2 font-plex text-[13px] font-bold text-on-surface shadow-sm outline-none transition-all focus:border-primary focus:ring-4 focus:ring-primary-container"
                          >
                            <option value="off">off</option>
                            <option value="shadow">shadow</option>
                            <option value="enabled">enabled</option>
                          </select>
                          <button
                            onClick={saveModeKnob}
                            disabled={settingsSaving}
                            className="flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 font-plex text-[12px] font-bold text-on-primary shadow-sm transition-all hover:opacity-90 active:scale-95 disabled:opacity-40"
                          >
                            <span className="msi text-[16px]">{settingsSaving ? 'hourglass_top' : 'save'}</span>
                            {settingsSaving ? 'Saving…' : 'Save & apply'}
                          </button>
                        </div>
                      </div>
                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        <span className="rounded-full bg-surface-container-high px-2.5 py-0.5 font-plex text-[10px] font-bold text-on-surface-variant">
                          current: <b className="text-primary">{settings.settings.semantic_tier_mode.value}</b>
                        </span>
                        <span className="rounded-full bg-surface-container-high px-2.5 py-0.5 font-plex text-[10px] font-bold text-on-surface-variant">
                          default: {settings.settings.semantic_tier_mode.default}
                        </span>
                        <span className="rounded-full bg-surface-container-high px-2.5 py-0.5 font-plex text-[10px] font-bold text-on-surface-variant">
                          source: {settings.settings.semantic_tier_mode.source}
                        </span>
                        <span
                          title="Shadow-log health — watch it accumulate while the mode is shadow"
                          className={`flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-plex text-[10px] font-bold ${
                            (health?.today || 0) > 0
                              ? 'bg-green-100 text-green-800'
                              : 'bg-surface-container-high text-on-surface-variant'
                          }`}
                        >
                          <span className={`h-1.5 w-1.5 rounded-full ${(health?.today || 0) > 0 ? 'animate-pulse bg-green-500' : 'bg-outline'}`} />
                          shadow log: <b className="text-primary">{health?.today ?? 0}</b> today ·{' '}
                          {health?.total ?? 0} total · {health?.reviewed ?? 0} reviewed
                        </span>
                      </div>
                    </div>
                  )}
                  {settings.settings?.decisive_match_threshold && (
                    <div className="rounded-xl border border-outline-variant bg-surface-container-low p-4">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div>
                          <p className="text-sm font-bold text-on-surface">
                            Decisive-match family threshold
                          </p>
                          <p className="mt-0.5 text-[11px] text-on-surface-variant">
                            A name search winning at this score only expands its family
                            from records of the SAME merchant — lookalike hits can&apos;t
                            drag their own families into the relationship network.
                            Identifier searches (phone/email/TID/MX) are unaffected.
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <input
                            type="number"
                            min={0}
                            max={100}
                            value={settingsDraft}
                            onChange={(e) => setSettingsDraft(e.target.value)}
                            title="Score threshold 0-100 (85 ≈ 8.5/10, 90 ≈ 9.0/10)"
                            className="w-24 rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-2 text-center font-plex text-[13px] font-bold text-on-surface shadow-sm outline-none transition-all focus:border-primary focus:ring-4 focus:ring-primary-container"
                          />
                          <button
                            onClick={saveSettingsKnobs}
                            disabled={settingsSaving}
                            className="flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 font-plex text-[12px] font-bold text-on-primary shadow-sm transition-all hover:opacity-90 active:scale-95 disabled:opacity-40"
                          >
                            <span className="msi text-[16px]">{settingsSaving ? 'hourglass_top' : 'save'}</span>
                            {settingsSaving ? 'Saving…' : 'Save & apply'}
                          </button>
                          <button
                            onClick={resetSettingsKnobs}
                            disabled={settingsSaving}
                            title="Delete engine_settings.json and fall back to defaults"
                            className="flex items-center gap-1.5 rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-2 font-plex text-[12px] font-bold text-on-surface-variant transition-all hover:bg-surface-container active:scale-95 disabled:opacity-40"
                          >
                            <span className="msi text-[16px]">restore</span>
                            Defaults
                          </button>
                        </div>
                      </div>
                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        <span className="rounded-full bg-surface-container-high px-2.5 py-0.5 font-plex text-[10px] font-bold text-on-surface-variant">
                          current: <b className="text-primary">{settings.settings.decisive_match_threshold.value}</b>
                        </span>
                        <span className="rounded-full bg-surface-container-high px-2.5 py-0.5 font-plex text-[10px] font-bold text-on-surface-variant">
                          default: {settings.settings.decisive_match_threshold.default}
                        </span>
                        <span className="rounded-full bg-surface-container-high px-2.5 py-0.5 font-plex text-[10px] font-bold text-on-surface-variant">
                          source: {settings.settings.decisive_match_threshold.source}
                        </span>
                        <span className="rounded-full bg-surface-container-high px-2.5 py-0.5 font-plex text-[10px] font-bold text-on-surface-variant">
                          range 0–100 · {settings.settings.decisive_match_threshold.value / 10}/10
                        </span>
                      </div>
                    </div>
                  )}
                  {settingsMsg && (
                    <p
                      className={`rounded-lg border px-3 py-2 font-plex text-[11px] font-semibold ${
                        settingsMsg.kind === 'error'
                          ? 'border-error/20 bg-error-container/30 text-error'
                          : settingsMsg.kind === 'success'
                            ? 'border-secondary/20 bg-secondary-container/30 text-on-secondary-container'
                            : 'border-outline-variant bg-surface-container-low text-on-surface-variant'
                      }`}
                    >
                      {settingsMsg.text}
                    </p>
                  )}
                  <p className="text-[11px] leading-relaxed text-on-surface-variant">
                    Stored in <code className="font-mono">{settings.file}</code> — read live on every
                    profile build, so no restart is needed. An env var{' '}
                    <code className="font-mono">DECISIVE_MATCH_THRESHOLD</code> (or the file) wins over
                    the built-in default.
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* Test box */}
          <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
            <div className="flex items-center justify-between border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
              <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
                <span className="msi text-[18px] text-primary">science</span>
                Test a phrase
              </h3>
              <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                uses /api/task/analyze
              </span>
            </div>
            <div className="p-5">
              <div className="flex flex-col gap-2 sm:flex-row">
                <input
                  value={testText}
                  onChange={(e) => setTestText(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && runTest()}
                  placeholder="get me all the addresses of all nnpc stations"
                  className="min-w-0 flex-1 rounded-xl border border-outline-variant bg-surface-container-lowest px-4 py-2.5 font-plex text-[13px] text-on-surface shadow-sm outline-none transition-all focus:border-primary focus:ring-4 focus:ring-primary-container"
                />
                <button
                  onClick={runTest}
                  disabled={!testText.trim() || testLoading}
                  className="flex items-center justify-center gap-2 rounded-xl bg-primary px-5 py-2.5 font-plex text-[13px] font-bold text-on-primary shadow-sm transition-all hover:opacity-90 active:scale-95 disabled:opacity-40"
                >
                  <span className="msi text-[18px]">{testLoading ? 'hourglass_top' : 'play_arrow'}</span>
                  {testLoading ? 'Analyzing…' : 'Analyze'}
                </button>
              </div>

              {/* Key-merchant quick tests — one click fills + runs the box */}
              <div className="mt-3">
                <p className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                  Key-merchant quick tests
                </p>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {KEY_MERCHANT_SAMPLES.map((s) => (
                    <button
                      key={s.text}
                      onClick={() => {
                        setTestText(s.text)
                        setTestRes(null)
                        runTestWith(s.text)
                      }}
                      className="rounded-full border border-tertiary/30 bg-tertiary/5 px-3 py-1 font-plex text-[11px] font-semibold text-tertiary transition-colors hover:border-tertiary hover:bg-tertiary/15"
                    >
                      {s.label}
                    </button>
                  ))}
                </div>
              </div>

              {testRes && (
                <div className="mt-4 rounded-xl border border-outline-variant bg-surface-container-low p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-bold text-on-surface">
                      Primary: <span className="font-mono text-primary">{testRes.primary}</span>
                    </span>
                    <span className="rounded-full bg-primary/10 px-2.5 py-0.5 font-plex text-[11px] font-bold text-primary">
                      confidence {Math.round(testRes.confidence || 0)}
                    </span>
                    <span
                      className={`rounded-full px-2.5 py-0.5 font-plex text-[10px] font-bold ${
                        testRes.is_task ? 'bg-green-100 text-green-800' : 'bg-slate-100 text-slate-700'
                      }`}
                    >
                      {testRes.is_task ? 'executes as a task' : 'plain search'}
                    </span>
                    {(testRes.key_merchants || []).length > 0 && (
                      <span
                        className="rounded-full border border-tertiary/30 bg-tertiary/10 px-2.5 py-0.5 font-plex text-[10px] font-bold uppercase tracking-wider text-tertiary"
                        title="A key merchant root matched the extracted name — this is why the request routed as a task"
                      >
                        <span className="msi align-[-3px] text-[14px]">storefront</span>
                        key merchant: {(testRes.key_merchants || []).join(', ')}
                      </span>
                    )}
                  </div>

                  {/* Clarification: the request reads ambiguously — the
                      backend would ask the user which interpretation to run.
                      analyze() exposes it via the new clarification field. */}
                  {testRes.clarification && (
                    <div className="mt-3 rounded-xl border border-secondary/25 bg-secondary/5 p-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded-full border border-secondary/30 bg-secondary/10 px-2.5 py-1 font-plex text-[10px] font-bold uppercase tracking-wider text-secondary">
                          <span className="msi align-[-3px] text-[14px]">help</span>
                          Would ask for clarification
                        </span>
                        {testRes.gap != null && (
                          <span
                            className="rounded-full border border-tertiary/30 bg-tertiary/10 px-2.5 py-1 font-plex text-[10px] font-bold uppercase tracking-wider text-tertiary"
                            title="Score gap between the top two intents — the engine asks when it falls inside the fitted race window"
                          >
                            <span className="msi align-[-3px] text-[14px]">swap_horiz</span>
                            race gap {testRes.gap}
                          </span>
                        )}
                        {testRes.clarification.auto_pick && (
                          <span
                            className="rounded-full border border-primary/30 bg-primary/10 px-2.5 py-1 font-plex text-[10px] font-bold uppercase tracking-wider text-primary"
                            title="A saved interpretation exists for this phrase — it auto-runs instead of asking"
                          >
                            <span className="msi align-[-3px] text-[14px]">bookmark</span>
                            Auto-picks saved: {String(testRes.clarification.auto_pick).replace(/_/g, ' ')}
                          </span>
                        )}
                      </div>
                      <p className="mt-2 text-[13px] font-medium text-on-surface">
                        {testRes.clarification.question}
                      </p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {(testRes.clarification.options || []).map((o) => (
                          <span
                            key={o.intent}
                            className="flex items-center gap-2 rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-1.5"
                          >
                            <span className="font-plex text-[12px] font-bold text-on-surface">{o.label}</span>
                            <span className="rounded bg-secondary/15 px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase tracking-wider text-secondary">
                              {o.intent.replace(/_/g, ' ')}
                            </span>
                          </span>
                        ))}
                      </div>
                      <p className="mt-2 font-plex text-[11px] text-on-surface-variant">
                        {testRes.clarification.auto_pick
                          ? 'This saved interpretation runs automatically — the clarification card is skipped in Search.'
                          : 'Picking one re-runs the request with that intent forced — or saves it ("remember my choice") for next time.'}
                      </p>
                    </div>
                  )}
                  {testRes.workflow?.workflow?.length > 0 && (
                    <div className="mt-3 flex flex-wrap items-center gap-1.5">
                      <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">Workflow</span>
                      {testRes.workflow.workflow.map((s, i) => (
                        <span key={s} className="flex items-center gap-1.5">
                          {i > 0 && <span className="msi text-[14px] text-outline">arrow_forward</span>}
                          <span className="rounded-md bg-primary/10 px-2 py-0.5 font-mono text-[10px] font-bold text-primary">{s}</span>
                        </span>
                      ))}
                    </div>
                  )}
                  {testRes.excluded?.length > 0 && (
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">Excluded</span>
                      {testRes.excluded.map((e) => (
                        <span key={e} className="rounded-md bg-red-100 px-2 py-0.5 font-mono text-[10px] font-bold text-red-700 line-through">{e}</span>
                      ))}
                    </div>
                  )}
                  {(testRes.intents || []).length > 0 && (
                    <div className="mt-3 space-y-2">
                      <p className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                        Intent breakdown
                      </p>
                      {testRes.intents.map((it) => (
                        <div key={it.intent} className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-[12px] font-bold text-on-surface">{it.intent}</span>
                          <span className="rounded bg-surface-container-high px-2 py-0.5 font-plex text-[10px] font-semibold text-on-surface-variant">
                            score {it.score}
                          </span>
                          <span className="rounded bg-surface-container-high px-2 py-0.5 font-plex text-[10px] font-semibold text-on-surface-variant">
                            conf {Math.round(it.confidence || 0)}
                          </span>
                          {it.matched?.length > 0 && (
                            <span className="flex min-w-0 flex-wrap items-center gap-1">
                              {it.matched.map((m) => {
                                const chip = matchedChip(m)
                                return (
                                  <span
                                    key={m}
                                    title={chip.title}
                                    className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[9px] font-bold ${chip.cls}`}
                                  >
                                    {chip.icon && <span className="msi text-[11px]">{chip.icon}</span>}
                                    <span className="max-w-[220px] truncate">{chip.label}</span>
                                  </span>
                                )
                              })}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Config help */}
          {cfg?.help && (
            <details className="group overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
              <summary className="flex cursor-pointer items-center gap-2 border-b border-outline-variant bg-surface-container-low px-5 py-3.5 font-plex text-[12px] font-bold text-on-surface transition-colors hover:bg-surface-container">
                <span className="msi text-[18px] text-primary">menu_book</span>
                How this file works (from _help)
                <span className="msi ml-auto text-[18px] text-outline transition-transform group-open:rotate-90">chevron_right</span>
              </summary>
              <pre className="whitespace-pre-wrap p-5 font-plex text-[12px] leading-relaxed text-on-surface-variant">
                {cfg.help}
              </pre>
            </details>
          )}

          {/* Confidence calibration */}
          <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
              <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
                <span className="msi text-[18px] text-primary">monitoring</span>
                Confidence calibration
              </h3>
              <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                learns from real requests · data/request_log.jsonl
              </span>
            </div>
            <div className="p-5">
              {!cal ? (
                <p className="py-2 text-center font-plex text-[12px] text-on-surface-variant">
                  Loading calibration…
                </p>
              ) : (
                <div className="space-y-5">
                  {/* Status row */}
                  <div className="flex flex-wrap items-center gap-3">
                    <span
                      className={`rounded-full px-3 py-1 font-plex text-[11px] font-bold ${
                        cal.fit?.active ? 'bg-green-100 text-green-800' : 'bg-amber-100 text-amber-800'
                      }`}
                      title={
                        cal.fit?.active
                          ? `Using fitted thresholds from ${cal.fit.samples} logged decisions`
                          : `Needs ${cal.fit?.min_samples || 20} logged decisions to start fitting (${cal.fit?.samples || 0} so far)`
                      }
                    >
                      {cal.fit?.active ? '● fitted from usage' : `○ learning — ${cal.fit?.samples || 0}/${cal.fit?.min_samples || 20} decisions`}
                    </span>
                    <span className="rounded-full bg-surface-container-high px-3 py-1 font-plex text-[11px] font-bold text-on-surface-variant">
                      {cal.stats?.decisions || 0} decisions · {(cal.stats?.acceptance || 0) * 100}% accepted
                    </span>
                    <span
                      className={`rounded-full px-3 py-1 font-plex text-[11px] font-bold ${
                        cal.fit?.active && cal.params?.ask_threshold < cal.fit?.default_ask
                          ? 'bg-green-100 text-green-800'
                          : 'bg-surface-container-high text-on-surface-variant'
                      }`}
                      title={`Ask-for-confirmation threshold: confidence below this gets asked first (default ${cal.fit?.default_ask})`}
                    >
                      ask below {cal.params?.ask_threshold ?? '—'} (default {cal.fit?.default_ask})
                    </span>
                    <span
                      className={`rounded-full px-3 py-1 font-plex text-[11px] font-bold ${
                        cal.fit?.gap_active && cal.params?.gap_threshold < cal.fit?.default_gap
                          ? 'bg-green-100 text-green-800'
                          : 'bg-surface-container-high text-on-surface-variant'
                      }`}
                      title={`Race window: when the top two intents score within this score gap, the engine asks (default ${cal.fit?.default_gap})`}
                    >
                      race below {cal.params?.gap_threshold ?? '—'} pts (default {cal.fit?.default_gap})
                    </span>
                    {!cal.fit?.gap_active && (
                      <span
                        className="rounded-full bg-amber-50 px-3 py-1 font-plex text-[11px] font-bold text-amber-800"
                        title={`Needs ${cal.fit?.gap_min_samples || 20} logged race outcomes (clarification picks that carried a top-2 score gap) to start fitting the race window`}
                      >
                        ○ race fitting — {cal.fit?.race_samples || 0}/{cal.fit?.gap_min_samples || 20}
                      </span>
                    )}
                    <button
                      onClick={refreshCal}
                      disabled={calLoading}
                      className="ml-auto flex items-center gap-1.5 rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-1.5 font-plex text-[11px] font-bold text-on-surface-variant transition-all hover:border-primary hover:text-primary active:scale-95 disabled:opacity-40"
                    >
                      <span className="msi text-[15px]">{calLoading ? 'hourglass_top' : 'refresh'}</span>
                      Refresh
                    </button>
                  </div>

                  {/* Threshold meter */}
                  <div>
                    <div className="mb-1.5 flex items-center justify-between font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                      <span>confidence →</span>
                      <span>0 · 20 · 40 · 60 · 80 · 100</span>
                    </div>
                    <div className="relative h-3 overflow-hidden rounded-full bg-surface-container-high">
                      <div
                        className="absolute inset-y-0 left-0 rounded-full bg-secondary/70"
                        style={{ width: `${(cal.params?.ask_threshold ?? cal.fit?.default_ask ?? 60) / 100}%` }}
                        title="Ask-for-confirmation region (below threshold)"
                      />
                      <div
                        className="absolute inset-y-0 w-0.5 bg-on-surface"
                        style={{ left: `${(cal.params?.ask_threshold ?? 60) / 100}%` }}
                        title={`Ask threshold: ${cal.params?.ask_threshold}`}
                      />
                      {cal.fit?.default_ask && cal.params?.ask_threshold !== cal.fit.default_ask && (
                        <div
                          className="absolute inset-y-0 w-0.5 bg-outline"
                          style={{ left: `${cal.fit.default_ask / 100}%` }}
                          title={`Default threshold: ${cal.fit.default_ask}`}
                        />
                      )}
                    </div>
                    <p className="mt-1.5 font-plex text-[11px] text-on-surface-variant">
                      Requests scoring below the threshold are flagged for confirmation before running.{' '}
                      {cal.fit?.active && cal.params?.ask_threshold !== cal.fit?.default_ask
                        ? `The engine lowered/raised it from the default (${cal.fit.default_ask}) based on how often users accept auto-routed intents.`
                        : 'The threshold starts at the default until enough real requests are logged.'}
                    </p>
                  </div>

                  {/* Race-window meter (fitted gap_threshold) */}
                  <div>
                    <div className="mb-1.5 flex items-center justify-between font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                      <span>race window (top-2 score gap) →</span>
                      <span>0 · 1 · 2 · 3 · 4 · 6</span>
                    </div>
                    <div className="relative h-3 overflow-hidden rounded-full bg-surface-container-high">
                      <div
                        className="absolute inset-y-0 left-0 rounded-full bg-tertiary/60"
                        style={{ width: `${Math.min(100, ((cal.params?.gap_threshold ?? cal.fit?.default_gap ?? 4) / (cal.fit?.gap_ceiling ?? 6)) * 100)}%` }}
                        title="Race window: top two intents scoring within this gap get asked"
                      />
                      <div
                        className="absolute inset-y-0 w-0.5 bg-on-surface"
                        style={{ left: `${Math.min(100, ((cal.params?.gap_threshold ?? 4) / (cal.fit?.gap_ceiling ?? 6)) * 100)}%` }}
                        title={`Race gap threshold: ${cal.params?.gap_threshold}`}
                      />
                      {cal.fit?.default_gap && cal.params?.gap_threshold !== cal.fit.default_gap && (
                        <div
                          className="absolute inset-y-0 w-0.5 bg-outline"
                          style={{ left: `${Math.min(100, (cal.fit.default_gap / (cal.fit?.gap_ceiling ?? 6)) * 100)}%` }}
                          title={`Default race window: ${cal.fit.default_gap}`}
                        />
                      )}
                    </div>
                    <p className="mt-1.5 font-plex text-[11px] text-on-surface-variant">
                      When the top two intents score within this gap (and the top is below the ask
                      threshold), the engine asks which one was meant instead of guessing.{' '}
                      {cal.fit?.gap_active && cal.params?.gap_threshold !== cal.fit?.default_gap
                        ? `Tuned from the default (${cal.fit.default_gap}) using ${cal.fit.race_samples} logged race outcomes — how users actually resolved close calls.`
                        : `Fitted from logged race outcomes once ${cal.fit?.gap_min_samples || 20} clarification picks carry a top-2 gap (${cal.fit?.race_samples || 0} so far).`}
                    </p>
                  </div>

                  {/* Band acceptance */}
                  {cal.stats?.bands?.length > 0 && (
                    <div>
                      <p className="mb-2 font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                        Acceptance by confidence band (what actually got accepted vs corrected)
                      </p>
                      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                        {cal.stats.bands.map((b) => (
                          <div key={b.band} className="rounded-lg border border-outline-variant bg-surface-container-low p-3">
                            <div className="flex items-center justify-between">
                              <span className="font-mono text-[11px] font-bold text-on-surface">{b.band}</span>
                              <span className="font-plex text-[10px] text-on-surface-variant">{b.samples} samples</span>
                            </div>
                            <div className="mt-1.5 flex items-center gap-2">
                              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-outline-variant">
                                <div
                                  className={`h-full rounded-full ${b.acceptance >= (cal.fit?.target_acceptance || 0.8) ? 'bg-secondary' : 'bg-error'}`}
                                  style={{ width: `${Math.round((b.acceptance || 0) * 100)}%` }}
                                />
                              </div>
                              <span className="font-plex text-[11px] font-bold text-on-surface-variant">
                                {Math.round((b.acceptance || 0) * 100)}%
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Race-gap acceptance (the gap_threshold fit's input) */}
                  {cal.stats?.gap_bands?.length > 0 && (
                    <div>
                      <p className="mb-2 font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                        Acceptance by race gap (how close races actually resolved — the race window's evidence)
                      </p>
                      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                        {cal.stats.gap_bands.map((b) => (
                          <div key={b.band} className="rounded-lg border border-outline-variant bg-surface-container-low p-3">
                            <div className="flex items-center justify-between">
                              <span className="font-mono text-[11px] font-bold text-on-surface">{b.band}</span>
                              <span className="font-plex text-[10px] text-on-surface-variant">{b.samples} races</span>
                            </div>
                            <div className="mt-1.5 flex items-center gap-2">
                              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-outline-variant">
                                <div
                                  className={`h-full rounded-full ${b.acceptance >= (cal.fit?.target_acceptance || 0.8) ? 'bg-tertiary' : 'bg-error'}`}
                                  style={{ width: `${Math.round((b.acceptance || 0) * 100)}%` }}
                                />
                              </div>
                              <span className="font-plex text-[11px] font-bold text-on-surface-variant">
                                {Math.round((b.acceptance || 0) * 100)}%
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Per-intent acceptance */}
                  {cal.stats?.per_intent?.length > 0 && (
                    <div>
                      <p className="mb-2 font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                        Acceptance by intent
                      </p>
                      <div className="flex flex-wrap gap-2">
                        {cal.stats.per_intent.map((p) => (
                          <span
                            key={p.intent}
                            className="rounded-full border border-outline-variant bg-surface-container-low px-3 py-1 font-plex text-[10px] font-bold text-on-surface-variant"
                            title={`${p.samples} samples · ${p.accepted} accepted`}
                          >
                            {p.intent}: {Math.round(p.acceptance * 100)}%
                            <span className="ml-1 text-[9px] font-semibold text-outline">({p.samples})</span>
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  <div className="flex flex-wrap items-center justify-between gap-2 border-t border-outline-variant pt-4">
                    <span className="flex flex-wrap items-center gap-1.5 font-plex text-[11px] text-on-surface-variant">
                      <span>Auto-routed: <b className="text-on-surface">{cal.stats?.sources?.auto || 0}</b></span>
                      <span className="text-outline">·</span>
                      <span title="User confirmed the engine's predicted intent on a clarification card">
                        Accepted: <b className="text-secondary">{cal.stats?.sources?.accept || 0}</b>
                      </span>
                      <span className="text-outline">·</span>
                      <span title="User corrected the engine's predicted intent on a clarification card">
                        Overridden: <b className="text-error">{cal.stats?.sources?.override || 0}</b>
                      </span>
                    </span>
                    <button
                      onClick={resetCal}
                      disabled={calLoading || !cal.stats?.decisions}
                      className="flex items-center gap-1.5 rounded-lg border border-error/30 bg-error-container/20 px-3 py-1.5 font-plex text-[11px] font-bold text-error transition-all hover:bg-error-container/40 active:scale-95 disabled:opacity-40"
                    >
                      <span className="msi text-[15px]">delete_sweep</span>
                      Reset decision log
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Saved interpretations ("remember my choice") */}
          <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
              <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
                <span className="msi text-[18px] text-primary">bookmark</span>
                Saved interpretations
              </h3>
              <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                “remember my choice” · data/clarification_preferences.json
              </span>
            </div>
            <div className="p-5">
              {!prefs ? (
                <p className="py-2 text-center font-plex text-[12px] text-on-surface-variant">
                  Loading saved interpretations…
                </p>
              ) : prefs.count === 0 ? (
                <div className="rounded-lg border border-dashed border-outline-variant px-4 py-6 text-center">
                  <p className="font-plex text-[12px] text-on-surface-variant">
                    None saved yet. When an ambiguous request shows the
                    “Which did you mean?” card, check <b>Remember my choice</b> —
                    the phrase → interpretation is saved here so next time it auto-runs.
                  </p>
                </div>
              ) : (
                <div className="space-y-2">
                  {prefs.preferences.map((p) => (
                    <div
                      key={p.key}
                      className="flex items-center gap-3 rounded-lg border border-outline-variant bg-surface-container-low px-3 py-2.5"
                    >
                      <span className="msi shrink-0 text-[18px] text-secondary">bookmark</span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-mono text-[12px] font-bold text-on-surface" title={p.key}>
                          “{p.key}”
                        </p>
                        <p className="text-[11px] text-on-surface-variant">
                          → <b className="text-primary">{p.intent.replace(/_/g, ' ')}</b>
                          {p.label && <span className="text-outline"> · {p.label}</span>}
                        </p>
                      </div>
                      <button
                        onClick={() => forgetPref(p.key)}
                        title={`Forget “${p.key}”`}
                        className="shrink-0 rounded-lg p-2 text-error transition-all hover:bg-error-container/40 active:scale-95"
                      >
                        <span className="msi text-[18px]">delete</span>
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* ── Tier 1 enrichment — WordNet synonym proposals ── */}
          <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
              <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
                <span className="msi text-[18px] text-primary">auto_awesome</span>
                Tier 1 enrichment — WordNet synonyms
              </h3>
              <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                build-time · data/exemplar_candidates.json
              </span>
            </div>
            <div className="p-5">
              {!syn ? (
                <p className="py-2 text-center font-plex text-[12px] text-on-surface-variant">
                  Loading synonym proposals…
                </p>
              ) : (
                <div className="space-y-5">
                  {/* Status row */}
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="rounded-full bg-surface-container-high px-3 py-1 font-plex text-[11px] font-bold text-on-surface-variant">
                      {syn.count || 0} proposals
                    </span>
                    <span className="rounded-full bg-amber-50 px-3 py-1 font-plex text-[11px] font-bold text-amber-800">
                      {syn.by_status?.pending || 0} pending
                    </span>
                    <span className="rounded-full bg-green-100 px-3 py-1 font-plex text-[11px] font-bold text-green-800">
                      {syn.by_status?.approved || 0} approved
                    </span>
                    <span className="rounded-full bg-blue-100 px-3 py-1 font-plex text-[11px] font-bold text-blue-800">
                      {syn.by_status?.applied || 0} applied
                    </span>
                    {syn.wordnet && !(syn.wordnet.nltk && syn.wordnet.wordnet) && (
                      <span
                        className="rounded-full bg-error-container px-3 py-1 font-plex text-[11px] font-bold text-error"
                        title={syn.wordnet.hint}
                      >
                        wordnet unavailable
                      </span>
                    )}
                    <button
                      onClick={refreshSyn}
                      disabled={synLoading}
                      className="ml-auto flex items-center gap-1.5 rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-1.5 font-plex text-[11px] font-bold text-on-surface-variant transition-all hover:border-primary hover:text-primary active:scale-95 disabled:opacity-40"
                    >
                      <span className="msi text-[15px]">{synLoading ? 'hourglass_top' : 'refresh'}</span>
                      Refresh
                    </button>
                  </div>

                  {/* Actions */}
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      onClick={generateSyn}
                      disabled={synLoading}
                      className="flex items-center gap-1.5 rounded-lg border border-outline-variant bg-surface-container-lowest px-3.5 py-2 font-plex text-[11px] font-bold text-on-surface-variant transition-all hover:border-primary hover:text-primary active:scale-95 disabled:opacity-40"
                    >
                      <span className="msi text-[16px]">auto_awesome</span>
                      Generate proposals
                    </button>
                    <button
                      onClick={() => markSyn('approved')}
                      disabled={synLoading || !synSel.size}
                      className="flex items-center gap-1.5 rounded-lg border border-secondary/40 bg-secondary-container/30 px-3.5 py-2 font-plex text-[11px] font-bold text-on-secondary-container transition-all hover:bg-secondary-container/60 active:scale-95 disabled:opacity-40"
                    >
                      <span className="msi text-[16px]">check</span>
                      Approve selected ({synSel.size})
                    </button>
                    <button
                      onClick={() => markSyn('rejected')}
                      disabled={synLoading || !synSel.size}
                      className="flex items-center gap-1.5 rounded-lg border border-error/30 bg-error-container/20 px-3.5 py-2 font-plex text-[11px] font-bold text-error transition-all hover:bg-error-container/40 active:scale-95 disabled:opacity-40"
                    >
                      <span className="msi text-[16px]">close</span>
                      Reject selected
                    </button>
                    <button
                      onClick={applySyn}
                      disabled={synLoading || !(syn.by_status?.approved)}
                      className="flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 font-plex text-[11px] font-bold text-on-primary shadow-sm transition-all hover:opacity-90 active:scale-95 disabled:opacity-40"
                    >
                      <span className="msi text-[16px]">merge</span>
                      Apply approved
                    </button>
                  </div>

                  {/* Candidate list grouped by intent */}
                  {synGroups.length > 0 ? (
                    <div className="space-y-3">
                      <p className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                        WordNet-proposed synonym phrases for the regex tier — check the good ones, Approve, then Apply. Applied patterns join intents.json as weight-2 entries (lockstep-synced with the code defaults so the regression suite stays green).
                      </p>
                      {synGroups.map(([intent, cands]) => (
                        <div key={intent} className="rounded-xl border border-outline-variant bg-surface-container-low p-4">
                          <div className="mb-2 flex flex-wrap items-center gap-2">
                            <span className="font-mono text-[12px] font-bold text-primary">{intent}</span>
                            <span className="font-plex text-[10px] text-on-surface-variant">{cands.length} candidate(s)</span>
                          </div>
                          <div className="flex flex-wrap gap-2">
                            {cands.map((c) => {
                              const disabled = c.status === 'applied' || c.status === 'rejected'
                              return (
                                <label
                                  key={c.id}
                                  className={`flex cursor-pointer items-center gap-1.5 rounded-lg border px-2.5 py-1.5 font-plex text-[11px] transition-all ${
                                    disabled
                                      ? 'border-outline-variant bg-surface-container-high/60 text-outline'
                                      : synSel.has(c.id)
                                        ? 'border-primary bg-primary/10 font-bold text-primary'
                                        : 'border-outline-variant bg-surface-container-lowest text-on-surface-variant hover:border-primary'
                                  }`}
                                  title={`${c.phrase} — from “${c.source_phrase}” (${c.synonym})${c.conflict ? ` · ⚠ also a pattern for ${c.conflict_with.join(', ')}` : ''}`}
                                >
                                  <input
                                    type="checkbox"
                                    checked={synSel.has(c.id)}
                                    disabled={disabled}
                                    onChange={() => toggleSynSel(c.id)}
                                    className="accent-primary"
                                  />
                                  <span>“{c.phrase}”</span>
                                  {c.status === 'applied' && <span className="text-[9px] font-bold text-blue-600">applied</span>}
                                  {c.status === 'rejected' && <span className="text-[9px] font-bold text-error">rejected</span>}
                                  {c.conflict && <span className="text-[11px] text-error">⚠</span>}
                                </label>
                              )
                            })}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="rounded-lg border border-dashed border-outline-variant px-4 py-8 text-center">
                      <p className="font-plex text-[12px] text-on-surface-variant">
                        No proposals yet — click <b>Generate proposals</b> to WordNet-expand the current
                        patterns into candidate synonym phrases, then approve the ones that fit your
                        operations language.
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* ── Tier 2 spot-check — shadow decisions review ── */}
          <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
              <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
                <span className="msi text-[18px] text-primary">fact_check</span>
                Tier 2 spot-check — shadow decisions
              </h3>
              <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                review the auto-run band · data/tier2_shadow.jsonl
              </span>
            </div>
            <div className="p-5">
              <p className="mb-4 font-plex text-[12px] leading-relaxed text-on-surface-variant">
                The embedding tier only logs decisions that would have been <b>asked</b> as a
                clarification — so the confident auto-run band (<b>would act</b>) never gets a free
                label from real usage. This panel is the manual spot-check the Phase-2 go/no-go
                needs: mark each decision correct/wrong to build per-intent precision.
              </p>

              <div className="mb-4 flex flex-wrap items-center gap-2">
                {['would_act', 'would_not', 'all'].map((b) => (
                  <button
                    key={b}
                    onClick={() => switchSrBand(b)}
                    className={`rounded-full px-3 py-1.5 font-plex text-[11px] font-bold transition-all active:scale-95 ${
                      srBand === b
                        ? 'bg-primary text-on-primary'
                        : 'border border-outline-variant bg-surface-container-lowest text-on-surface-variant hover:border-primary hover:text-primary'
                    }`}
                  >
                    {b === 'would_act' ? 'would act' : b === 'would_not' ? 'would not act' : 'all'}
                  </button>
                ))}
                <button
                  onClick={refreshSr}
                  disabled={srLoading}
                  className="ml-auto flex items-center gap-1.5 rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-1.5 font-plex text-[11px] font-bold text-on-surface-variant transition-all hover:border-primary hover:text-primary active:scale-95 disabled:opacity-40"
                >
                  <span className="msi text-[15px]">{srLoading ? 'hourglass_top' : 'refresh'}</span>
                  Refresh
                </button>
              </div>

              {sr?.stats && (sr.stats.reviewed > 0 || sr.stats.band_total > 0) && (
                <div className="mb-4 flex flex-wrap items-center gap-3">
                  <span className="rounded-full bg-surface-container-high px-3 py-1 font-plex text-[11px] font-bold text-on-surface-variant">
                    {sr.stats.band_total || 0} in band
                  </span>
                  <span className="rounded-full bg-green-100 px-3 py-1 font-plex text-[11px] font-bold text-green-800">
                    {sr.stats.reviewed || 0} reviewed
                  </span>
                  <span className={`rounded-full px-3 py-1 font-plex text-[11px] font-bold ${
                    (sr.stats.precision || 0) >= 0.85
                      ? 'bg-green-100 text-green-800'
                      : (sr.stats.precision || 0) >= 0.6
                        ? 'bg-amber-50 text-amber-800'
                        : 'bg-error-container/40 text-error'
                  }`}
                  >
                    precision {(sr.stats.precision || 0) * 100}%
                  </span>
                  {sr.stats.precision >= 0.85 && sr.stats.reviewed >= 10 && (
                    <span className="rounded-full bg-green-100 px-3 py-1 font-plex text-[11px] font-bold text-green-800">
                      Phase-2 go/no-go: ready to consider enabling
                    </span>
                  )}
                </div>
              )}

              {sr?.tier2_fit && Object.keys(sr.tier2_fit.per_intent || {}).length > 0 && (
                <div className="mb-4 overflow-hidden rounded-xl border border-outline-variant">
                  <div className="flex items-center justify-between bg-surface-container-high px-4 py-2">
                    <span className="font-plex text-[11px] font-bold text-on-surface-variant">
                      Tier-2 fitted gates (Phase 3)
                    </span>
                    <span className="font-mono text-[10px] text-on-surface-variant">
                      margin {sr.tier2_fit.margin ?? sr.tier2_fit.defaults.margin}
                    </span>
                  </div>
                  <table className="w-full text-left font-mono text-[11px]">
                    <thead className="bg-surface-container-low text-on-surface-variant">
                      <tr>
                        <th className="px-4 py-1.5 font-bold">intent</th>
                        <th className="px-2 py-1.5 font-bold">labeled</th>
                        <th className="px-2 py-1.5 font-bold">precision</th>
                        <th className="px-4 py-1.5 font-bold">threshold</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(sr.tier2_fit.per_intent).map(([intent, g]) => (
                        <tr key={intent} className="border-t border-outline-variant/60">
                          <td className="px-4 py-1.5 font-bold text-primary">{intent}</td>
                          <td className="px-2 py-1.5">
                            {g.samples}/{g.needed}
                            {g.would_not_correct > 0 && (
                              <span className="ml-1 text-outline" title="correct would-not picks (lowering evidence)">
                                +{g.would_not_correct}
                              </span>
                            )}
                          </td>
                          <td className="px-2 py-1.5">
                            {g.precision != null ? `${(g.precision * 100).toFixed(0)}%` : '–'}
                          </td>
                          <td className="px-4 py-1.5">
                            {g.threshold != null ? (
                              <span className="rounded bg-primary/10 px-1.5 py-0.5 font-bold text-primary">
                                {g.threshold}
                              </span>
                            ) : (
                              <span className="text-outline">needs {g.needed - g.samples} more</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {sr && sr.entries?.length ? (
                <div className="space-y-3">
                  {sr.entries.map((e) => (
                    <div
                      key={e.entry_id}
                      className={`rounded-xl border p-4 ${
                        e.label
                          ? e.label.correct
                            ? 'border-green-300 bg-green-50/40'
                            : 'border-error/30 bg-error-container/20'
                          : 'border-outline-variant bg-surface-container-low'
                      }`}
                    >
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <p className="mb-1 break-words font-plex text-[12px] text-on-surface">
                            {e.text}
                          </p>
                          <div className="flex flex-wrap items-center gap-2 font-mono text-[10px] text-on-surface-variant">
                            <span className="rounded bg-primary/10 px-1.5 py-0.5 font-bold text-primary">
                              {e.tier2_intent}
                            </span>
                            <span>conf {e.tier2_confidence}</span>
                            <span>margin {e.tier2_margin}</span>
                            <span title={`matched exemplar: ${e.tier2_exemplar}`}>
                              “{String(e.tier2_exemplar || '').slice(0, 40)}”
                            </span>
                            {e.tier1_intent && (
                              <span className="text-outline">tier1 {e.tier1_intent}</span>
                            )}
                          </div>
                          {e.label?.note && (
                            <p className="mt-1 font-plex text-[11px] italic text-on-surface-variant">
                              {e.label.note}
                            </p>
                          )}
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          <button
                            onClick={() => labelSr(e.entry_id, true)}
                            disabled={srLoading}
                            className="flex items-center gap-1 rounded-lg border border-green-300 bg-green-50 px-2.5 py-1.5 font-plex text-[11px] font-bold text-green-800 transition-all hover:bg-green-100 active:scale-95 disabled:opacity-40"
                          >
                            <span className="msi text-[14px]">check</span>Correct
                          </button>
                          <button
                            onClick={() => labelSr(e.entry_id, false)}
                            disabled={srLoading}
                            className="flex items-center gap-1 rounded-lg border border-error/30 bg-error-container/20 px-2.5 py-1.5 font-plex text-[11px] font-bold text-error transition-all hover:bg-error-container/40 active:scale-95 disabled:opacity-40"
                          >
                            <span className="msi text-[14px]">close</span>Wrong
                          </button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-lg border border-dashed border-outline-variant px-4 py-8 text-center">
                  <p className="font-plex text-[12px] text-on-surface-variant">
                    {srLoading
                      ? 'Loading…'
                      : 'No shadow decisions in this band yet — switch the semantic-tier mode to shadow and let real requests accumulate, then review them here.'}
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* ── Learning — pattern suggestions ── */}
          <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
              <h3 className="flex items-center gap-2 text-sm font-bold text-on-surface">
                <span className="msi text-[18px] text-primary">psychology</span>
                Learning — pattern suggestions
              </h3>
              <span className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                mined from corrections · data/requests_log.jsonl
              </span>
            </div>
            <div className="p-5">
              {!sugs ? (
                <p className="py-2 text-center font-plex text-[12px] text-on-surface-variant">
                  Loading suggestion data…
                </p>
              ) : (
                <div className="space-y-5">
                  {/* Status row */}
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="rounded-full bg-surface-container-high px-3 py-1 font-plex text-[11px] font-bold text-on-surface-variant">
                      {sugs.stats?.logged || 0} requests logged
                    </span>
                    <span className="rounded-full bg-green-100 px-3 py-1 font-plex text-[11px] font-bold text-green-800">
                      {sugs.stats?.accepted || 0} accepted
                    </span>
                    <span className="rounded-full bg-blue-100 px-3 py-1 font-plex text-[11px] font-bold text-blue-800" title="Clarification picks where the user corrected the engine's prediction">
                      {sugs.stats?.overridden || 0} overridden
                    </span>
                    <span className="rounded-full bg-amber-100 px-3 py-1 font-plex text-[11px] font-bold text-amber-800" title="Empty-result requests that were re-asked with different wording">
                      {sugs.stats?.rephrased || 0} rephrased
                    </span>
                    <span className="rounded-full bg-surface-container-high px-3 py-1 font-plex text-[11px] font-bold text-on-surface-variant" title="Produced no results and no follow-up came within the window">
                      {sugs.stats?.abandoned || 0} abandoned
                    </span>
                    {sugs.stats?.pending > 0 && (
                      <span className="rounded-full bg-purple-100 px-3 py-1 font-plex text-[11px] font-bold text-purple-800" title="Recent requests with no results yet — waiting to see if a follow-up arrives">
                        {sugs.stats.pending} pending
                      </span>
                    )}
                    <button
                      onClick={refreshSugs}
                      disabled={sugLoading}
                      className="ml-auto flex items-center gap-1.5 rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-1.5 font-plex text-[11px] font-bold text-on-surface-variant transition-all hover:border-primary hover:text-primary active:scale-95 disabled:opacity-40"
                    >
                      <span className="msi text-[15px]">{sugLoading ? 'hourglass_top' : 'refresh'}</span>
                      Refresh
                    </button>
                  </div>

                  {/* Suggestions list */}
                  {sugs.suggestions?.length > 0 ? (
                    <div className="space-y-3">
                      <p className="font-plex text-[10px] font-bold uppercase tracking-wider text-outline">
                        {sugs.suggestions.length} suggestion(s) — phrases found in corrections that are not yet covered by existing patterns. Accept to write to intents.json (hot-reloaded).
                      </p>
                      {sugs.suggestions.map((s, i) => (
                        <div key={`${s.ngram}-${s.intent}`} className="rounded-xl border border-outline-variant bg-surface-container-low p-4">
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="whitespace-nowrap rounded-md bg-primary/10 px-2.5 py-1 font-mono text-[13px] font-bold text-primary">
                                  “{s.ngram}”
                                </span>
                                <span className="msi text-[18px] text-outline">arrow_forward</span>
                                <span className="font-mono text-[13px] font-bold text-on-surface">{s.intent}</span>
                                <span className="rounded-full bg-surface-container-high px-2.5 py-0.5 font-plex text-[10px] font-bold text-on-surface-variant">
                                  weight {s.weight}
                                </span>
                                <span className="rounded-full bg-green-50 px-2.5 py-0.5 font-plex text-[10px] font-bold text-green-800">
                                  {s.samples} sample{s.samples !== 1 ? 's' : ''}
                                </span>
                              </div>
                              {s.examples?.length > 0 && (
                                <div className="mt-2 space-y-1">
                                  {s.examples.map((ex, j) => (
                                    <p key={j} className="truncate font-mono text-[10px] text-on-surface-variant" title={ex}>
                                      {ex}
                                    </p>
                                  ))}
                                </div>
                              )}
                              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                                {(s.labels || []).map((l) => (
                                  <span key={l} className="rounded-full bg-surface-container-high px-2 py-0.5 font-plex text-[9px] font-bold text-outline">
                                    {l}
                                  </span>
                                ))}
                              </div>
                            </div>
                            <div className="flex shrink-0 items-center gap-1.5">
                              <button
                                onClick={() => applySug(s)}
                                className="flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 font-plex text-[11px] font-bold text-on-primary shadow-sm transition-all hover:opacity-90 active:scale-95"
                              >
                                <span className="msi text-[16px]">add</span>
                                Accept
                              </button>
                              <button
                                onClick={() => rejectSug(s)}
                                className="flex items-center gap-1.5 rounded-lg border border-outline-variant bg-surface-container-lowest px-3.5 py-2 font-plex text-[11px] font-bold text-on-surface-variant transition-all hover:bg-surface-container active:scale-95"
                              >
                                <span className="msi text-[16px]">close</span>
                                Reject
                              </button>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="rounded-lg border border-dashed border-outline-variant px-4 py-8 text-center">
                      <p className="font-plex text-[12px] text-on-surface-variant">
                        {sugs.stats?.logged > 0
                          ? 'No suggestions yet — the engine has not found any recurring corrections to learn from. Keep using the app; suggestions appear as requests are overridden (clarification picks) or rephrased (empty-result requests that get re-asked).'
                          : 'Start using the app — search merchants, run task requests. The engine learns from every request: corrections, rephrased queries, and accepted results.'}
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {calMsg && (
        <div
          className={`rounded-xl border px-5 py-3 font-plex text-[13px] font-semibold ${
            calMsg.kind === 'error'
              ? 'border-error/20 bg-error-container/30 text-error'
              : calMsg.kind === 'success'
                ? 'border-secondary/20 bg-secondary-container/30 text-on-secondary-container'
                : 'border-outline-variant bg-surface-container-low text-on-surface-variant'
          }`}
        >
          {calMsg.text}
        </div>
      )}
      {sugMsg && (
        <div
          className={`rounded-xl border px-5 py-3 font-plex text-[13px] font-semibold ${
            sugMsg.kind === 'error'
              ? 'border-error/20 bg-error-container/30 text-error'
              : sugMsg.kind === 'success'
                ? 'border-secondary/20 bg-secondary-container/30 text-on-secondary-container'
                : 'border-outline-variant bg-surface-container-low text-on-surface-variant'
          }`}
        >
          {sugMsg.text}
        </div>
      )}
      {synMsg && (
        <div
          className={`rounded-xl border px-5 py-3 font-plex text-[13px] font-semibold ${
            synMsg.kind === 'error'
              ? 'border-error/20 bg-error-container/30 text-error'
              : synMsg.kind === 'success'
                ? 'border-secondary/20 bg-secondary-container/30 text-on-secondary-container'
                : 'border-outline-variant bg-surface-container-low text-on-surface-variant'
          }`}
        >
          {synMsg.text}
        </div>
      )}
      {srMsg && (
        <div
          className={`rounded-xl border px-5 py-3 font-plex text-[13px] font-semibold ${
            srMsg.kind === 'error'
              ? 'border-error/20 bg-error-container/30 text-error'
              : srMsg.kind === 'success'
                ? 'border-secondary/20 bg-secondary-container/30 text-on-secondary-container'
                : 'border-outline-variant bg-surface-container-low text-on-surface-variant'
          }`}
        >
          {srMsg.text}
        </div>
      )}
    </div>
  )
}
