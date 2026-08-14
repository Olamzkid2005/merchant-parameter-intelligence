import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { LINK_META, LINK_FIELDS, linkMeta, initials, truncate } from '../utils/links'

/* ─────────────────────────────────────────────────────────────
   RelationshipNetwork — a small, self-contained SVG mini-graph
   showing how the profile's linked records connect through shared
   identifiers. The seed record sits in the centre; family members
   are grouped into coloured sectors by the identifier type that
   links them (email / phone / TID / MX code / …).

   Interactivity:
     · hover a node → its edges + neighbours highlight
     · click a node → pin it and show a detail card

   Depth control:
     · depth 1 — the profile's direct family (members prop), instant
     · depth 2/3 — fetches the backend BFS entity graph (api.entity)
       and renders members 2+ hops out in concentric outer rings
   ───────────────────────────────────────────────────────────── */

const VW = 640 // virtual canvas
const VH = 460
const CX = VW / 2
const CY = VH / 2
const MAX_NODES = 40 // keep the visual "small"
const MAX_EDGES = 160

// ── identifier normalisation (mirrors the backend's link fields) ──
function norm(v) {
  return String(v || '')
    .trim()
    .toLowerCase()
    .replace(/[\s\-_.]+/g, '')
}

function parseReason(r) {
  const s = String(r || '')
  const i = s.indexOf('=')
  if (i <= 0) return null
  return { field: s.slice(0, i).trim(), value: s.slice(i + 1).trim() }
}

/* Build { nodes, edges } from the seed + members (depth 1).
   Edges are seed→member per link_reason, plus member→member links
   between records that share the same identifier value. */
function buildGraph(seed, members) {
  const nodes = []
  const nodeIndex = new Map()
  const edges = []

  const seedNode = {
    id: 'seed',
    name: seed?.merchant_name || 'Seed',
    isSeed: true,
    depth: 0,
    record: seed || {},
  }
  nodes.push(seedNode)
  nodeIndex.set('seed', seedNode)

  const membersList = (members || []).slice(0, MAX_NODES)
  for (const m of membersList) {
    const id = String(m.id ?? `m${nodes.length}`)
    if (nodeIndex.has(id)) continue
    const node = { id, name: m.merchant_name || 'Record', isSeed: false, depth: 1, record: m }
    nodes.push(node)
    nodeIndex.set(id, node)
  }

  const seenEdges = new Set()
  const addEdge = (source, target, field, value) => {
    const key = `${source}→${target}::${field}`
    if (seenEdges.has(key)) return
    seenEdges.add(key)
    edges.push({ source, target, field, value })
  }

  // 1) seed → member edges from link_reasons (authoritative from backend)
  for (const n of nodes) {
    if (n.isSeed) continue
    const reasons = (n.record.link_reasons || []).map(parseReason).filter(Boolean)
    for (const r of reasons) addEdge('seed', n.id, r.field, r.value)
  }

  // 2) member → member edges through shared identifier values
  const byValue = new Map() // field:value -> [nodeId]
  const rawValueOf = (nodeId, field) => {
    const node = nodeIndex.get(nodeId)
    if (!node) return ''
    const rec = node.record || {}
    const raw = String(rec[field] || '').trim()
    if (raw) return raw
    for (const r of (rec.link_reasons || []).map(parseReason).filter(Boolean)) {
      if (r.field === field && norm(r.value)) return r.value
    }
    return ''
  }
  const valueOf = (n) => {
    const out = new Map()
    for (const f of LINK_FIELDS) {
      const v = norm(n.record[f])
      if (v && v.length >= 3) out.set(f, v)
    }
    for (const r of (n.record.link_reasons || []).map(parseReason).filter(Boolean)) {
      const v = norm(r.value)
      if (v && v.length >= 3 && !out.has(r.field)) out.set(r.field, v)
    }
    return out
  }

  const memberNodes = nodes.filter((n) => !n.isSeed)
  for (const n of memberNodes) {
    const vals = valueOf(n)
    for (const [f, v] of vals) {
      const key = `${f}:${v}`
      if (!byValue.has(key)) byValue.set(key, [])
      byValue.get(key).push(n.id)
    }
  }

  for (const [key, ids] of byValue) {
    if (ids.length < 2) continue
    const [field] = key.split(':')
    const head = ids[0]
    for (let i = 1; i < ids.length; i++) {
      // show the raw value (as typed in the source file) in tooltips,
      // not the normalised form
      const raw = rawValueOf(ids[i], field) || key.slice(field.length + 1)
      addEdge(head, ids[i], field, raw)
    }
  }

  // edge cap: prefer seed edges, then member-member
  let finalEdges = edges
  if (finalEdges.length > MAX_EDGES) {
    const seedEdges = finalEdges.filter((e) => e.source === 'seed' || e.target === 'seed')
    const intra = finalEdges.filter((e) => e.source !== 'seed' && e.target !== 'seed')
    finalEdges = [...seedEdges, ...intra.slice(0, MAX_EDGES - seedEdges.length)]
  }

  return { nodes, edges: finalEdges }
}

