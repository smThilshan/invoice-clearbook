import { describe, expect, it } from 'vitest'

import { formatScaled, parseAmount, sumAmounts, withCents, withinTolerance } from './money'

describe('money', () => {
  it('reads typed amounts exactly', () => {
    expect(parseAmount('1,050.10')).toBe(1_050_100_000n)
    expect(parseAmount('0.1')).toBe(100_000n)
    expect(parseAmount('-3')).toBe(-3_000_000n)
    expect(parseAmount('.5')).toBe(500_000n)
    expect(parseAmount('')).toBeNull()
    expect(parseAmount('AED 10')).toBeNull()
  })

  it('adds without floating point errors', () => {
    // 0.1 + 0.2 is 0.30000000000000004 in floats
    expect(formatScaled(sumAmounts(['0.1', '0.2']) ?? 0n)).toBe('0.30')
    expect(formatScaled(sumAmounts(['600.00', '400.00', '33.335']) ?? 0n)).toBe('1,033.335')
    expect(sumAmounts(['1', 'x'])).toBeNull()
  })

  it('compares with the server tolerance of 0.01', () => {
    expect(withinTolerance(parseAmount('1050.00')!, parseAmount('1050.01')!)).toBe(true)
    expect(withinTolerance(parseAmount('1050.00')!, parseAmount('1050.02')!)).toBe(false)
  })

  it('shows money with two decimals, adding zeros only', () => {
    expect(withCents('4.5')).toBe('4.50')
    expect(withCents('4')).toBe('4.00')
    expect(withCents('-3.1')).toBe('-3.10')
    expect(withCents('1050.10')).toBe('1050.10')
    expect(withCents('28.234')).toBe('28.234') // never rounded: a misread third decimal shows
    expect(withCents('')).toBe('')
    expect(withCents('1E+1')).toBe('1E+1')
  })
})
