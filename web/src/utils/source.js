// Helpers for splitting the DB's `sheet_name` field into file + sheet parts.
// Format in intelligence.db is "FileStem :: SheetName"; legacy forms like
// "NNPC:Batch 1" or plain sheet names have no separator (treated as sheet-only).
export function partsOf(sheet) {
  const s = String(sheet || '')
  const sep = s.indexOf(' :: ')
  if (sep > -1) {
    return { file: s.slice(0, sep).trim(), sheet: s.slice(sep + 4).trim() }
  }
  return { file: '', sheet: s.trim() }
}

export function sourceOf(sheet) {
  const { file, sheet: sh } = partsOf(sheet)
  return file || sh
}

export function sheetOf(sheet) {
  return partsOf(sheet).sheet
}
