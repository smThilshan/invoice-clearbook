import { describe, expect, it } from 'vitest'

import type { Invoice } from '@/api/client'

import { toFormValues, toInvoiceInput } from './formModel'

const invoice = {
  document_type: 'RECEIPT',
  subtotal: '4.28',
  vat: '0.22',
  total: '4.5',
  lines: [{ description: 'Oman chips', qty: '2.0', unit_price: '0.75', amount: '1.5' },
          { description: 'Fee', qty: null, unit_price: '0.125', amount: '3' }],
} as unknown as Invoice

describe('toFormValues', () => {
  it('shows money with two decimals, quantities as read', () => {
    const values = toFormValues(invoice)

    expect([values.subtotal, values.vat, values.total]).toEqual(['4.28', '0.22', '4.50'])
    expect(values.lines.map((l) => [l.qty, l.unit_price, l.amount])).toEqual([
      ['2.0', '0.75', '1.50'],
      ['', '0.125', '3.00'],
    ])
  })

  it('leaves missing amounts empty', () => {
    expect(toFormValues({ ...invoice, vat: null }).vat).toBe('')
  })

  it('sends the same values back', () => {
    const sent = toInvoiceInput(toFormValues(invoice))

    expect(sent.total).toBe('4.50')
    expect(sent.lines?.[1]?.unit_price).toBe('0.125')
  })
})
