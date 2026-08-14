import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { bankName, plausibleId } from '../utils/bank'
import { LINK_META, LINK_FIELDS, linkMeta, initials, truncate } from '../utils/links'

const EXAMPLES = ['LAGOON WATERS', 'MONEYTRUST MICROFINANCE', 'THE FILM HOUSE', 'SPAR']

const VW = 1200 // virtual canvas width (viewBox units)
const VH = 880
const CX = VW / 2
const CY = VH / 2

// Hub-cluster geometry (pure trig, deterministic — no physics):
//   center disc = the seed merchant
//   ring 1      = one hub per link type present (email / phone / TID / MX …)
//   rings 2+    = the records, fanned in an arc inside their hub's angular
//                 wedge. Crowded hubs spread into concentric bands; deeper
//                 hops (2nd/3rd degree) sit on progressively outer rings.
const HUB_R = 150
const LEAF_BASE = 290
const DEPTH_STEP = 58
const BAND_GAP = 32

// Short labels for the hub ring (LINK_META labels are verbose for the map).
const FIELD_LABEL = {
  email: 'Email', mxcode: 'MX Code', phone: 'Phone', tid: 'TID',
  payable_code: 'Payable', account_number: 'Account', merchant_id: 'MID', other: 'Other',
}

// Confidence-band palette for leaves (mirrors the app's match tones).
const LEAF_TONE = {
  high: { fill: '#16a34a', text: '#ffffff' },   // match_pct >= 80
  med: { fill: '#d97706', text: '#ffffff' },    // match_pct 50-79
  low: { fill: '#64748b', text: '#ffffff' },    // match_pct < 50
  deep: { fill: '#e2e8f0', text: '#0f172a' },   // 2nd/3rd degree (no score)
}

const DEPTH_LABEL = ['Seed', '1st degree', '2nd degree', '3rd degree']

/* ── Data-quality risk signals ─────────────────────────────────────────
   Every leaf gets a severity level from record-level signals:
     - missing email
     - merchant name that differs a lot from the family's dominant name
     - identifiers duplicated across many family records
   Encoded as an amber (medium) / red (high) ring around the leaf, flags in
   the tooltip and a Data-quality block in the detail panel. */
const RISK_ID_FIELDS = ['email', 'phone', 'tid', 'mxcode', 'account_number', 'payable_code', 'merchant_id']

// Words that don't distinguish merchants (removed before name comparison).
const GENERIC_WORDS = new Set([
  'ltd', 'limited', 'plc', 'inc', 'llc', 'nig', 'nigeria', 'ng', 'co', 'company', 'companies',
  'services', 'service', 'enterprise', 'enterprises', 'ventures', 'venture', 'group', 'global',
  'international', 'intl', 'investment', 'investments', 'holding', 'holdings', 'and', '&', 'the',
  'of', 'for', 'store', 'stores', 'branch', 'restaurant', 'hotel', 'suite', 'suites',
])

