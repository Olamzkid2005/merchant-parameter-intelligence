// API client for the Merchant Intelligence backend.
// In dev, Vite proxies /api → http://127.0.0.1:8000 (see vite.config.js).

const BASE = '/api'

async function post(path, body) {
  return request('POST', path, body)
}

async function request(method, path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text()
    let detail = text
    try {
      detail = JSON.parse(text).detail || text
    } catch {
      /* keep raw text */
    }
    throw new Error(detail || `API error ${res.status}`)
  }
  return res.json()
}

export const api = {
  stats: async () => {
    const res = await fetch(`${BASE}/stats`)
    if (!res.ok) throw new Error('Failed to load stats')
    return res.json()
  },
  search: (query, limit = 20, offset = 0) =>
    post('/search', { query, limit, offset }),
  suggest: (query) => post('/suggest', { query, limit: 5 }),
  autocomplete: async (prefix, limit = 8) => {
    const res = await fetch(
      `${BASE}/autocomplete?prefix=${encodeURIComponent(prefix)}&limit=${limit}`,
    )
    if (!res.ok) throw new Error('Autocomplete failed')
    return res.json()
  },
  similar: (query, limit = 12) => post('/similar', { query, limit }),
  duplicates: async () => {
    const res = await fetch(`${BASE}/duplicates?limit=100`)
    if (!res.ok) throw new Error('Failed to load duplicates')
    return res.json()
  },
  aliases: async () => {
    const res = await fetch(`${BASE}/aliases`)
    if (!res.ok) throw new Error('Failed to load aliases')
    return res.json()
  },
  aliasApprove: (alias, canonical) => post('/aliases/approve', { alias, canonical }),
  aliasReject: (alias, canonical) => post('/aliases/reject', { alias, canonical }),
  report: (merchants) => post('/report', { merchants }),
  exportReport: (merchants) => fetch(`${BASE}/report/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ merchants }),
  }),
  exportSearch: (query, limit = 100, offset = 0) => fetch(`${BASE}/search/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, limit, offset }),
  }),
  batch: (merchants) => post('/batch', { merchants }),
  entity: (query, depth = 2, max_nodes = 120) =>
    post('/entity', { query, depth, max_nodes }),
  profile: (query, max_members = 200) =>
    post('/profile', { query, max_members }),
  timeline: (query) => post('/timeline', { query }),
  compare: (queryA, queryB, max_members = 200) =>
    post('/compare', { query_a: queryA, query_b: queryB, max_members }),
  learn: (query, merchant_name) => post('/learn', { query, merchant_name }),
  brief: (query, max_members = 200) =>
    post('/brief', { query, max_members }),
  selfImprove: async () => {
    const res = await fetch(`${BASE}/selfimprove`)
    if (!res.ok) throw new Error('Failed to load self-improve status')
    return res.json()
  },
  exportBatch: (merchants) => fetch(`${BASE}/batch/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ merchants }),
  }),
  quality: async () => {
    const res = await fetch(`${BASE}/quality`)
    if (!res.ok) throw new Error('Failed to load quality report')
    return res.json()
  },
  exportQuality: () => fetch(`${BASE}/quality/export`, { method: 'POST' }),
  reconcile: (merchants) => post('/reconcile', { merchants }),
  exportReconcile: (merchants) => fetch(`${BASE}/reconcile/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ merchants }),
  }),
  quickmatch: (identifiers) => post('/quickmatch', { identifiers }),
  exportQuickMatch: (identifiers) => fetch(`${BASE}/quickmatch/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ identifiers }),
  }),
  task: (text, intent, remember) => post('/task', {
    text,
    ...(intent ? { intent } : {}),
    ...(remember ? { remember: true } : {}),
  }),
  preferences: async () => {
    const res = await fetch(`${BASE}/preferences`)
    if (!res.ok) throw new Error('Failed to load saved interpretations')
    return res.json()
  },
  forgetPreference: (key) => request('POST', '/preferences/forget', { key }),
  exportTask: (text) => fetch(`${BASE}/task/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  }),
  intents: async () => {
    const res = await fetch(`${BASE}/intents`)
    if (!res.ok) throw new Error('Failed to load intent config')
    return res.json()
  },
  saveIntent: (intent, spec) => request('PUT', '/intents', {
    intent,
    patterns: spec.patterns,
    keywords: spec.keywords,
    fuzzy: spec.fuzzy,
  }),
  settings: async () => {
    const res = await fetch(`${BASE}/settings`)
    if (!res.ok) throw new Error('Failed to load engine settings')
    return res.json()
  },
  saveSettings: (patch) => request('PUT', '/settings', patch),
  resetSettings: async () => {
    const res = await fetch(`${BASE}/settings`, { method: 'DELETE' })
    if (!res.ok) throw new Error('Failed to reset engine settings')
    return res.json()
  },
  taskAnalyze: (text) => post('/task/analyze', { text }),
  calibration: async () => {
    const res = await fetch(`${BASE}/calibration`)
    if (!res.ok) throw new Error('Failed to load calibration')
    return res.json()
  },
  resetCalibration: async () => {
    const res = await fetch(`${BASE}/calibration/reset`, { method: 'POST' })
    if (!res.ok) throw new Error('Failed to reset calibration')
    return res.json()
  },
  feedbackSuggestions: async () => {
    const res = await fetch(`${BASE}/feedback/suggestions`)
    if (!res.ok) throw new Error('Failed to load pattern suggestions')
    return res.json()
  },
  applySuggestion: (ngram, intent, weight) =>
    post('/feedback/suggestions/apply', { ngram, intent, weight }),
  rejectSuggestion: (ngram, intent) =>
    post('/feedback/suggestions/reject', { ngram, intent }),
  idclassDebug: async (values, text = '') => {
    const params = new URLSearchParams()
    if (values) params.set('values', values)
    if (text) params.set('text', text)
    const res = await fetch(`${BASE}/idclass/debug?${params}`)
    if (!res.ok) throw new Error('Classifier debug failed')
    return res.json()
  },
}
