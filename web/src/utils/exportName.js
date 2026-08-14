// Descriptive download filenames for every Excel export button — mirrors
// the manual workbook naming ("ADDIDE_Static_Accounts.xlsx") instead of
// generic names like "task_results.xlsx". Sanitises the base so the result
// is always a safe filename.

function slug(s) {
  return String(s || '')
    .replace(/[^a-z0-9]+/gi, '_')
    .replace(/^_+|_+$/g, '')
}

function titleCase(s) {
  return slug(s)
    .split('_')
    .filter(Boolean)
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join('_')
}

/**
 * Build an export filename like `MEDPLUS_Limited_profile.xlsx`.
 *
 * @param {string} base      primary label (merchant / query / key root)
 * @param {string} suffix    what the file contains ('profile', 'batch_search')
 * @param {string} fallback  used when base is empty
 */
export function exportFilename(base, suffix, fallback = 'Export') {
  const name = titleCase(base) || titleCase(fallback)
  const kind = slug(suffix) || 'results'
  return `${name}_${kind}.xlsx`
}
