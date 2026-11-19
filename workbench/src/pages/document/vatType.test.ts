import { describe, expect, it } from 'vitest'

import { linesForVatType } from './formModel'

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
