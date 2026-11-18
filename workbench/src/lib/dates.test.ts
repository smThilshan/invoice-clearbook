import { describe, expect, it } from 'vitest'

import { fromDayMonthYear, isIsoDate, toDayMonthYear } from './dates'

describe('dates shown day/month/year for everyone', () => {
  it('shows a saved date day first, and anything else as it is', () => {
    expect(toDayMonthYear('2026-10-17')).toBe('17/10/2026')
    expect(toDayMonthYear('2026-01-07')).toBe('07/01/2026')
    expect(toDayMonthYear('')).toBe('')
    expect(toDayMonthYear('17/1')).toBe('17/1') // half typed: left alone
  })

  it('reads a typed date day first, never month first', () => {
    expect(fromDayMonthYear('17/10/2026')).toBe('2026-10-17')
    expect(fromDayMonthYear('10/11/2026')).toBe('2026-11-10') // 10 November, not 11 October
    expect(fromDayMonthYear('7/1/2026')).toBe('2026-01-07')
    expect(fromDayMonthYear('17-10-2026')).toBe('2026-10-17')
    expect(fromDayMonthYear('17.10.2026')).toBe('2026-10-17')
  })

  it('refuses what is not a real date instead of guessing one', () => {
    expect(fromDayMonthYear('31/02/2026')).toBeNull()
    expect(fromDayMonthYear('29/02/2025')).toBeNull()
    expect(fromDayMonthYear('10/17/2026')).toBeNull() // month first: no 17th month
    expect(fromDayMonthYear('17/10/26')).toBeNull() // two-digit year
    expect(fromDayMonthYear('2026-10-17')).toBeNull()
    expect(fromDayMonthYear('')).toBeNull()
    expect(fromDayMonthYear('29/02/2028')).toBe('2028-02-29') // a leap year
  })

  it('while typing, takes only a full dd/mm/yyyy, so a half-edited date is not taken early', () => {
    expect(fromDayMonthYear('17/1/2026', { full: true })).toBeNull()
    expect(fromDayMonthYear('17/11/2026', { full: true })).toBe('2026-11-17')
  })

  it('knows a real saved date', () => {
    expect(isIsoDate('2026-10-17')).toBe(true)
    expect(isIsoDate('2026-02-31')).toBe(false)
    expect(isIsoDate('17/10/2026')).toBe(false)
    expect(isIsoDate('')).toBe(false)
  })
})
