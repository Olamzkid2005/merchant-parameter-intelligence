// NIBSS bank code → name resolution for the frontend (mirrors the backend
// config.bank_name). The workbook `bank` column stores institution codes
// (070, 058, 011…) — never display a bare code as if it were a bank name.
const NIBSS_BANKS = {
  '011': 'First Bank of Nigeria',
  '023': 'Citibank Nigeria',
  '030': 'Heritage Bank',
  '032': 'Union Bank of Nigeria',
  '033': 'United Bank for Africa (UBA)',
  '035': 'Wema Bank',
  '040': 'Ecobank Nigeria',
  '044': 'Access Bank',
  '050': 'Ecobank Nigeria',
  '058': 'Guaranty Trust Bank (GTBank)',
  '063': 'Diamond Bank',
  '068': 'Standard Chartered Bank',
  '070': 'Fidelity Bank',
  '076': 'Polaris Bank',
  '082': 'Keystone Bank',
  '084': 'Enterprise Bank',
  '085': 'SunTrust Bank',
  '090': 'Providus Bank',
  '101': 'Providus Bank',
  '103': 'Globus Bank',
  '214': 'First City Monument Bank (FCMB)',
  '215': 'Unity Bank',
  '221': 'Stanbic IBTC Bank',
  '232': 'Sterling Bank',
  '301': 'Jaiz Bank',
  '302': 'Kuda Microfinance Bank',
  '303': 'Moniepoint MFB',
  '401': 'Mint FB',
  '501': '9 Payment Service Bank (9PSB)',
  '502': 'Eyowo MFB',
  '503': 'Paga',
  '505': 'Grey MFB',
  '901': '9 Payment Service Bank (9PSB)',
  '903': 'Palmpay',
  '904': 'Opay',
  '905': 'Moniepoint MFB',
  '999': 'CBN Settlement',
}

// A value that is NOT a plausible identifier for its field (mirrors the
// backend entity._plausible). Used to hide garbage TIDs / payable codes /
// MIDs (507, POS, GPRS…) that leaked into identifier columns.
const IDENT_SHAPES = {
  tid: /^(?:2ISW[A-Z0-9]{4,6}|\d{8}|\d{4}[A-Z]\d{3})$/i,
  mxcode: /^MX\d{4,8}$/i,
  merchant_id: /^2ISW[A-Z0-9]{11}$/i,
  payable_code: /^(?:\d{7}|Default[_-]?Payable[_-]?MX\d+|MX\d+_[A-Z_]+)$/i,
  account_number: /^\d{10}$/,
}

export function bankName(code) {
  const s = String(code || '').trim()
  if (!s) return ''
  if (/[a-z]/i.test(s)) return s // already a name
  return NIBSS_BANKS[s] || s
}

export function plausibleId(field, value) {
  const v = String(value || '').trim()
  if (!v || v.length < 4) return false
  if (field === 'email') return v.includes('@') && v.split('@')[1]?.includes('.')
  const shape = IDENT_SHAPES[field]
  if (!shape) return true
  return shape.test(v.toUpperCase())
}