/* Build { nodes, edges } from the backend BFS entity graph (depth > 1).
   Node ids are prefixed ("e<recordId>") so they can't collide with the
   depth-1 'seed' node; every node carries its BFS depth so the layout can
   place 2nd/3rd-degree members in outer rings. */
function buildEntityGraph(res) {
  // Prefer a spread across BFS depths over a blind first-40 slice: large
  // families (e.g. ARTEE = 210 rows) can have 40+ depth-0/1 records, which
  // would otherwise push every 2nd-degree member out of the mini graph.
  // Keep the seed records plus a healthy sample of each outer ring.
  const PER_DEPTH = [12, 16, 8, 4] // depth 0 / 1 / 2 / 3+ — sums to MAX_NODES
  const counts = {}
  const rawNodes = []
  for (const n of res?.graph?.nodes || []) {
    const d = Math.min(n.depth ?? 1, 3)
    if ((counts[d] || 0) >= (PER_DEPTH[d] ?? 4)) continue
    counts[d] = (counts[d] || 0) + 1
    rawNodes.push(n)
  }
  const idMap = new Map()
  const nodes = []
  for (const n of rawNodes) {
    const nodeId = `e${n.id}`
    idMap.set(n.id, nodeId)
    nodes.push({
      id: nodeId,
      name: n.name || 'Record',
      isSeed: n.depth === 0,
      depth: n.depth ?? 1,
      record: n,
    })
  }

  const edges = []
  for (const e of res?.graph?.edges || []) {
    const s = idMap.get(e.source)
    const t = idMap.get(e.target)
    if (s && t) edges.push({ source: s, target: t, field: e.field, value: e.value })
  }

  // edge cap: prefer edges touching the seed records
  let finalEdges = edges
  if (finalEdges.length > MAX_EDGES) {
    const seedIds = new Set(nodes.filter((n) => n.isSeed).map((n) => n.id))
    const seedEdges = finalEdges.filter((e) => seedIds.has(e.source) || seedIds.has(e.target))
    const intra = finalEdges.filter((e) => !seedIds.has(e.source) && !seedIds.has(e.target))
    finalEdges = [...seedEdges, ...intra.slice(0, MAX_EDGES - seedEdges.length)]
  }

  return { nodes, edges: finalEdges }
}

/* Deterministic radial layout (depth 1):
   seed at centre; members grouped by primary link field into sectors. */
function computeLayout(nodes, edges) {
  const pos = {}
  pos.seed = { x: CX, y: CY }

  const members = nodes.filter((n) => !n.isSeed)
  if (!members.length) return pos

  // primary field per member (first reason / first matching edge)
  const fieldOf = {}
  for (const n of members) {
    const reasons = (n.record.link_reasons || []).map(parseReason).filter(Boolean)
    fieldOf[n.id] = reasons.length ? reasons[0].field : null
  }
  // fall back to any edge field touching the member
  for (const e of edges) {
    for (const id of [e.source, e.target]) {
      if (id !== 'seed' && !fieldOf[id]) fieldOf[id] = e.field
    }
  }

  // group members by field, preserving LINK_FIELDS order, 'other' last
  const order = [...LINK_FIELDS, '__other__']
  const rank = (f) => {
    const i = order.indexOf(f || '__other__')
    return i < 0 ? order.length - 1 : i
  }
  const groups = {}
  for (const n of members) {
    const f = fieldOf[n.id] || '__other__'
    ;(groups[f] = groups[f] || []).push(n)
  }
  const groupKeys = Object.keys(groups).sort((a, b) => rank(a) - rank(b))
  const total = members.length

  const RADII = [168, 196, 224]
  let angle = -Math.PI / 2 // start at 12 o'clock

  for (const f of groupKeys) {
    const list = groups[f]
    const span = (list.length / total) * Math.PI * 2
    list.forEach((n, i) => {
      const mid = angle + span * ((i + 0.5) / list.length)
      const ring = Math.min(i, RADII.length - 1)
      pos[n.id] = {
        x: CX + Math.cos(mid) * RADII[ring],
        y: CY + Math.sin(mid) * RADII[ring],
      }
    })
    angle += span
  }
  return pos
}

