/** Dates are typed and shown day/month/year for everyone (invoices in the UAE are printed that
 * way), whatever the computer's language; the form and the API keep 2026-10-17. */

const ISO = /^(\d{4})-(\d{2})-(\d{2})$/
const DAY_MONTH_YEAR = /^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\s*$/
const FULL_DAY_MONTH_YEAR = /^(\d{2})\/(\d{2})\/(\d{4})$/

/** A real calendar date written 2026-10-17 (not 2026-02-31). */
export function isIsoDate(value: string): boolean {
  const match = ISO.exec(value)
  return match !== null && isReal(Number(match[1]), Number(match[2]), Number(match[3]))
}

/** "2026-10-17" -> "17/10/2026"; anything else is shown as it is. */
export function toDayMonthYear(value: string): string {
  const match = ISO.exec(value)
  return match ? `${match[3]}/${match[2]}/${match[1]}` : value
}

/** "17/10/2026" (also 17-10-2026, 17.10.2026, 7/1/2026) -> "2026-10-17"; null when it is not a
 * real date written day first. Never guesses which number is the month. `full` takes only
 * dd/mm/yyyy, so a date half edited (17/1/2026 on the way to 17/11/2026) is not taken early. */
export function fromDayMonthYear(text: string, { full = false } = {}): string | null {
  const match = (full ? FULL_DAY_MONTH_YEAR : DAY_MONTH_YEAR).exec(text)
  if (!match) return null
  const [day, month, year] = [Number(match[1]), Number(match[2]), Number(match[3])]
  if (!isReal(year, month, day)) return null
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

function isReal(year: number, month: number, day: number): boolean {
  const at = new Date(Date.UTC(year, month - 1, day))
  return at.getUTCFullYear() === year && at.getUTCMonth() === month - 1 && at.getUTCDate() === day
}