function normTokens(s) {
  return String(s || '')
    .toLowerCase()
    .replace(/&/g, ' and ')
    .replace(/[^a-z0-9]+/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .filter((t) => !GENERIC_WORDS.has(t))
}

// Values that mean "no real email" in these files (N/A, EMAIL ALERTS…).
const EMAIL_NOISE = new Set(['n/a', 'na', '-', '--', 'nil', 'null', '0', '1', 'y', 'n', 'yes', 'no', 'email', 'alert', 'alerts', 'email alerts'])

// 0..1 similarity: token-set Jaccard blended with token-bigram Jaccard and
// character-bigram Jaccard (the last one catches compound-word variants like
// FILMHOUSE ≈ FILM HOUSE, BEACONHEALTH ≈ BEACON HEALTH, POWERFOIL ≈ POWER FOIL).
function nameSimilarity(a, b) {
  const ta = normTokens(a)
  const tb = normTokens(b)
  if (!ta.length || !tb.length) return 0
  const setA = new Set(ta)
  const setB = new Set(tb)
  let inter = 0
  for (const t of setA) if (setB.has(t)) inter++
  const union = setA.size + setB.size - inter
  const jac = union ? inter / union : 0
  const big = (arr) => {
    const s = new Set()
    for (let i = 0; i < arr.length - 1; i++) s.add(arr[i] + ' ' + arr[i + 1])
    return s
  }
  const ba = big(ta)
  const bb = big(tb)
  let bi = 0
  for (const x of ba) if (bb.has(x)) bi++
  const bu = ba.size + bb.size - bi
  const bjac = bu ? bi / bu : 0
  const charBig = (tokens) => {
    const set = new Set()
    const flat = tokens.join('') // normalized string, generics already stripped
    for (let i = 0; i < flat.length - 1; i++) set.add(flat.slice(i, i + 2))
    return set
  }
  const ca = charBig(ta)
  const cb = charBig(tb)
  let ci = 0
  for (const x of ca) if (cb.has(x)) ci++
  const cu = ca.size + cb.size - ci
  const cjac = cu ? ci / cu : 0
  return Math.max(jac, bjac * 0.85, cjac)
}

function computeRiskByLeaf(cluster, seedName) {
  // identifier → how many rows across the whole family share it
  const idCounts = {}
  for (const l of cluster.leaves) {
    const rec = l.record || {}
    for (const f of RISK_ID_FIELDS) {
      const v = String(rec[f] || '').trim().toLowerCase()
      if (!v || v === '—' || v === 'n/a') continue
      const key = `${f}::${v}`
      idCounts[key] = (idCounts[key] || 0) + 1
    }
  }
  // dominant merchant name (mode) — the family's expected name
  const nameCounts = {}
  for (const l of cluster.leaves) {
    const n = String(l.name || '').trim()
    if (n) nameCounts[n] = (nameCounts[n] || 0) + 1
  }
  const dominant = Object.entries(nameCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || seedName || ''

  const out = {}
  for (const l of cluster.leaves) {
    const rec = l.record || {}
    const flags = []
    let score = 0
    if (!rec.email || EMAIL_NOISE.has(String(rec.email).trim().toLowerCase())) {
      score += 2
      flags.push('No email')
    }
    const sim = nameSimilarity(l.name, dominant)
    if (dominant && sim < 0.35) {
      score += 2
      flags.push(`Name differs ${Math.round(sim * 100)}%`)
    }
    let maxShared = 1
    for (const f of RISK_ID_FIELDS) {
      const v = String(rec[f] || '').trim().toLowerCase()
      if (!v) continue
      const c = idCounts[`${f}::${v}`] || 1
      if (c > maxShared) maxShared = c
    }
    if (maxShared >= 5) {
      score += 2
      flags.push(`Duplicate id ×${maxShared}`)
    } else if (maxShared >= 3) {
      score += 1
      flags.push(`Shared id ×${maxShared}`)
    }
    out[l.id] = { score, level: score >= 4 ? 'high' : score >= 2 ? 'medium' : 'low', flags, maxShared }
  }
  return out
}

/* ── In-graph node search ────────────────────────────────────────────
   Jump to a specific record already in the loaded family by TID, MX code,
   email, phone, account or merchant name. Local search — no API round trip. */
const SEARCH_FIELD_LABEL = { tid: 'TID', mxcode: 'MX code', email: 'Email', phone: 'Phone', account_name: 'Account' }

function buildSearchIndex(leaves) {
  return leaves.map((l) => {
    const rec = l.record || {}
    return {
      id: l.id,
      name: l.name || '',
      field: l.field,
      depth: l.depth,
      matchPct: l.matchPct || 0,
      tid: String(rec.tid || '').trim(),
      mxcode: String(rec.mxcode || '').trim(),
      email: String(rec.email || '').trim(),
      phone: String(rec.phone || '').trim(),
      account_name: String(rec.account_name || '').trim(),
    }
  })
}

function searchNodes(q, index) {
  const query = String(q || '').trim().toLowerCase()
  if (!query) return []
  const scored = []
  for (const item of index) {
    let best = 0
    let matchLabel = ''
    for (const key of ['tid', 'mxcode', 'email', 'phone', 'account_name']) {
      const v = String(item[key] || '').toLowerCase()
      if (!v) continue
      if (v === query) {
        if (best < 3) {
          best = 3
          matchLabel = `${SEARCH_FIELD_LABEL[key]} exact`
        }
      } else if (v.includes(query) && best < 1) {
        best = 1
        if (!matchLabel) matchLabel = `${SEARCH_FIELD_LABEL[key]} …`
      }
    }
    const name = item.name.toLowerCase()
    if (name === query) {
      if (best < 2.5) {
        best = 2.5
        matchLabel = 'Exact name'
      }
    } else if (name.startsWith(query)) {
      if (best < 2) {
        best = 2
        matchLabel = 'Name starts with'
      }
    } else if (name.includes(query) && best < 1.5) {
      best = 1.5
      if (!matchLabel) matchLabel = 'Name contains'
    }
    if (best > 0) scored.push({ ...item, score: best, matchLabel })
  }
  return scored
    .sort((a, b) => b.score - a.score || b.matchPct - a.matchPct || a.name.localeCompare(b.name))
    .slice(0, 8)
}

/* ─────────────────────────────────────────────────────────────
   Data model: turn the API response into { center, hubs, leaves }
   ───────────────────────────────────────────────────────────── */
function parseReason(r) {
  const s = String(r || '')
  const i = s.indexOf('=')
  if (i <= 0) return null
  return { field: s.slice(0, i).trim(), value: s.slice(i + 1).trim() }
}

function primaryField(m) {
  const r = (m.link_reasons || []).map(parseReason).filter(Boolean)
  return r.length ? r[0].field : 'other'
}

function buildCluster(family, graph, depth, seedName) {
  const leaves = []
  if (depth <= 1) {
    // Depth 1 — the profile family: rich records with link_reasons + match_pct.
    for (const m of family?.members || []) {
      leaves.push({
        id: `m${m.id}`,
        name: m.merchant_name || 'Record',
        field: primaryField(m),
        depth: 1,
        matchPct: m.match_pct || 0,
        record: m,
      })
    }
  } else {
    // Depth 2/3 — the BFS graph. Each non-seed node hangs off the link field
    // of its incoming BFS edge (the identifier that connected it to the frontier).
    const inField = new Map()
    for (const e of graph?.edges || []) {
      if (!inField.has(e.target)) inField.set(e.target, e.field)
    }
    for (const n of graph?.nodes || []) {
      const d = n.depth ?? 0
      if (d === 0) continue // seeds ARE the center disc
      leaves.push({
        id: `n${n.id}`,
        name: n.name || 'Record',
        field: inField.get(n.id) || 'other',
        depth: d,
        matchPct: 0,
        record: n,
      })
    }
  }

  const counts = {}
  for (const l of leaves) counts[l.field] = (counts[l.field] || 0) + 1
  const fields = [
    ...LINK_FIELDS.filter((f) => counts[f]),
    ...(counts.other ? ['other'] : []),
  ]
  const hubs = fields.map((field) => ({ field, count: counts[field], meta: linkMeta(field) }))
  return { center: seedName || 'Merchant', hubs, leaves }
}

/* Deterministic radial-cluster layout → id → { x, y, angle, r } */
function computeClusterLayout(cluster) {
  const pos = {}
  pos.center = { x: CX, y: CY, angle: -Math.PI / 2 }
  const { hubs, leaves } = cluster
  const nHubs = hubs.length
  if (!nHubs) return pos

  const sector = (Math.PI * 2) / nHubs
  const startAngle = -Math.PI / 2
  const byHub = {}
  for (const l of leaves) (byHub[l.field] = byHub[l.field] || []).push(l)

  hubs.forEach((hub, i) => {
    const ang = startAngle + (i / nHubs) * Math.PI * 2
    const hx = CX + Math.cos(ang) * HUB_R
    const hy = CY + Math.sin(ang) * HUB_R
    const hr = 14 + Math.min(hub.count, 12) * 1.4 // hub size encodes child count
    pos[`hub-${hub.field}`] = { x: hx, y: hy, r: hr, angle: ang }

    const list = byHub[hub.field] || []
    const byDepth = {}
    for (const l of list) (byDepth[l.depth] = byDepth[l.depth] || []).push(l)
    for (const [dStr, tier] of Object.entries(byDepth)) {
      const depth = +dStr
      const n = tier.length
      // Leaves stay inside this hub's own angular wedge; the arc widens with
      // count but never crosses into a neighbouring hub.
      const arc = n <= 1 ? 0 : Math.min(sector * 0.74, 0.16 * n)
      const bands = Math.max(1, Math.ceil(n / 6)) // crowded hubs fan into bands
      const base = LEAF_BASE + (depth - 1) * DEPTH_STEP
      const bandGap = bands > 1 ? BAND_GAP / (bands - 1) : 0
      tier.forEach((l, j) => {
        const band = j % bands
        const t = n === 1 ? 0 : j / (n - 1) - 0.5
        const la = ang + t * arc
        const lr = base + band * bandGap
        pos[l.id] = { x: CX + Math.cos(la) * lr, y: CY + Math.sin(la) * lr, angle: la }
      })
    }
  })
  return pos
}

function leafTone(leaf) {
  if (leaf.depth > 1) return LEAF_TONE.deep
  if (leaf.matchPct >= 80) return LEAF_TONE.high
  if (leaf.matchPct >= 50) return LEAF_TONE.med
  return LEAF_TONE.low
}

function leafRadius(leaf) {
  if (leaf.depth === 1) return 8 + (leaf.matchPct / 100) * 8 // size encodes confidence
  return leaf.depth === 2 ? 7 : 6.5
}

/* ─────────────────────────────────────────────────────────────
   Small components
   ───────────────────────────────────────────────────────────── */
function LinkChip({ field, count }) {
  const meta = linkMeta(field)
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-plex text-[11px] font-semibold"
      style={{ borderColor: meta.stroke + '40', color: meta.stroke, background: meta.stroke + '0d' }}
    >
      <span className="msi text-[14px]">{meta.icon}</span>
      {FIELD_LABEL[field] || field}
      {count != null && <span className="opacity-70">· {count}</span>}
    </span>
  )
}

function Toast({ toast }) {
  if (!toast) return null
  const ok = toast.kind === 'ok'
  return (
    <div className="pointer-events-none fixed bottom-6 right-6 z-50">
      <div
        className={`animate-fade-in-up flex items-center gap-4 rounded-xl px-6 py-4 shadow-2xl ${
          ok ? 'bg-inverse-surface text-inverse-on-surface' : 'bg-error text-on-error'
        }`}
      >
        <span
          className={`msi text-[20px] ${ok ? 'text-secondary' : ''}`}
          style={ok ? undefined : { fontVariationSettings: "'FILL' 1" }}
        >
          {ok ? 'check_circle' : 'error'}
        </span>
        <div>
          <p className="text-sm font-bold">{toast.title}</p>
          {toast.detail && <p className="mt-0.5 text-xs opacity-80">{toast.detail}</p>}
        </div>
      </div>
    </div>
  )
}

function EmptyState({ icon, title, children }) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-outline-variant bg-surface-container-lowest px-8 py-16 text-center shadow-sm">
      <div className="mb-4 flex h-20 w-20 items-center justify-center rounded-full bg-surface-container-high text-outline-variant">
        <span className="msi text-[44px]">{icon}</span>
      </div>
      <h3 className="mb-1 text-lg font-semibold text-on-surface">{title}</h3>
      <p className="max-w-[420px] text-[13px] text-on-surface-variant">{children}</p>
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────
   Main page
   ───────────────────────────────────────────────────────────── */
export default function EntityGraphPage() {
  const [seed, setSeed] = useState('')
  const [depth, setDepth] = useState(2)
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [focusField, setFocusField] = useState(null) // hovered hub field (wedge focus)
  const [hubFilter, setHubFilter] = useState(null) // clicked hub → filters family table
  const [viewport, setViewport] = useState({ x: 0, y: 0, k: 1 })
  const [toast, setToast] = useState(null)
  const [teaching, setTeaching] = useState(null)
  const [nodeQuery, setNodeQuery] = useState('') // in-graph node search
  const [nodeActive, setNodeActive] = useState(0)
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchTargetId, setSearchTargetId] = useState(null) // pulsing highlight

  const svgRef = useRef(null)
  const graphRef = useRef(null)
  const panRef = useRef(null)
  const toastTimer = useRef(null)
  const nodeSearchRef = useRef(null)

  // Stable reference: `data?.family || {}` would mint a new {} each render
  // while data is null and churn the memo chain below (and any effect keyed on
  // it) into an infinite loop — memoize the fallback on [data].
  const family = useMemo(() => data?.family || {}, [data])
  const members = family.members || []
  const candidates = family.alias_candidates || []

  // The map model + geometry, rebuilt only when the data/depth changes.
  const cluster = useMemo(
    () => buildCluster(family, data?.graph, depth, seed.trim() || data?.seed || 'Merchant'),
    [family, data?.graph, depth, seed],
  )
  const { nodes, edges } = useMemo(() => {
    const ns = [{ id: 'center', isCenter: true, name: cluster.center }]
    for (const h of cluster.hubs) ns.push({ id: `hub-${h.field}`, isHub: true, field: h.field, count: h.count })
    for (const l of cluster.leaves) ns.push({ id: l.id, isLeaf: true, ...l })
    const es = []
    for (const h of cluster.hubs) {
      es.push({ source: 'center', target: `hub-${h.field}`, field: h.field, hub: true })
      for (const l of cluster.leaves) {
        if (l.field === h.field) es.push({ source: `hub-${h.field}`, target: l.id, field: h.field })
      }
    }
    return { nodes: ns, edges: es }
  }, [cluster])

  const layoutPositions = useMemo(() => computeClusterLayout(cluster), [cluster])

  // Data-quality risk per leaf (missing email / mismatched name / duplicated id).
  const riskByLeaf = useMemo(() => computeRiskByLeaf(cluster, data?.seed || ''), [cluster, data])

  // In-graph node search index + live matches.
  const searchIndex = useMemo(() => buildSearchIndex(cluster.leaves), [cluster])
  const nodeMatches = useMemo(() => searchNodes(nodeQuery, searchIndex), [nodeQuery, searchIndex])

  useEffect(() => {
    setNodeActive(0) // keep the dropdown cursor at the top on new queries
  }, [nodeQuery])

  /* Apply viewport transform on top of the static layout (memo so pan/zoom
     don't recompute the trig). */
  // The layout positions double as the render map directly — no separate state,
  // so there is nothing an effect could loop on.
  const posMap = useMemo(() => {
    const out = {}
    for (const [id, p] of Object.entries(layoutPositions)) out[id] = p
    return out
  }, [layoutPositions])

  const nodeById = useMemo(() => {
    const m = {}
    for (const n of nodes) m[n.id] = n
    return m
  }, [nodes])

  const selected = selectedId != null ? nodeById[selectedId] : null

  /* Auto-run from ?q= param (e.g. deep-linking from Search / Profile) */
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get('q')
    if (q && q.trim()) {
      setSeed(q.trim())
      runGraph(q.trim(), 2)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const showToast = useCallback((kind, title, detail) => {
    setToast({ kind, title, detail })
    window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToast(null), 4200)
  }, [])

  async function runGraph(query, d) {
    const q = (query ?? seed).trim()
    if (!q) return
    setLoading(true)
    setError(null)
    setHubFilter(null)
    setNodeQuery('')
    setNodeActive(0)
    setSearchOpen(false)
    setSearchTargetId(null)
    try {
      const res = await api.entity(q, d ?? depth, 160)
      setData(res)
      setSelectedId(null)
      setViewport({ x: 0, y: 0, k: 1 })
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setLoading(false)
    }
  }

  /* ── pan / zoom / drag (shared with the old depth-ring canvas) ── */
  const localPoint = useCallback((clientX, clientY) => {
    const g = graphRef.current
    if (!g || typeof g.getScreenCTM !== 'function') return { x: clientX, y: clientY }
    const ctm = g.getScreenCTM()
    if (!ctm) return { x: clientX, y: clientY }
    const p = new DOMPoint(clientX, clientY).matrixTransform(ctm.inverse())
    return { x: p.x, y: p.y }
  }, [])

  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const onWheel = (e) => {
      e.preventDefault()
      const p = localPoint(e.clientX, e.clientY)
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12
      setViewport((v) => {
        const k2 = Math.min(3, Math.max(0.35, v.k * factor))
        return { x: v.x + p.x * (v.k - k2), y: v.y + p.y * (v.k - k2), k: k2 }
      })
    }
    svg.addEventListener('wheel', onWheel, { passive: false })
    return () => svg.removeEventListener('wheel', onWheel)
  }, [localPoint])

  function onSvgPointerDown(e) {
    svgRef.current?.setPointerCapture(e.pointerId)
    const p = localPoint(e.clientX, e.clientY)
    panRef.current = { x: p.x, y: p.y }
    // clicking/dragging empty canvas dismisses the search pulse
    if (e.target === svgRef.current || e.target.tagName === 'rect') {
      setSearchTargetId(null)
    }
  }
  function onSvgPointerMove(e) {
    if (panRef.current) {
      const p = localPoint(e.clientX, e.clientY)
      setViewport((v) => ({
        ...v,
        x: v.x + (p.x - panRef.current.x) * v.k,
        y: v.y + (p.y - panRef.current.y) * v.k,
      }))
      panRef.current = p
    }
  }
  function onSvgPointerUp() {
    panRef.current = null
  }

  function zoomBy(factor) {
    const rect = svgRef.current?.getBoundingClientRect()
    const cx2 = rect ? rect.width / 2 : 0
    const cy2 = rect ? rect.height / 2 : 0
    const p = localPoint(rect ? rect.left + cx2 : 0, rect ? rect.top + cy2 : 0)
    setViewport((v) => {
      const k2 = Math.min(3, Math.max(0.35, v.k * factor))
      return { x: v.x + p.x * (v.k - k2), y: v.y + p.y * (v.k - k2), k: k2 }
    })
  }

  function fitView() {
    if (!nodes.length) return
    const ids = Object.keys(posMap)
    if (!ids.length) return
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
    for (const id of ids) {
      const p = posMap[id]
      if (!p) continue
      minX = Math.min(minX, p.x); minY = Math.min(minY, p.y)
      maxX = Math.max(maxX, p.x); maxY = Math.max(maxY, p.y)
    }
    const w = Math.max(maxX - minX, 100)
    const h = Math.max(maxY - minY, 100)
    const rect = svgRef.current?.getBoundingClientRect()
    const cw = rect ? rect.width : 800
    const ch = rect ? rect.height : 600
    const sctm = svgRef.current?.getScreenCTM?.()
    const S = sctm && sctm.a > 0 ? sctm.a : 1
    const k = Math.min(cw / (S * (w + 160)), ch / (S * (h + 160)), 1.4)
    setViewport({
      x: cw / (2 * S) - ((minX + maxX) / 2) * k,
      y: ch / (2 * S) - ((minY + maxY) / 2) * k,
      k,
    })
  }

  /* Pan/zoom the viewport so a node lands at the canvas centre. */
  function centerOn(id) {
    const p = posMap[id]
    if (!p) return
    const rect = svgRef.current?.getBoundingClientRect()
    const cw = rect ? rect.width : 800
    const ch = rect ? rect.height : 600
    const sctm = svgRef.current?.getScreenCTM?.()
    const S = sctm && sctm.a > 0 ? sctm.a : 1
    const k = Math.max(viewport.k, 1.5) // zoom in a touch to frame the record
    setViewport({ x: cw / (2 * S) - p.x * k, y: ch / (2 * S) - p.y * k, k })
  }

  function jumpToNode(id) {
    setSearchTargetId(id)
    setSelectedId(id)
    setFocusField(null) // bring the record to full opacity, no wedge focus
    setSearchOpen(false)
    centerOn(id)
  }

  async function teach(candidate) {
    setTeaching(candidate)
    try {
      const d = await api.learn(seed.trim(), candidate)
      showToast(
        'ok',
        d.learned ? 'Alias learned' : 'Already known',
        d.learned
          ? `${seed.trim().toUpperCase()} → ${candidate} — saved for next run`
          : `The mapping ${seed.trim().toUpperCase()} → ${candidate} was already taught.`,
      )
    } catch (e) {
      showToast('error', 'Teach failed', String(e.message || e))
    } finally {
      setTeaching(null)
    }
  }

  const sharedGroups = useMemo(() => {
    const order = ['email', 'mxcode', 'phone', 'tid', 'account_number', 'payable_code', 'merchant_id']
    const out = []
    for (const field of order) {
      const groups = family.shared?.[field]
      if (!groups) continue
      for (const [value, names] of Object.entries(groups)) {
        out.push({ field, value, names })
        if (out.filter((g) => g.field === field).length >= 2) break
      }
    }
    return out
  }, [family])

  // Edges touching the selected node, for its detail panel.
  const selectedEdges = useMemo(() => {
    if (!selected) return []
    if (selected.isHub) {
      return edges.filter((e) => e.target === selected.id)
    }
    if (selected.isLeaf) {
      const rec = selected.record || {}
      const fromReasons = (rec.link_reasons || []).map(parseReason).filter(Boolean).map((r) => ({ field: r.field, value: r.value }))
      const rawId = selected.id.startsWith('n') ? selected.id.slice(1) : selected.id
      const fromGraph = (data?.graph?.edges || []).filter((e) => String(e.source) === rawId || String(e.target) === rawId)
      return fromReasons.length ? fromReasons : fromGraph.map((e) => ({ field: e.field, value: e.value }))
    }
    return []
  }, [selected, edges, data])

  const memberField = (m) => primaryField(m)
  const filteredMembers = hubFilter ? members.filter((m) => memberField(m) === hubFilter) : members

  /* ── SVG builders ── */
  const NS = 'http://www.w3.org/2000/svg'
  const mk = (tag) => document.createElementNS(NS, tag)
  const disc = (x, y, r, fill, stroke, sw, cls) => {
    const c = mk('circle')
    c.setAttribute('cx', x); c.setAttribute('cy', y); c.setAttribute('r', r)
    c.setAttribute('fill', fill); c.setAttribute('stroke', stroke); c.setAttribute('stroke-width', sw)
    if (cls) c.setAttribute('class', cls)
    return c
  }
  const ring = (x, y, r, stroke, sw, op) => {
    const c = mk('circle')
    c.setAttribute('cx', x); c.setAttribute('cy', y); c.setAttribute('r', r)
    c.setAttribute('fill', 'none'); c.setAttribute('stroke', stroke)
    c.setAttribute('stroke-width', sw); c.setAttribute('opacity', op)
    return c
  }
  const curveEdge = (x1, y1, x2, y2, stroke, sw, op) => {
    const l = mk('path')
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2
    const dx = x2 - x1, dy = y2 - y1
    const qx = mx - dy * 0.12, qy = my + dx * 0.12
    l.setAttribute('d', `M${x1.toFixed(1)} ${y1.toFixed(1)} Q${qx.toFixed(1)} ${qy.toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`)
    l.setAttribute('stroke', stroke)
    l.setAttribute('stroke-width', sw)
    l.setAttribute('opacity', op)
    l.setAttribute('fill', 'none')
    l.setAttribute('stroke-linecap', 'round')
    return l
  }

  /* ── Render the map into the SVG (raw DOM inside the transformed <g>) ── */
  const renderMap = (selId) => {
    const frag = document.createDocumentFragment()

    // defs: centre gradient
    const defs = mk('defs')
    const grad = mk('radialGradient')
    grad.setAttribute('id', 'coreGrad')
    grad.setAttribute('cx', '38%'); grad.setAttribute('cy', '32%')
    ;[['0%', '#a98bff'], ['55%', '#7c5cff'], ['100%', '#4f6dff']].forEach(([o, c]) => {
      const st = mk('stop')
      st.setAttribute('offset', o); st.setAttribute('stop-color', c)
      grad.appendChild(st)
    })
    defs.appendChild(grad)
    frag.appendChild(defs)

    const focusDim = focusField != null

    // edges first (under the nodes)
    for (const e of edges) {
      const a = posMap[e.source]
      const b = posMap[e.target]
      if (!a || !b) continue
      const related = !focusDim || e.field === focusField
      const stroke = linkMeta(e.field).stroke
      const el = curveEdge(a.x, a.y, b.x, b.y, stroke, e.hub ? 1.8 : 1.1, related ? (focusDim ? 0.95 : 0.6) : 0.06)
      // Focus metadata so hover-dimming can update these in place (no rebuild)
      el.setAttribute('data-field', e.field)
      el.setAttribute('data-base', '0.6')
      el.setAttribute('data-rel', '0.95')
      el.setAttribute('data-dim', '0.06')
      frag.appendChild(el)
    }

    // center node
    const cp = posMap.center
    if (cp) {
      const g = mk('g')
      g.setAttribute('class', 'map-center')
      g.appendChild(disc(cp.x, cp.y, 34, 'var(--hub-fill, #1e293b)', 'var(--line-2, #475569)', 1.5))
      g.appendChild(disc(cp.x, cp.y, 30, 'url(#coreGrad)', 'none', 0))
      const t = mk('text')
      t.setAttribute('x', cp.x); t.setAttribute('y', cp.y + 62)
      t.setAttribute('text-anchor', 'middle'); t.setAttribute('class', 'map-lbl map-lbl-center')
      t.textContent = truncate(cluster.center, 26)
      g.appendChild(t)
      const title = mk('title')
      title.textContent = cluster.center
      g.appendChild(title)
      frag.appendChild(g)
    }

    // hubs + leaves
    for (const h of cluster.hubs) {
      const hp = posMap[`hub-${h.field}`]
      if (!hp) continue
      const related = !focusDim || h.field === focusField
      const g = mk('g')
      g.setAttribute('class', 'map-hub')
      g.setAttribute('data-field', h.field)
      g.setAttribute('data-base', '1')
      g.setAttribute('data-rel', '1')
      g.setAttribute('data-dim', '0.18')
      g.setAttribute('opacity', related ? 1 : 0.18)
      g.appendChild(ring(hp.x, hp.y, hp.r + 6, h.meta.stroke, 1.3, 0.5))
      g.appendChild(disc(hp.x, hp.y, hp.r, h.meta.stroke, '#0f172a', 1.2))
      const ct = mk('text')
      ct.setAttribute('x', hp.x); ct.setAttribute('y', hp.y + 5)
      ct.setAttribute('text-anchor', 'middle'); ct.setAttribute('class', 'map-count')
      ct.setAttribute('fill', '#ffffff')
      ct.textContent = String(h.count)
      g.appendChild(ct)
      // hub label sits just outside the hub ring
      const lx = CX + Math.cos(hp.angle) * (HUB_R + hp.r + 16)
      const ly = CY + Math.sin(hp.angle) * (HUB_R + hp.r + 16)
      const hl = mk('text')
      hl.setAttribute('x', lx); hl.setAttribute('y', ly + 4)
      hl.setAttribute('class', 'map-lbl')
      hl.setAttribute('text-anchor', Math.cos(hp.angle) < -0.25 ? 'end' : Math.cos(hp.angle) > 0.25 ? 'start' : 'middle')
      hl.textContent = FIELD_LABEL[h.field] || h.field
      g.appendChild(hl)
      frag.appendChild(g)

      for (const l of cluster.leaves) {
        if (l.field !== h.field) continue
        const lp = posMap[l.id]
        if (!lp) continue
        const rel = !focusDim || l.field === focusField
        const tone = leafTone(l)
        const r = leafRadius(l)
        const risk = riskByLeaf?.[l.id]
        const lg = mk('g')
        lg.setAttribute('class', 'map-leaf')
        lg.setAttribute('data-field', l.field)
        lg.setAttribute('data-id', l.id)
        lg.setAttribute('data-base', '1')
        lg.setAttribute('data-rel', '1')
        lg.setAttribute('data-dim', '0.12')
        lg.setAttribute('opacity', rel ? 1 : 0.12)
        // generous transparent hit target
        lg.appendChild(disc(lp.x, lp.y, Math.max(r + 9, 17), 'transparent', 'none', 0))
        if (l.matchPct >= 80) {
          lg.appendChild(ring(lp.x, lp.y, r + 5, tone.fill, 2, 0.9)) // glow ring (high match)
        }
        // risk ring takes precedence over the depth ring — both sit at r+3
        if (risk?.level === 'high') {
          lg.appendChild(ring(lp.x, lp.y, r + 3, '#dc2626', 2.6, 1))
        } else if (risk?.level === 'medium') {
          lg.appendChild(ring(lp.x, lp.y, r + 3, '#f59e0b', 2.2, 1))
        } else if (l.depth > 1) {
          lg.appendChild(ring(lp.x, lp.y, r + 3, '#94a3b8', 1, 0.8))
        }
        if (selId === l.id) {
          lg.appendChild(ring(lp.x, lp.y, r + 7, 'var(--color-primary, #0053db)', 2.2, 1)) // selection ring
        }
        // search-target pulse — expanding, fading ring (SMIL, self-contained)
        if (searchTargetId === l.id) {
          const pulse = mk('circle')
          pulse.setAttribute('cx', lp.x); pulse.setAttribute('cy', lp.y)
          pulse.setAttribute('r', String(r + 8)); pulse.setAttribute('fill', 'none')
          pulse.setAttribute('stroke', '#0053db'); pulse.setAttribute('stroke-width', '2.5')
          const ra = mk('animate')
          ra.setAttribute('attributeName', 'r')
          ra.setAttribute('from', String(r + 8)); ra.setAttribute('to', String(r + 24))
          ra.setAttribute('dur', '1.1s'); ra.setAttribute('repeatCount', 'indefinite')
          pulse.appendChild(ra)
          const oa = mk('animate')
          oa.setAttribute('attributeName', 'opacity')
          oa.setAttribute('from', '0.9'); oa.setAttribute('to', '0')
          oa.setAttribute('dur', '1.1s'); oa.setAttribute('repeatCount', 'indefinite')
          pulse.appendChild(oa)
          lg.appendChild(pulse)
        }
        lg.appendChild(disc(lp.x, lp.y, r, tone.fill, l.depth > 1 ? '#94a3b8' : 'transparent', l.depth > 1 ? 1 : 0))
        const it = mk('text')
        it.setAttribute('x', lp.x); it.setAttribute('y', lp.y + 4)
        it.setAttribute('text-anchor', 'middle'); it.setAttribute('class', 'map-initials')
        it.setAttribute('fill', tone.text)
        it.textContent = initials(l.name)
        lg.appendChild(it)
        // label when the hub isn't too crowded
        if (h.count <= 8) {
          const showLeft = Math.cos(lp.angle) < 0
          const tl = mk('text')
          tl.setAttribute('x', lp.x + (showLeft ? -(r + 7) : r + 7))
          tl.setAttribute('y', lp.y + 4)
          tl.setAttribute('class', 'map-lbl')
          tl.setAttribute('text-anchor', showLeft ? 'end' : 'start')
          tl.textContent = truncate(l.name, 20)
          lg.appendChild(tl)
        }
        const title = mk('title')
        const riskSuffix = risk?.flags?.length ? ` · ⚠ ${risk.flags.join(' · ')}` : ''
        title.textContent = `${l.name}${l.depth > 1 ? ` · ${l.depth} hops` : l.matchPct ? ` · ${l.matchPct}% match` : ''}${riskSuffix}`
        lg.appendChild(title)
        frag.appendChild(lg)
      }
    }
    return frag
  }

  return (
    <div className="space-y-5">
      {/* Header + depth control */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[26px] font-extrabold tracking-tight text-on-surface">Entity Graph</h1>
          <p className="mt-1 text-sm text-on-surface-variant">
            Merchant families clustered by the identifier that links them — emails, phones, MX codes &amp; TIDs.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="font-plex text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">Depth</span>
          <div className="flex overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest">
            {[1, 2, 3].map((d) => (
              <button
                key={d}
                onClick={() => {
                  setDepth(d)
                  runGraph(seed, d)
                }}
                className={`px-3.5 py-1.5 font-plex text-[12px] font-bold transition-colors ${
                  depth === d ? 'bg-primary text-on-primary' : 'text-on-surface-variant hover:bg-surface-container'
                }`}
              >
                {d}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Search row */}
      <div className="flex gap-2.5">
        <div className="relative flex-1">
          <span className="pointer-events-none absolute inset-y-0 left-4 flex items-center text-outline">
            <span className="msi text-[22px]">hub</span>
          </span>
          <input
            value={seed}
            onChange={(e) => setSeed(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && runGraph(seed, depth)}
            placeholder="Seed merchant — try LAGOON WATERS or MONEYTRUST MICROFINANCE"
            className="w-full rounded-xl border border-outline-variant bg-surface-container-lowest py-3.5 pl-12 pr-4 text-sm shadow-sm outline-none transition-all focus:border-primary focus:ring-4 focus:ring-primary-container"
          />
        </div>
        <button
          onClick={() => runGraph(seed, depth)}
          disabled={loading || !seed.trim()}
          className="flex items-center gap-2 rounded-xl bg-primary px-5 py-3.5 font-plex text-[13px] font-bold text-on-primary shadow-sm transition-all hover:opacity-90 active:scale-95 disabled:opacity-50"
        >
          <span className="msi text-[20px]">account_tree</span>
          Map Family
        </button>
      </div>

      {/* Examples */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-plex text-[11px] font-semibold uppercase tracking-wider text-outline">Try:</span>
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            onClick={() => {
              setSeed(ex)
              runGraph(ex, depth)
            }}
            className="rounded-full border border-outline-variant bg-surface-container-lowest px-3 py-1 font-plex text-[11px] font-semibold text-on-surface-variant transition-colors hover:border-primary hover:text-primary"
          >
            {ex}
          </button>
        ))}
      </div>

      {error && (
        <div className="rounded-xl border border-error/30 bg-error-container/40 px-4 py-3 text-sm font-medium text-on-error-container">
          {error}
        </div>
      )}

      {!loading && !data && !error && (
        <EmptyState icon="hub" title="Entity graph">
          Type a seed merchant to map its family — records connected by shared email, phone, MX code, TID or
          account number, clustered by the link type. Depth controls how many hops the BFS expands.
        </EmptyState>
      )}

      {loading && (
        <div className="flex flex-col items-center rounded-xl border border-outline-variant bg-surface-container-lowest px-8 py-20 shadow-sm">
          <div className="mb-4 h-10 w-10 animate-spin rounded-full border-[3px] border-primary border-t-transparent" />
          <p className="text-sm font-medium text-on-surface-variant">
            Tracing relationships for <b className="text-on-surface">{seed || '…'}</b>…
          </p>
        </div>
      )}

      {data && !loading && nodes.length <= 1 && (
        <EmptyState icon="hub_off" title="No linked records">
          No family members found for <b>{data.seed}</b>. Try an alias, a TID, or a different spelling.
        </EmptyState>
      )}

      {data && !loading && nodes.length > 1 && (
        <>
          {/* Stats + legend */}
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border border-outline-variant bg-surface-container px-5 py-3.5 text-[13px] text-on-surface-variant">
            <span>
              <b className="text-on-surface">{cluster.leaves.length}</b> records
            </span>
            <span>
              <b className="text-on-surface">{cluster.hubs.length}</b> link types
            </span>
            {members[0]?.email && (
              <span className="truncate">
                Seed email: <b className="text-on-surface">{members[0].email}</b>
              </span>
            )}
            <span className="ml-auto flex flex-wrap items-center gap-3 font-plex text-[11px]">
              <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-[#16a34a]" />≥80%</span>
              <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-[#d97706]" />50–79%</span>
              <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-[#64748b]" />&lt;50%</span>
              <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-[#e2e8f0] ring-1 ring-slate-400" />2+ hops</span>
              <span className="w-px self-stretch bg-outline-variant/70" />
              <span className="flex items-center gap-1.5" title="Missing email, name mismatch or duplicated identifiers">
                <span className="h-2.5 w-2.5 rounded-full bg-[#cbd5e1] ring-2 ring-red-500" />High risk
              </span>
              <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-[#cbd5e1] ring-2 ring-amber-400" />Med risk</span>
              <span className="text-outline">{data.elapsed_ms}ms</span>
            </span>
          </div>

          {/* Map + detail panel */}
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
            <div className="relative h-[600px] overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
              <svg
                ref={svgRef}
                viewBox={`0 0 ${VW} ${VH}`}
                className="h-full w-full cursor-grab touch-none select-none active:cursor-grabbing"
                onPointerDown={onSvgPointerDown}
                onPointerMove={onSvgPointerMove}
                onPointerUp={onSvgPointerUp}
              >
                <rect x={0} y={0} width={VW} height={VH} fill="transparent" />
                {/* raw-DOM map fragments are appended imperatively into this
                    transformed group by <MapLayer> after each data change */}
                <g ref={graphRef} transform={`translate(${viewport.x} ${viewport.y}) scale(${viewport.k})`} />
              </svg>

              {/* imperative render hook */}
              <MapLayer
                render={() => renderMap(selectedId)}
                graphRef={graphRef}
                positionsReady={!!posMap.center}
                cluster={cluster}
                posMap={posMap}
                selectedId={selectedId}
                searchTargetId={searchTargetId}
                focusField={focusField}
                onHoverField={setFocusField}
                onSelect={(id) => {
                  setSelectedId(id)
                  setSearchTargetId(null) // a manual click replaces the search pulse
                }}
                onHubClick={(field) => setHubFilter((cur) => (cur === field ? null : field))}
              />

              {/* Zoom controls */}
              <div className="absolute bottom-4 right-4 flex flex-col overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest shadow-sm">
                <button onClick={() => zoomBy(1.25)} className="px-3 py-1.5 text-on-surface-variant transition-colors hover:bg-surface-container" title="Zoom in">
                  <span className="msi text-[18px]">add</span>
                </button>
                <button onClick={() => zoomBy(1 / 1.25)} className="border-t border-outline-variant px-3 py-1.5 text-on-surface-variant transition-colors hover:bg-surface-container" title="Zoom out">
                  <span className="msi text-[18px]">remove</span>
                </button>
                <button onClick={fitView} className="border-t border-outline-variant px-3 py-1.5 text-on-surface-variant transition-colors hover:bg-surface-container" title="Fit view">
                  <span className="msi text-[18px]">fit_screen</span>
                </button>
              </div>

              {/* Legend */}
              <div className="absolute left-4 top-4 flex max-w-[300px] flex-wrap gap-1.5 rounded-lg border border-outline-variant bg-surface-container-lowest/95 p-2 shadow-sm backdrop-blur">
                {cluster.hubs.map((h) => (
                  <LinkChip key={h.field} field={h.field} count={h.count} />
                ))}
                {cluster.hubs.length === 0 && <span className="font-plex text-[10px] text-outline">No link types</span>}
              </div>

              {/* In-graph node search — find & jump to a record by TID/MX/name */}
              <div className="absolute right-4 top-4 z-20 w-64">
                <div className="relative">
                  <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-outline">
                    <span className="msi text-[18px]">manage_search</span>
                  </span>
                  <input
                    ref={nodeSearchRef}
                    value={nodeQuery}
                    aria-label="Find record in graph"
                    onChange={(e) => {
                      setNodeQuery(e.target.value)
                      setSearchOpen(true)
                    }}
                    onFocus={() => setSearchOpen(true)}
                    onBlur={() => window.setTimeout(() => setSearchOpen(false), 140)}
                    onKeyDown={(e) => {
                      if (e.key === 'ArrowDown') {
                        e.preventDefault()
                        setNodeActive((i) => Math.min(i + 1, Math.max(nodeMatches.length - 1, 0)))
                      } else if (e.key === 'ArrowUp') {
                        e.preventDefault()
                        setNodeActive((i) => Math.max(i - 1, 0))
                      } else if (e.key === 'Enter') {
                        if (nodeMatches[nodeActive]) jumpToNode(nodeMatches[nodeActive].id)
                      } else if (e.key === 'Escape') {
                        setSearchOpen(false)
                        setNodeQuery('')
                        setSearchTargetId(null)
                      }
                    }}
                    placeholder="Find record — TID, MX, name…"
                    className="w-full rounded-lg border border-outline-variant bg-surface-container-lowest/95 py-2 pl-9 pr-8 text-[12px] shadow-sm outline-none backdrop-blur transition-all focus:border-primary focus:ring-2 focus:ring-primary-container"
                  />
                  {nodeQuery.trim() && (
                    <button
                      onClick={() => {
                        setNodeQuery('')
                        setSearchTargetId(null)
                        nodeSearchRef.current?.focus()
                      }}
                      className="absolute inset-y-0 right-1.5 flex items-center px-1 text-outline hover:text-on-surface"
                      title="Clear search"
                    >
                      <span className="msi text-[15px]">close</span>
                    </button>
                  )}
                </div>
                {searchOpen && nodeQuery.trim() && (
                  <div className="absolute top-full mt-1.5 w-full overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest shadow-xl">
                    {nodeMatches.length === 0 ? (
                      <div className="px-3 py-2.5 font-plex text-[11px] text-outline">
                        No matches in this family — try a different seed
                      </div>
                    ) : (
                      <ul className="max-h-64 overflow-y-auto py-1">
                        {nodeMatches.map((m, i) => (
                          <li key={m.id}>
                            <button
                              onMouseDown={(e) => e.preventDefault()}
                              onClick={() => jumpToNode(m.id)}
                              onMouseEnter={() => setNodeActive(i)}
                              className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left transition-colors ${
                                i === nodeActive ? 'bg-surface-container' : 'hover:bg-surface-container-low'
                              }`}
                            >
                              <div className="min-w-0">
                                <div className="truncate text-[12px] font-semibold text-on-surface">{m.name}</div>
                                <div className="flex items-center gap-1.5 font-plex text-[10px] text-outline">
                                  <span className="truncate">{m.matchLabel}</span>
                                  <span className="shrink-0 rounded bg-surface-container-high px-1 py-px font-bold text-on-surface-variant">
                                    {m.depth > 1 ? `${m.depth} hops` : 'direct'}
                                  </span>
                                </div>
                              </div>
                              <span className="msi shrink-0 text-[16px] text-primary">east</span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </div>

              {/* Hint */}
              <div className="pointer-events-none absolute bottom-4 left-4 rounded-lg bg-surface-container-lowest/90 px-3 py-1.5 font-plex text-[11px] text-outline shadow-sm backdrop-blur">
                Hover to focus a link type · click a record for details · click a hub to filter the table
              </div>
            </div>

            {/* Right panel */}
            <div className="flex max-h-[600px] flex-col overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
              {selected ? (
                <NodeDetail
                  node={selected}
                  edges={selectedEdges}
                  depthLabel={DEPTH_LABEL[Math.min(selected.depth ?? 0, 3)]}
                  risk={riskByLeaf?.[selected.id]}
                />
              ) : (
                <SharedIdentifiers groups={sharedGroups} />
              )}
            </div>
          </div>

          {/* Alias candidates */}
          {candidates.length > 0 && (
            <div className="rounded-xl border border-outline-variant bg-surface-container-lowest p-5 shadow-sm">
              <div className="mb-3 flex items-center gap-2">
                <span className="msi fill text-[20px] text-tertiary">lightbulb</span>
                <h3 className="text-sm font-bold text-on-surface">Alias candidates</h3>
                <span className="font-plex text-[11px] text-outline">
                  — family members that share identifiers with your seed. Teach the engine to remember them.
                </span>
              </div>
              <div className="flex flex-wrap gap-2">
                {candidates.slice(0, 8).map((cand) => (
                  <button
                    key={cand}
                    onClick={() => teach(cand)}
                    disabled={teaching === cand}
                    className="group flex items-center gap-2 rounded-full border border-primary/25 bg-primary/5 px-3.5 py-1.5 font-plex text-[12px] font-semibold text-primary transition-all hover:bg-primary/10 active:scale-95 disabled:opacity-50"
                  >
                    {teaching === cand ? (
                      <span className="h-3 w-3 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                    ) : (
                      <span className="msi text-[15px]">school</span>
                    )}
                    {cand}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Family table (filterable by clicking a hub) */}
          <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-outline-variant bg-surface-container-low px-5 py-3.5">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-bold text-on-surface">Family members</h3>
                <span className="rounded-md bg-primary/10 px-2 py-0.5 font-plex text-[10px] font-bold text-primary">
                  {filteredMembers.length} LINKED
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                {cluster.hubs.map((h) => (
                  <button
                    key={h.field}
                    onClick={() => setHubFilter((cur) => (cur === h.field ? null : h.field))}
                    title={`Filter to ${FIELD_LABEL[h.field] || h.field} links`}
                    className={`flex items-center gap-1 rounded-full border px-2 py-0.5 font-plex text-[10px] font-bold transition-colors ${
                      hubFilter === h.field
                        ? 'border-primary bg-primary text-on-primary'
                        : 'border-outline-variant bg-surface-container-lowest text-on-surface-variant hover:border-primary/50'
                    }`}
                  >
                    <span className="msi text-[12px]">{h.meta.icon}</span>
                    {FIELD_LABEL[h.field] || h.field}
                    <span className="opacity-70">{h.count}</span>
                  </button>
                ))}
                {hubFilter && (
                  <button
                    onClick={() => setHubFilter(null)}
                    className="flex items-center gap-1 rounded-full border border-outline-variant px-2 py-0.5 font-plex text-[10px] font-bold text-outline hover:text-on-surface"
                  >
                    <span className="msi text-[12px]">close</span>
                    Clear
                  </button>
                )}
              </div>
            </div>
            <div className="max-h-[420px] overflow-y-auto">
              <table className="w-full text-left">
                <thead className="sticky top-0 z-10 bg-surface-container">
                  <tr className="font-plex text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">
                    <th className="px-5 py-2.5">Merchant</th>
                    <th className="px-5 py-2.5">Match</th>
                    <th className="px-5 py-2.5">TID</th>
                    <th className="px-5 py-2.5">MX Code</th>
                    <th className="px-5 py-2.5">Email</th>
                    <th className="px-5 py-2.5">Linked by</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredMembers
                    .slice()
                    .sort((a, b) => (b.match_pct || 0) - (a.match_pct || 0))
                    .map((m) => (
                      <tr key={m.id} className="border-b border-outline-variant/60 transition-colors last:border-0 hover:bg-surface-container-low/60">
                        <td className="px-5 py-3">
                          <div className="font-semibold text-on-surface">{m.merchant_name || '—'}</div>
                          <div className="font-plex text-[11px] text-outline">{m.sheet_name || 'Parameter file'}</div>
                        </td>
                        <td className="px-5 py-3">
                          <span
                            className={`inline-flex items-center rounded-lg px-2.5 py-1 font-plex text-[11px] font-bold ${
                              (m.match_pct || 0) >= 80
                                ? 'bg-green-100 text-green-800'
                                : 'bg-surface-container-high text-on-surface-variant'
                            }`}
                          >
                            {m.match_pct || 0}%
                          </span>
                        </td>
                        <td className="px-5 py-3 font-plex text-[12px] text-on-surface-variant">{m.tid || '—'}</td>
                        <td className="px-5 py-3 font-plex text-[12px] text-on-surface-variant">{m.mxcode || '—'}</td>
                        <td className="px-5 py-3 text-[12px] text-on-surface-variant">{m.email || '—'}</td>
                        <td className="px-5 py-3">
                          <div className="flex max-w-[280px] flex-wrap gap-1">
                            {(m.link_reasons || []).slice(0, 3).map((r, i) => {
                              const [field] = String(r).split('=')
                              const meta = linkMeta(field)
                              return (
                                <span
                                  key={i}
                                  className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-plex text-[10px] font-semibold"
                                  style={{ color: meta.stroke, background: meta.stroke + '0d' }}
                                >
                                  <span className="msi text-[12px]">{meta.icon}</span>
                                  {String(r).replace('=', ': ')}
                                </span>
                              )
                            })}
                          </div>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      <Toast toast={toast} />
    </div>
  )
}

/* Imperatively append the raw-SVG map into the transformed <g> and wire
   hover/click behaviour.

   The map is rebuilt ONLY when geometry or the selection changes (cluster /
   posMap / selectedId are memoised refs, so they're stable while panning,
   zooming or hovering). Hover-focus dimming is applied by a separate effect
   that updates opacity attributes in place — never a DOM rebuild — so a
   160-node map stays smooth at 60fps while dragging. */
function MapLayer({ render, graphRef, positionsReady, cluster, posMap, selectedId, searchTargetId, focusField, onHoverField, onSelect, onHubClick }) {
  // Build/rebuild the static map when geometry or the selection changes.
  useEffect(() => {
    const g = graphRef.current
    if (!g || !positionsReady) return
    g.innerHTML = ''
    g.appendChild(render())

    g.querySelectorAll('.map-hub').forEach((el) => {
      el.addEventListener('mouseenter', () => onHoverField(el.dataset.field))
      el.addEventListener('mouseleave', () => onHoverField(null))
      el.addEventListener('click', (e) => {
        e.stopPropagation()
        onHubClick(el.dataset.field)
      })
    })
    g.querySelectorAll('.map-leaf').forEach((el) => {
      el.addEventListener('mouseenter', () => onHoverField(el.dataset.field))
      el.addEventListener('mouseleave', () => onHoverField(null))
      el.addEventListener('click', (e) => {
        e.stopPropagation()
        onSelect(el.dataset.id)
      })
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [positionsReady, cluster, posMap, selectedId, searchTargetId])

  // Hover-focus dimming — in-place opacity attribute updates only.
  // After any rebuild (cluster/posMap/selectedId change) this re-applies the
  // current focus state so the baked opacity never goes stale.
  useEffect(() => {
    const g = graphRef.current
    if (!g || !positionsReady) return
    const dim = focusField != null
    g.querySelectorAll('[data-dim]').forEach((el) => {
      const related = !dim || el.dataset.field === focusField
      el.setAttribute('opacity', dim ? (related ? el.dataset.rel : el.dataset.dim) : el.dataset.base)
    })
  }, [focusField, positionsReady, cluster, posMap, selectedId])
  return null
}

/* ── Right panel: selected node detail ─────────────────────────── */
function NodeDetail({ node, edges, depthLabel, risk }) {
  const rec = node.record || {}
  const name = node.name || rec.merchant_name || 'Record'
  // Only surface identifiers that LOOK like real ones — dirty columns leak
  // garbage (tid=507 is terminal owner code, tid=POS is terminal type) that
  // would otherwise display as if they were the record's real TID.
  const tidy = (field, v) => (plausibleId(field, v) ? v : '')
  const rows = [
    ['Sheet', rec.sheet_name || rec.sheet],
    ['TID', tidy('tid', rec.tid)],
    ['MX Code', tidy('mxcode', rec.mxcode)],
    ['Email', rec.email],
    ['Phone', rec.phone],
    ['Account', rec.account_name],
    ['Bank', bankName(rec.bank)],
  ]
  return (
    <div className="flex flex-col">
      <div className="border-b border-outline-variant bg-surface-container-low px-5 py-4">
        <div className="flex items-center gap-2">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary text-on-primary">
            <span className="msi text-[18px]">business</span>
          </span>
          <div className="min-w-0">
            <h3 className="truncate text-sm font-bold text-on-surface">{name}</h3>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <span className="rounded-full bg-primary/10 px-2 py-0.5 font-plex text-[10px] font-bold text-primary">
                {node.isHub ? `${depthLabel} hub` : depthLabel}
              </span>
              {risk && risk.level !== 'low' && (
                <span
                  className={`rounded-full px-2 py-0.5 font-plex text-[10px] font-bold ${
                    risk.level === 'high' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-800'
                  }`}
                >
                  {risk.level === 'high' ? 'High risk' : 'Medium risk'}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>
      <div className="flex-1 space-y-2.5 overflow-y-auto px-5 py-4">
        {rows.map(([label, value]) => (
          <div key={label}>
            <div className="font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">{label}</div>
            <div className="truncate text-[13px] font-medium text-on-surface">{value || '—'}</div>
          </div>
        ))}
        {risk && risk.flags.length > 0 && (
          <div className="pt-1">
            <div className="mb-2 font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">
              Data quality
            </div>
            <div className="flex flex-wrap gap-1.5">
              {risk.flags.map((f, i) => (
                <span
                  key={i}
                  className={`rounded-full px-2 py-1 font-plex text-[10px] font-bold ${
                    risk.level === 'high' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-800'
                  }`}
                >
                  {f}
                </span>
              ))}
            </div>
          </div>
        )}
        {edges.length > 0 && (
          <div className="pt-2">
            <div className="mb-2 font-plex text-[10px] font-semibold uppercase tracking-wider text-outline">
              Connections ({edges.length})
            </div>
            <div className="space-y-1.5">
              {edges.map((e, i) => {
                const meta = linkMeta(e.field)
                return (
                  <div
                    key={i}
                    className="flex items-center gap-2 rounded-lg border border-outline-variant/60 bg-surface-container-low px-2.5 py-1.5"
                  >
                    <span className="msi text-[16px]" style={{ color: meta.stroke }}>
                      {meta.icon}
                    </span>
                    <div className="min-w-0">
                      <div className="font-plex text-[10px] font-bold" style={{ color: meta.stroke }}>
                        {meta.label}
                      </div>
                      <div className="truncate text-[11px] text-on-surface-variant">{e.value}</div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/* ── Right panel: shared identifiers overview (no selection) ────── */
function SharedIdentifiers({ groups }) {
  return (
    <div className="flex flex-col">
      <div className="border-b border-outline-variant bg-surface-container-low px-5 py-4">
        <h3 className="text-sm font-bold text-on-surface">Shared identifiers</h3>
        <p className="mt-0.5 text-[11px] text-on-surface-variant">
          How family members are connected — click a node to inspect its links.
        </p>
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4">
        {groups.length === 0 && (
          <p className="text-[12px] text-on-surface-variant">No shared identifiers in this family.</p>
        )}
        {groups.map((g, i) => {
          const meta = linkMeta(g.field)
          return (
            <div key={i} className="rounded-lg border border-outline-variant/60 bg-surface-container-low p-3">
              <div className="mb-1.5 flex items-center justify-between">
                <span className="flex items-center gap-1.5 font-plex text-[10px] font-bold uppercase tracking-wider" style={{ color: meta.stroke }}>
                  <span className="msi text-[14px]">{meta.icon}</span>
                  {meta.label}
                </span>
                <span className="rounded-full bg-surface-container-high px-2 py-0.5 font-plex text-[10px] font-bold text-on-surface-variant">
                  {g.names.length} records
                </span>
              </div>
              <div className="break-all font-plex text-[12px] font-semibold text-on-surface">{g.value}</div>
              <div className="mt-1.5 truncate text-[11px] text-on-surface-variant">
                {g.names.slice(0, 3).join(' · ')}
                {g.names.length > 3 ? ` +${g.names.length - 3} more` : ''}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