/* Concentric-ring layout (depth > 1): seed records cluster at the centre,
   direct members on ring 1, members 2 hops out on ring 2, 3 hops on ring 3. */
function computeDepthLayout(nodes) {
  const pos = {}
  const byDepth = {}
  for (const n of nodes) {
    const d = Math.min(n.depth ?? (n.isSeed ? 0 : 1), 3)
    ;(byDepth[d] = byDepth[d] || []).push(n)
  }
  // Max ring 275 keeps node circles + labels inside the 640×460 canvas
  // (half-width 320, node radius ~12 — 310 would clip at the extremes).
  const RADII = [0, 150, 225, 275]
  for (const [dStr, list] of Object.entries(byDepth)) {
    const d = +dStr
    const r = RADII[d]
    if (r === 0) {
      // seed records cluster around the centre
      list.forEach((node, i) => {
        const off = i - (list.length - 1) / 2
        pos[node.id] = { x: CX + off * 80, y: CY + off * 46 }
      })
    } else {
      list.forEach((node, i) => {
        const ang = (i / list.length) * Math.PI * 2 - Math.PI / 2
        pos[node.id] = { x: CX + Math.cos(ang) * r, y: CY + Math.sin(ang) * r }
      })
    }
  }
  return pos
}

/* ── detail card for the pinned node ─────────────────────────── */
function DetailCard({ node, onClose }) {
  const rec = node.record || {}
  const rows = [
    ['Sheet', rec.sheet_name || rec.sheet],
    ['TID', rec.tid],
    ['MX Code', rec.mxcode],
    ['Email', rec.email],
    ['Phone', rec.phone],
    ['Account', rec.account_name],
  ].filter(([, v]) => v)

  return (
    <div className="absolute right-3 top-3 w-60 rounded-xl border border-outline-variant bg-surface-container-lowest p-4 shadow-lg animate-fade-in-up">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[13px] font-bold text-on-surface">{node.name}</p>
          <p className="font-plex text-[10px] font-semibold uppercase tracking-wider text-primary">
            {node.isSeed ? 'Seed record' : node.depth > 1 ? `${node.depth} hops out` : 'Linked record'}
          </p>
        </div>
        <button onClick={onClose} className="shrink-0 rounded-full p-1 text-outline transition-colors hover:bg-surface-container">
          <span className="msi text-[16px]">close</span>
        </button>
      </div>
      <div className="space-y-2">
        {rows.map(([label, v]) => (
          <div key={label}>
            <p className="font-plex text-[9px] font-semibold uppercase tracking-wider text-outline">{label}</p>
            <p className="truncate font-mono text-[11px] font-medium text-on-surface">{v}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ── main component ──────────────────────────────────────────── */
export default function RelationshipNetwork({ seed, members }) {
  const seedName = seed?.merchant_name || 'Seed'
  const [depth, setDepth] = useState(1) // hops: 1 = profile family, 2/3 = BFS
  const [graphData, setGraphData] = useState(null) // entity BFS response for depth > 1
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [hoverId, setHoverId] = useState(null)
  const [pinnedId, setPinnedId] = useState(null)

  const isExpanded = depth > 1

  /* Fetch the BFS entity graph when the user bumps depth past 1.
     depth 1 uses the already-loaded profile members (no fetch). */
  useEffect(() => {
    if (!isExpanded) {
      setGraphData(null)
      setError('')
      setLoading(false) // a depth-2/3 fetch may still be in flight — its
      // cleanup cancels it and skips the finally's setLoading(false), so
      // clear it here or the spinner would stay stuck forever
      return
    }
    let cancelled = false
    setLoading(true)
    setError('')
    api.entity(seedName, depth, 120)
      .then((res) => {
        if (!cancelled) setGraphData(res)
      })
      .catch(() => {
        if (!cancelled) setError('Could not expand the network — showing direct links')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [isExpanded, depth, seedName])

  const { nodes, edges } = useMemo(() => {
    // On a fetch error graphData stays null and we fall back to the direct
    // family so the user still sees something useful.
    if (isExpanded && graphData) return buildEntityGraph(graphData)
    return buildGraph(seed, members)
  }, [isExpanded, graphData, seed, members])

  const positions = useMemo(() => {
    if (isExpanded && graphData) return computeDepthLayout(nodes)
    return computeLayout(nodes, edges)
  }, [isExpanded, graphData, nodes, edges])

  const focus = hoverId || pinnedId
  const activeEdges = useMemo(() => {
    if (!focus) return null
    const s = new Set()
    for (const e of edges) {
      if (e.source === focus || e.target === focus) {
        s.add(`${e.source}→${e.target}::${e.field}`)
      }
    }
    return s
  }, [focus, edges])

  const fieldCounts = useMemo(() => {
    const c = {}
    for (const e of edges) c[e.field] = (c[e.field] || 0) + 1
    return c
  }, [edges])

  const pinnedNode = pinnedId ? nodes.find((n) => n.id === pinnedId) : null
  const totalNodes = isExpanded ? (graphData?.graph?.nodes?.length || 0) : (members?.length || 0)
  const shownNote = totalNodes > MAX_NODES

  function changeDepth(d) {
    if (d === depth) return
    setDepth(d)
    setHoverId(null)
    setPinnedId(null)
  }

  const depthStyle = (n) => {
    const d = Math.min(n.depth ?? (n.isSeed ? 0 : 1), 3)
    if (d === 0) return { r: 22, fill: 'var(--color-primary)', stroke: 'none', text: 'var(--color-on-primary)' }
    if (d === 1) {
      return { r: 15, fill: 'var(--color-surface-container-high)', stroke: 'var(--color-outline-variant)', text: 'var(--color-on-surface)' }
    }
    // 2+ hops: lighter fill + tertiary outline so the extra ring reads as "further out"
    return { r: 12, fill: 'var(--color-surface-variant)', stroke: 'var(--color-tertiary)', text: 'var(--color-on-surface)' }
  }

  return (
    <div className="animate-fade-in-up overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-sm">
      {/* depth control */}
      <div className="flex items-center justify-between gap-3 border-b border-outline-variant bg-surface-container-low px-4 py-2">
        <span className="flex items-center gap-1.5 font-plex text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">
          <span className="msi text-[15px]">layers</span>
          Depth
          <span className="hidden text-[10px] font-normal normal-case tracking-normal text-outline sm:inline">
            — show members up to N hops from the seed
          </span>
        </span>
        <div className="flex items-center gap-2">
          {loading && <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary border-t-transparent" title="Tracing hops…" />}
          <div className="flex overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest">
            {[1, 2, 3].map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => changeDepth(d)}
                title={`Show members up to ${d} ${d === 1 ? 'hop' : 'hops'} from the seed`}
                className={`px-3 py-1 font-plex text-[12px] font-bold transition-colors ${
                  depth === d ? 'bg-primary text-on-primary' : 'text-on-surface-variant hover:bg-surface-container'
                }`}
              >
                {d}
              </button>
            ))}
          </div>
        </div>
      </div>

      {error && (
        <div className="border-b border-outline-variant bg-error-container/30 px-4 py-1.5 text-center font-plex text-[11px] font-medium text-error">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex flex-col items-center justify-center py-16 text-on-surface-variant">
          <span className="mb-3 h-8 w-8 animate-spin rounded-full border-[3px] border-primary border-t-transparent" />
          <p className="text-[12px] font-medium">Tracing {depth} hops…</p>
        </div>
      ) : nodes.length < 2 ? (
        <div className="p-8 text-center text-sm text-on-surface-variant">
          This profile has a single record — no network to draw.
        </div>
      ) : (
        <div className="relative">
          <svg
            viewBox={`0 0 ${VW} ${VH}`}
            className="h-auto w-full touch-none select-none"
            role="img"
            aria-label="Relationship network of linked merchant records"
          >
            {/* edges */}
            {edges.map((e, i) => {
              const a = positions[e.source]
              const b = positions[e.target]
              if (!a || !b) return null
              const meta = linkMeta(e.field)
              const key = `${e.source}→${e.target}::${e.field}`
              const lit = !focus || activeEdges.has(key)
              return (
                <line
                  key={i}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke={meta.stroke}
                  strokeWidth={e.source === 'seed' || e.target === 'seed' ? 1.8 : 1.1}
                  strokeOpacity={lit ? (focus ? 0.95 : 0.55) : 0.08}
                  strokeLinecap="round"
                >
                  <title>{`${meta.label}: ${e.value}`}</title>
                </line>
              )
            })}

            {/* nodes */}
            {nodes.map((n) => {
              const p = positions[n.id]
              if (!p) return null
              const st = depthStyle(n)
              const connected = focus
                ? edges.some((e) => (e.source === focus || e.target === focus) && (e.source === n.id || e.target === n.id))
                : true
              const isFocus = focus === n.id
              const dim = focus && !connected && !isFocus
              return (
                <g
                  key={n.id}
                  transform={`translate(${p.x} ${p.y})`}
                  onMouseEnter={() => setHoverId(n.id)}
                  onMouseLeave={() => setHoverId(null)}
                  onClick={() => setPinnedId((cur) => (cur === n.id ? null : n.id))}
                  className="cursor-pointer"
                  opacity={dim ? 0.3 : 1}
                  style={{ transition: 'opacity 0.15s' }}
                >
                  {isFocus && <circle r={st.r + 6} fill="none" stroke="var(--color-primary)" strokeWidth={2} strokeDasharray="4 4" />}
                  <circle r={st.r} fill={st.fill} stroke={st.stroke} strokeWidth={1} />
                  <text
                    textAnchor="middle"
                    dy="0.35em"
                    fontSize={n.isSeed || st.r >= 20 ? 13 : 10}
                    fontWeight={700}
                    fill={st.text}
                    style={{ pointerEvents: 'none' }}
                  >
                    {initials(n.name)}
                  </text>
                  <text
                    y={st.r + 13}
                    textAnchor="middle"
                    fontSize={9.5}
                    fontWeight={isFocus ? 700 : 500}
                    fill="var(--color-on-surface)"
                    style={{ pointerEvents: 'none' }}
                  >
                    {truncate(n.name, 22)}
                  </text>
                  <text
                    y={st.r + 24}
                    textAnchor="middle"
                    fontSize={8.5}
                    fill="var(--color-outline)"
                    style={{ pointerEvents: 'none', fontFamily: 'IBM Plex Mono, monospace' }}
                  >
                    {n.isSeed ? '' : n.record.tid ? `TID ${n.record.tid}` : ''}
                  </text>
                  <title>{`${n.name}\n${n.record.sheet_name || n.record.sheet || 'Parameter file'}\nTID: ${n.record.tid || '—'}\nEmail: ${n.record.email || '—'}`}</title>
                </g>
              )
            })}
          </svg>

          {/* legend */}
          <div className="absolute left-3 top-3 flex max-w-[320px] flex-wrap gap-1.5 rounded-lg border border-outline-variant bg-surface-container-lowest/95 p-2 shadow-sm backdrop-blur">
            {Object.entries(LINK_META)
              .filter(([f]) => fieldCounts[f])
              .map(([f, meta]) => (
                <span
                  key={f}
                  className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-plex text-[10px] font-semibold"
                  style={{ borderColor: meta.stroke + '40', color: meta.stroke, background: meta.stroke + '0d' }}
                >
                  <span className="msi text-[12px]">{meta.icon}</span>
                  {meta.label.replace('Shared ', '')}
                  <span className="opacity-70">· {fieldCounts[f]}</span>
                </span>
              ))}
            {Object.keys(fieldCounts).length === 0 && (
              <span className="font-plex text-[10px] text-outline">No identifier links</span>
            )}
          </div>

          {/* hint */}
          <div className="pointer-events-none absolute bottom-3 left-3 rounded-lg bg-surface-container-lowest/90 px-2.5 py-1 font-plex text-[10px] text-outline shadow-sm backdrop-blur">
            Hover to trace · click to inspect
          </div>

          {/* detail card */}
          {pinnedNode && <DetailCard node={pinnedNode} onClose={() => setPinnedId(null)} />}
        </div>
      )}

      {/* footer stats */}
      <div className="flex items-center justify-between border-t border-outline-variant bg-surface-container-low px-4 py-2 font-plex text-[11px] text-on-surface-variant">
        <span>
          <b className="text-on-surface">{nodes.length}</b> records · <b className="text-on-surface">{edges.length}</b> identifier links
        </span>
        {isExpanded && !shownNote && <span className="text-outline">expanded to {depth} hops</span>}
        {shownNote && <span className="text-outline">showing first {MAX_NODES} of {totalNodes}</span>}
      </div>
    </div>
  )
}
