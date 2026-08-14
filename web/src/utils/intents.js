// Shared intent metadata — label, Material Symbol icon and chip tone per
// intent, so every surface (task results chips, clarification options, rule
// engine list) renders the same friendly name for the same intent.
// Unknown intents fall back to a neutral generic chip.

const INTENT_META = {
  static_account: { label: 'Static account', icon: 'account_balance', tone: 'border-secondary/30 bg-secondary/10 text-secondary' },
  mxcode: { label: 'MX code', icon: 'qr_code_2', tone: 'border-primary/25 bg-primary/10 text-primary' },
  email: { label: 'Emails', icon: 'alternate_email', tone: 'border-primary/25 bg-primary/10 text-primary' },
  phone: { label: 'Phones', icon: 'phone', tone: 'border-primary/25 bg-primary/10 text-primary' },
  address: { label: 'Addresses', icon: 'location_on', tone: 'border-primary/25 bg-primary/10 text-primary' },
  bank: { label: 'Banks', icon: 'account_balance', tone: 'border-primary/25 bg-primary/10 text-primary' },
  account_name: { label: 'Account names', icon: 'badge', tone: 'border-primary/25 bg-primary/10 text-primary' },
  account_number: { label: 'Account numbers', icon: 'pin', tone: 'border-primary/25 bg-primary/10 text-primary' },
  payable: { label: 'Payable codes', icon: 'payments', tone: 'border-primary/25 bg-primary/10 text-primary' },
  alias: { label: 'Aliases', icon: 'label', tone: 'border-primary/25 bg-primary/10 text-primary' },
  contact: { label: 'Contacts', icon: 'person', tone: 'border-primary/25 bg-primary/10 text-primary' },
  onboarded: { label: 'Onboarded dates', icon: 'event', tone: 'border-primary/25 bg-primary/10 text-primary' },
  state: { label: 'States', icon: 'map', tone: 'border-primary/25 bg-primary/10 text-primary' },
  source: { label: 'Source files', icon: 'folder', tone: 'border-primary/25 bg-primary/10 text-primary' },
  beneficiary: { label: 'Beneficiaries', icon: 'person_search', tone: 'border-secondary/30 bg-secondary/10 text-secondary' },
  profile: { label: 'Merchant profile', icon: 'storefront', tone: 'border-secondary/30 bg-secondary/10 text-secondary' },
  change_details: { label: 'Change of account details', icon: 'swap_horiz', tone: 'border-orange-200 bg-orange-50 text-orange-900' },
  related: { label: 'Related records', icon: 'hub', tone: 'border-secondary/30 bg-secondary/10 text-secondary' },
  formerly: { label: 'Formerly / name history', icon: 'history', tone: 'border-secondary/30 bg-secondary/10 text-secondary' },
  compare: { label: 'Compare merchants', icon: 'compare_arrows', tone: 'border-secondary/30 bg-secondary/10 text-secondary' },
  coverage: { label: 'Missing data', icon: 'rule', tone: 'border-orange-200 bg-orange-50 text-orange-900' },
  top: { label: 'Top rankings', icon: 'leaderboard', tone: 'border-secondary/30 bg-secondary/10 text-secondary' },
  verify: { label: 'Verify in registry', icon: 'verified', tone: 'border-green-200 bg-green-100 text-green-900' },
  count: { label: 'Count', icon: 'numbers', tone: 'border-secondary/30 bg-secondary/10 text-secondary' },
  duplicates: { label: 'Duplicates', icon: 'content_copy', tone: 'border-orange-200 bg-orange-50 text-orange-900' },
  summary: { label: 'Summary', icon: 'summarize', tone: 'border-secondary/30 bg-secondary/10 text-secondary' },
  segment: { label: 'Segment / collection', icon: 'dataset', tone: 'border-secondary/30 bg-secondary/10 text-secondary' },
  resolve: { label: 'Resolve', icon: 'search', tone: 'border-primary/25 bg-primary/10 text-primary' },
}

const FALLBACK = { label: 'Request', icon: 'auto_awesome', tone: 'border-outline-variant bg-surface-container text-on-surface-variant' }

export function intentMeta(intent) {
  return INTENT_META[intent] || FALLBACK
}

export function intentLabel(intent) {
  return intentMeta(intent).label
}

export function intentIcon(intent) {
  return intentMeta(intent).icon
}

export function intentTone(intent) {
  return intentMeta(intent).tone
}
