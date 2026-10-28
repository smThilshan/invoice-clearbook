/**
 * Exact decimal arithmetic for amounts typed in the review form (rule 1: money is never a
 * float). Amounts stay strings; sums use whole numbers (BigInt) at a fixed scale.
 */

const SCALE = 6 // decimal places kept: enough for unit prices like 0.123456
const FACTOR = 10n ** BigInt(SCALE)
export const TOLERANCE = parseAmount('0.01') ?? 0n // same as the server's ±0.01

/** "1,050.10" / "1050.1" / "-3" -> scaled integer; "" or not a number -> null. */
export function parseAmount(text: string): bigint | null {
  const cleaned = text.trim().replace(/,/g, '')
  const match = /^(-?)(\d+)(?:\.(\d*))?$/.exec(cleaned) ?? /^(-?)()\.(\d+)$/.exec(cleaned)
  if (!match) return null
  const [, sign = '', whole = '', fraction = ''] = match
  if (fraction.length > SCALE) return null
  const value = BigInt(whole || '0') * FACTOR + BigInt(fraction.padEnd(SCALE, '0') || '0')
  return sign === '-' ? -value : value
}

/** Scaled integer -> "1,050.10" (at least 2 decimals, trailing zeros beyond that dropped). */
export function formatScaled(value: bigint): string {
  const negative = value < 0n
  const absolute = negative ? -value : value
  const whole = (absolute / FACTOR).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  let fraction = (absolute % FACTOR).toString().padStart(SCALE, '0').replace(/0+$/, '')
  fraction = fraction.padEnd(2, '0')
  return `${negative ? '-' : ''}${whole}.${fraction}`
}

/**
 * "4.5" -> "4.50", "4" -> "4.00": money as accountants read it. Only zeros are added, never
 * rounded or cut ("28.234" stays, so a misread third decimal still shows); "" and text that is
 * not a plain number are left as they are.
 */
export function withCents(text: string): string {
  const match = /^(-?\d+)(?:\.(\d*))?$/.exec(text)
  if (!match) return text
  const [, whole = '', fraction = ''] = match
  return `${whole}.${fraction.padEnd(2, '0')}`
}

/** Sum of amounts; null if any of them is not a number. Empty strings count as missing. */
export function sumAmounts(texts: string[]): bigint | null {
  let total = 0n
  for (const text of texts) {
    const value = parseAmount(text)
    if (value === null) return null
    total += value
  }
  return total
}

export function withinTolerance(a: bigint, b: bigint): boolean {
  const difference = a - b
  return (difference < 0n ? -difference : difference) <= TOLERANCE
}
