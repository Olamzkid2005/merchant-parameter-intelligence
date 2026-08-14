// Shared metadata for relationship link types (the identifiers that connect
// records into a family). Single source of truth for edge colours + legend
// labels — used by the Entity Graph page and the Profile page's mini network.

export const LINK_META = {
  email: { stroke: '#004ac6', label: 'Shared Email', icon: 'mail' },
  mxcode: { stroke: '#7c3aed', label: 'Shared MX Code', icon: 'dns' },
  phone: { stroke: '#006c49', label: 'Shared Phone', icon: 'call' },
  tid: { stroke: '#d97706', label: 'Shared TID', icon: 'point_of_sale' },
  payable_code: { stroke: '#db2777', label: 'Shared Payable Code', icon: 'receipt_long' },
  account_number: { stroke: '#ba1a1a', label: 'Shared Account No.', icon: 'account_balance' },
  merchant_id: { stroke: '#0d9488', label: 'Shared MID', icon: 'storefront' },
}

export const DEFAULT_LINK_META = { stroke: '#737686', label: 'Linked', icon: 'link' }

// Preferred order for layout sectors + legend.
export const LINK_FIELDS = ['email', 'phone', 'tid', 'mxcode', 'payable_code', 'account_number', 'merchant_id']

export function linkMeta(field) {
  return LINK_META[field] || DEFAULT_LINK_META
}

export function initials(name) {
  const parts = String(name || '?').trim().split(/\s+/).filter(Boolean).slice(0, 2)
  return parts.map((p) => p[0] || '').join('').toUpperCase() || '?'
}

export function truncate(s, n) {
  s = String(s || '')
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}
