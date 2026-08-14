/**
 * check-export-name.mjs — sanity checks for the shared export filename helper.
 *
 * Run:  node scripts/check-export-name.mjs   (or: npm run test:exports)
 *
 * Guards the naming logic every Export button relies on so a future edit
 * cannot silently regress the descriptive filenames. Exit code is 0 when
 * every check passes, 1 otherwise.
 */
import { exportFilename } from '../src/utils/exportName.js'

let PASS = 0
let FAIL = 0

function check(label, ok, detail = '') {
  if (ok) {
    PASS++
    console.log(`  [PASS] ${label}`)
  } else {
    FAIL++
    console.log(`  [FAIL] ${label}${detail ? `  ${detail}` : ''}`)
  }
}

console.log('[exportName] filename helper sanity checks\n')

// ── Core naming (the shapes users see on every Export button) ────────────
check('task export names after key-merchant root',
      exportFilename('ADDIDE', 'static_account+tid', 'Task') === 'ADDIDE_static_account_tid.xlsx',
      exportFilename('ADDIDE', 'static_account+tid', 'Task'))

check('merchant base is title-cased',
      exportFilename('MEDPLUS LIMITED', 'profile', 'Task') === 'MEDPLUS_LIMITED_profile.xlsx',
      exportFilename('MEDPLUS LIMITED', 'profile', 'Task'))

check('mixed-case base is preserved sensibly',
      exportFilename('SPAR Lekki', 'batch_search', 'Batch_Search') === 'SPAR_Lekki_batch_search.xlsx',
      exportFilename('SPAR Lekki', 'batch_search', 'Batch_Search'))

check('identifier base works (quick match)',
      exportFilename('MX156725', 'quick_match', 'Quick_Match') === 'MX156725_quick_match.xlsx',
      exportFilename('MX156725', 'quick_match', 'Quick_Match'))

check('search names after the query',
      exportFilename('LAGOON WATERS', 'search', 'Search') === 'LAGOON_WATERS_search.xlsx',
      exportFilename('LAGOON WATERS', 'search', 'Search'))

check('report names after first merchant',
      exportFilename('SPAR Lekki', 'intelligence_report', 'Merchant_Intelligence_Report')
        === 'SPAR_Lekki_intelligence_report.xlsx',
      exportFilename('SPAR Lekki', 'intelligence_report', 'Merchant_Intelligence_Report'))

check('reconcile names after first merchant',
      exportFilename('SPAR Lekki', 'reconciliation_report', 'Merchant_Reconciliation_Report')
        === 'SPAR_Lekki_reconciliation_report.xlsx',
      exportFilename('SPAR Lekki', 'reconciliation_report', 'Merchant_Reconciliation_Report'))

check('dated report keeps the ISO date as an underscore slug',
      exportFilename('data quality', '2026-08-11', 'Data_Quality') === 'Data_Quality_2026_08_11.xlsx',
      exportFilename('data quality', '2026-08-11', 'Data_Quality'))

// ── Intent-slug conversions (suffix side) ────────────────────────────────
check('compound intent + becomes an underscore',
      exportFilename('X', 'static_account+tid', 'Task') === 'X_static_account_tid.xlsx',
      exportFilename('X', 'static_account+tid', 'Task'))

check('non-compound intent suffix slugs cleanly',
      exportFilename('X', 'change_details', 'Task') === 'X_change_details.xlsx',
      exportFilename('X', 'change_details', 'Task'))

check('intent-as-base fallback (no root, no rows) is harmless',
      exportFilename('static_account+tid', 'static_account+tid', 'Task')
        === 'Static_Account_Tid_static_account_tid.xlsx',
      exportFilename('static_account+tid', 'static_account+tid', 'Task'))

// ── Fallbacks ────────────────────────────────────────────────────────────
check('empty base falls back to the fallback label',
      exportFilename(undefined, 'batch_search', 'Batch_Search') === 'Batch_Search_batch_search.xlsx',
      exportFilename(undefined, 'batch_search', 'Batch_Search'))

check('empty base falls back to the generic default',
      exportFilename('', 'results') === 'Export_results.xlsx',
      exportFilename('', 'results'))

check('punctuation-only base falls back (never an empty filename)',
      !exportFilename('!!!', 'report', 'Report').startsWith('_.xlsx')
        && exportFilename('!!!', 'report', 'Report') === 'Report_report.xlsx',
      exportFilename('!!!', 'report', 'Report'))

// ── Sanitisation (safe on Windows / any OS) ──────────────────────────────
check('quotes and slashes become underscores',
      exportFilename('C:\\temp\\"BAD" NAME', 'report', 'Report') === 'C_Temp_BAD_NAME_report.xlsx',
      exportFilename('C:\\temp\\"BAD" NAME', 'report', 'Report'))

check('no reserved Windows chars survive',
      !/[<>:"/\\|?*\x00-\x1F]/.test(exportFilename('A:B*C?D|E<F>G', 'report', 'Report')),
      exportFilename('A:B*C?D|E<F>G', 'report', 'Report'))

check('always ends in .xlsx',
      exportFilename('X', 'y', 'Z').endsWith('.xlsx')
        && exportFilename('X', 'y', 'Z').split('.').length === 2,
      exportFilename('X', 'y', 'Z'))

check('emoji in merchant names are stripped to underscores',
      !/[^\x00-\x7F]/.test(exportFilename('MEDPLUS \u{1F3EC}', 'profile', 'Task')),
      exportFilename('MEDPLUS \u{1F3EC}', 'profile', 'Task'))

console.log(`\n  RESULT: ${PASS} passed, ${FAIL} failed`)
process.exit(FAIL > 0 ? 1 : 0)
