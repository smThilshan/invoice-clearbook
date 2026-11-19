import { describe, expect, it } from 'vitest'

import { followVatType, linesForVatType } from './formModel'

const ALL = ['STANDARD_5', 'ZERO', 'EXEMPT', 'NONE'] as const

describe('the lines follow "Amounts are" (decision 105)', () => {
  it('No VAT puts only the 5% lines on No VAT', () => {
    expect(linesForVatType(['STANDARD_5', 'ZERO', 'EXEMPT', ''], 'EXCLUSIVE', 'NO_TAX', [...ALL]))
      .toEqual(['NONE', 'ZERO', 'EXEMPT', ''])  // prettier-ignore
  })

  it('back to VAT puts the No VAT lines on 5%, for added on top and included alike', () => {
    for (const next of ['EXCLUSIVE', 'INCLUSIVE'] as const) {
      expect(linesForVatType(['NONE', 'ZERO'], 'NO_TAX', next, [...ALL])).toEqual(['STANDARD_5', 'ZERO'])
    }
  })

  it('changes nothing between added on top and included, or without the code', () => {
    expect(linesForVatType(['STANDARD_5'], 'EXCLUSIVE', 'INCLUSIVE', [...ALL])).toEqual(['STANDARD_5'])
    expect(linesForVatType(['STANDARD_5'], 'EXCLUSIVE', 'NO_TAX', ['STANDARD_5', 'ZERO'])).toEqual(['STANDARD_5'])
    expect(linesForVatType(['NONE'], 'NO_TAX', 'EXCLUSIVE', ['NONE'])).toEqual(['NONE'])
    expect(linesForVatType(['NONE'], 'NO_TAX', '', [...ALL])).toEqual(['NONE'])  // not stated
  })
})

describe('"Amounts are" carries the VAT and the amounts (decision 106)', () => {
  const line = (amount: string, tax_type: 'STANDARD_5' | 'NONE' | 'ZERO', qty = '1', unit_price = amount) =>
    ({ qty, unit_price, amount, tax_type })
  // The Al Ain receipt: 40.00 + 7.00 + 6.61 = 53.61, VAT 2.68, total 56.29.
  const alAin = {
    vat: '2.68', total: '56.29',
    lines: [line('40.00', 'STANDARD_5', '5', '8.00'), line('7.00', 'STANDARD_5'), line('6.61', 'STANDARD_5')],
  }

  it('No VAT adds the unclaimed VAT into the lines by size, so they add up to the total', () => {
    const after = followVatType(alAin, 'EXCLUSIVE', 'NO_TAX', [...ALL], '')
    expect(after.vat).toBe('')
    expect(after.total).toBe('56.29')
    expect(after.lines.map((l) => [l.amount, l.unit_price, l.tax_type])).toEqual([
      ['42.00', '8.40', 'NONE'], ['7.35', '7.35', 'NONE'], ['6.94', '6.94', 'NONE'],
    ])  // prettier-ignore
  })

  it('back to added on top takes that VAT out of the lines again and refills the VAT', () => {
    const noVat = followVatType(alAin, 'EXCLUSIVE', 'NO_TAX', [...ALL], '')
    const back = followVatType(noVat, 'NO_TAX', 'EXCLUSIVE', [...ALL], '2.68')
    expect(back.vat).toBe('2.68')
    expect(back.lines.map((l) => [l.amount, l.unit_price, l.tax_type])).toEqual([
      ['40.00', '8.00', 'STANDARD_5'], ['7.00', '7.00', 'STANDARD_5'], ['6.61', '6.61', 'STANDARD_5'],
    ])  // prettier-ignore
  })

  it('back to included refills the VAT and keeps the lines, which already hold it', () => {
    const noVat = followVatType(alAin, 'EXCLUSIVE', 'NO_TAX', [...ALL], '')
    const back = followVatType(noVat, 'NO_TAX', 'INCLUSIVE', [...ALL], '2.68')
    expect(back.vat).toBe('2.68')
    expect(back.lines.map((l) => l.amount)).toEqual(['42.00', '7.35', '6.94'])
  })

  it('the rounding cent goes on the biggest line: the lines move by exactly the VAT', () => {
    const after = followVatType(
      { vat: '1.00', total: '4.00', lines: [line('1.00', 'STANDARD_5'), line('1.00', 'STANDARD_5'), line('1.00', 'STANDARD_5')] },
      'EXCLUSIVE', 'NO_TAX', [...ALL], '',
    )  // prettier-ignore
    expect(after.lines.map((l) => l.amount)).toEqual(['1.34', '1.33', '1.33'])
  })

  it('never moves amounts the numbers do not prove', () => {
    // Lines already hold the VAT (lines = total): nothing to add.
    expect(followVatType({ ...alAin, lines: [line('56.29', 'STANDARD_5')] }, 'INCLUSIVE', 'NO_TAX', [...ALL], '').lines[0]?.amount)
      .toBe('56.29')  // prettier-ignore
    // Lines + VAT do not make the total (a missing line?): left for the checks to show.
    expect(followVatType({ ...alAin, total: '60.00' }, 'EXCLUSIVE', 'NO_TAX', [...ALL], '').lines.map((l) => l.amount))
      .toEqual(['40.00', '7.00', '6.61'])  // prettier-ignore
    // Zero rated lines keep their amount; only lines that left 5% take the VAT.
    expect(followVatType({ vat: '0.50', total: '20.50', lines: [line('10.00', 'STANDARD_5'), line('10.00', 'ZERO')] },
      'EXCLUSIVE', 'NO_TAX', [...ALL], '').lines.map((l) => l.amount)).toEqual(['10.50', '10.00'])  // prettier-ignore
    // No VAT code at the company: no line leaves 5%, so no amount moves.
    expect(followVatType(alAin, 'EXCLUSIVE', 'NO_TAX', ['STANDARD_5'], '').lines.map((l) => l.amount))
      .toEqual(['40.00', '7.00', '6.61'])  // prettier-ignore
    // A VAT already typed is kept when coming back.
    expect(followVatType({ ...alAin, vat: '2.70' }, 'NO_TAX', 'EXCLUSIVE', [...ALL], '2.68').vat).toBe('2.70')
  })
})
