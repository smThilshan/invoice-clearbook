import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import type { CodingOptions, DocumentDetail, Readiness } from '@/api/client'
import { type FakeApi, mockApi, renderApp, reply } from '@/test/render'

const READY: Readiness = { status: 'ok', database: 'ok', redis: 'ok', model: 'ok' }
const ID = 'doc-1'
const PRINTED_TRN = '100111222333003'

function detail(changes: Partial<DocumentDetail> = {}): DocumentDetail {
  return {
    document_id: ID,
    client_id: 'cafe',
    run_id: 'run-1',
    file: 'inv-1.pdf',
    status: 'IN_REVIEW',
    queue: 'EXCEPTION',
    document_type: 'BILL',
    vendor_name: 'Fresh Farms Trading LLC',
    invoice_number: 'INV-291',
    invoice_date: '2026-05-04',
    total: '1050.00',
    currency: 'AED',
    errors: 1,
    warnings: 1,
    created_at: '2026-10-21T08:00:00Z',
    source_path: 'May/May 4/inv-1.pdf',
    mime_type: 'application/pdf',
    folder_date: '2026-05-04',
    page_count: 2,
    invoice: {
      document_type: 'BILL',
      vendor_name: 'Fresh Farms Trading LLC',
      vendor_trn: '100444555666003',
      invoice_number: 'INV-291',
      invoice_date: '2026-05-04',
      due_date: null,
      currency: 'AED',
      amount_type: 'EXCLUSIVE',
      subtotal: '1000.00',
      vat: '50.00',
      total: '1050.00',
      recipient_name: 'Cafe Example',
      recipient_trn: '100444555666003',
      po_number: null,
      original_invoice_ref: null,
      lines: [
        { description: 'Milk', qty: '10', unit_price: '60.00', amount: '600.00',
          account_code: '5000', tax_type: 'STANDARD_5' },
        { description: 'Cheese', qty: null, unit_price: null, amount: '400.00',
          account_code: null, tax_type: 'STANDARD_5' },
      ],
      evidence: {},
    },
    issues: [
      { code: 'trn_differs_from_printed', severity: 'ERROR', field: 'vendor_trn',
        message: `The supplier TRN printed on the document is ${PRINTED_TRN}, not 100444555666003.`,
        suggestion: PRINTED_TRN },
      { code: 'type_maybe_receipt', severity: 'WARNING', field: 'document_type',
        message: 'Read as a bill to pay, but the document shows a payment.', suggestion: null },
      { code: 'photo_poor', severity: 'WARNING', field: null,
        message: 'Page 1 looks blurry: values may be misread.', suggestion: null },
      { code: 'account_missing', severity: 'ERROR', field: 'lines[1].account_code',
        message: 'Choose an account for this line.', suggestion: null },
    ],
    coding: [
      { line: 0, account_code: '5000', account_source: 'model' },
      { line: 1, account_code: null, account_source: 'none' },
    ],
    verified_fields: ['invoice_date'],
    contact_id: null,
    failure_reason: null,
    updated_at: '2026-10-21T08:01:00Z',
    ...changes,
  } as DocumentDetail
}

const OPTIONS: CodingOptions = {
  accounts: [
    { code: '5000', name: 'Food Cost', account_class: 'EXPENSE' },
    { code: '5010', name: 'Beverage Cost', account_class: 'EXPENSE' },
  ],
  tax_types: [
    { tax_type: 'STANDARD_5', code: 'STD5' },
    { tax_type: 'ZERO', code: 'ZERO' },
  ],
}

const realFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = realFetch
})

function backend(document = detail(), saved = detail({ queue: 'READY', errors: 0, warnings: 0,
  issues: [], updated_at: '2026-10-21T08:02:00Z' })): FakeApi {  // prettier-ignore
  return mockApi({
    '/health/ready': READY,
    '/clients': [],
    [`/documents/${ID}`]: document,
    [`PATCH /documents/${ID}`]: saved,
    '/clients/cafe/coding-options': OPTIONS,
  })
}

async function open(api = backend()) {
  renderApp(`/documents/${ID}`)
  await screen.findByRole('form', { name: 'Review' })
  return api
}

function sent(api: FakeApi) {
  return api.bodies[`PATCH /documents/${ID}`] as { invoice: Record<string, unknown>; confirmed: string[] }
}

describe('review screen', () => {
  it('shows the page and the extracted values with their issues', async () => {
    await open()

    expect(screen.getByRole('img', { name: 'Page 1 of the document' })).toHaveAttribute(
      'src', `/api/documents/${ID}/pages/1/image`,
    )  // prettier-ignore
    expect(screen.getByText('Page 1 of 2')).toBeInTheDocument()
    expect(screen.getByLabelText('Supplier')).toHaveValue('Fresh Farms Trading LLC')
    expect(screen.getByLabelText('Total')).toHaveValue('1050.00')
    expect(screen.getByLabelText('Supplier TRN')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText(/supplier TRN printed on the document is/)).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Document issues' })).getByText(/looks blurry/))
      .toBeInTheDocument()
    expect(screen.getByText('Checked')).toBeInTheDocument() // invoice_date already verified
    expect(screen.getByText('Choose an account for this line.')).toBeInTheDocument()
  })

  it('pages through the document', async () => {
    const user = userEvent.setup()
    await open()

    await user.click(screen.getByRole('button', { name: 'Next page' }))

    expect(screen.getByRole('img', { name: 'Page 2 of the document' })).toHaveAttribute(
      'src', `/api/documents/${ID}/pages/2/image`,
    )  // prettier-ignore
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
  })

  it('offers the printed TRN and sends it, with the fields ticked as checked', async () => {
    const api = await open()
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: `Use printed value ${PRINTED_TRN}` }))
    await user.click(screen.getByRole('checkbox', { name: 'Supplier TRN checked against the original' }))
    await user.click(screen.getByRole('button', { name: 'Save & re-check' }))

    await waitFor(() => expect(sent(api)).toBeDefined())
    expect(sent(api).invoice.vendor_trn).toBe(PRINTED_TRN)
    expect(sent(api).confirmed).toEqual(['vendor_trn'])
    expect(await screen.findByRole('status')).toHaveTextContent('Saved and checked again: now Ready')
  })

  it('sends amounts exactly as typed and empty fields as null', async () => {
    const api = await open()
    const user = userEvent.setup()

    await user.clear(screen.getByLabelText('Total'))
    await user.type(screen.getByLabelText('Total'), '1,050.10')
    await user.clear(screen.getByLabelText('Customer'))
    await user.click(screen.getByRole('button', { name: 'Save & re-check' }))

    await waitFor(() => expect(sent(api)).toBeDefined())
    expect(sent(api).invoice).toMatchObject({ total: '1050.10', recipient_name: null, due_date: null })
    expect((sent(api).invoice.lines as unknown[])[0]).toEqual({
      description: 'Milk', qty: '10', unit_price: '60.00', amount: '600.00',
      account_code: '5000', tax_type: 'STANDARD_5',
    })  // prettier-ignore
  })

  it('checks the totals while typing', async () => {
    const user = userEvent.setup()
    await open()
    const totals = screen.getByRole('region', { name: 'Totals check' })
    expect(totals).toHaveTextContent('Lines add up to the subtotal1,000.00')

    const amount = within(screen.getByRole('listitem', { name: 'Line 2' })).getByLabelText('Amount')
    await user.clear(amount)
    await user.type(amount, '410')

    expect(totals).toHaveTextContent('1,010.00 vs 1,000.00, off by 10.00')
    expect(totals).toHaveTextContent('Subtotal + VAT = total1,050.00')
  })

  it('adds and removes lines', async () => {
    const api = await open()
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Remove line 1' }))
    await user.click(screen.getByRole('button', { name: 'Add line' }))
    const added = within(screen.getByRole('listitem', { name: 'Line 2' }))
    await user.type(added.getByLabelText('Description'), 'Butter')
    await user.type(added.getByLabelText('Amount'), '25.5')
    await user.click(screen.getByRole('button', { name: 'Save & re-check' }))

    await waitFor(() => expect(sent(api)).toBeDefined())
    const lines = sent(api).invoice.lines as { description: string; amount: string }[]
    expect(lines.map((line) => [line.description, line.amount])).toEqual([
      ['Cheese', '400.00'], ['Butter', '25.5'],
    ])  // prettier-ignore
  })

  it('codes a line from the restaurant accounts and says where a code came from', async () => {
    const api = await open()
    const user = userEvent.setup()
    expect(within(screen.getByRole('listitem', { name: 'Line 1' })).getByText('AI suggestion'))
      .toBeInTheDocument()

    await user.click(screen.getByRole('combobox', { name: 'Account for line 2' }))
    await user.click(await screen.findByRole('option', { name: '5010 Beverage Cost' }))
    await user.click(screen.getByRole('button', { name: 'Save & re-check' }))

    await waitFor(() => expect(sent(api)).toBeDefined())
    expect((sent(api).invoice.lines as { account_code: string }[])[1]?.account_code).toBe('5010')
  })

  it('shows the original invoice field for credit notes only', async () => {
    const user = userEvent.setup()
    await open()
    expect(screen.queryByLabelText('Original invoice')).not.toBeInTheDocument()

    await user.click(screen.getByRole('combobox', { name: 'Document type' }))
    await user.click(await screen.findByRole('option', { name: 'Credit note' }))

    expect(screen.getByLabelText('Original invoice')).toBeInTheDocument()
  })

  it('asks for the document by hand when the extraction could not be read', async () => {
    await open(backend(detail({ invoice: null })))

    expect(screen.getByText(/Enter the document by hand/)).toBeInTheDocument()
    expect(screen.getByLabelText('Supplier')).toHaveValue('')
  })

  it('cannot change a document that is no longer in review', async () => {
    await open(backend(detail({ status: 'REJECTED' })))

    expect(screen.getByText(/rejected: it can no longer be changed/)).toBeInTheDocument()
    expect(screen.getByLabelText('Supplier')).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save & re-check' })).not.toBeInTheDocument()
  })

  it('says why a save failed', async () => {
    mockApi({
      '/health/ready': READY,
      '/clients': [],
      [`/documents/${ID}`]: detail(),
      [`PATCH /documents/${ID}`]: reply(409, { detail: 'status is no longer IN_REVIEW' }),
      '/clients/cafe/coding-options': OPTIONS,
    })
    const user = userEvent.setup()
    renderApp(`/documents/${ID}`)

    await user.click(await screen.findByRole('button', { name: 'Save & re-check' }))

    expect(await screen.findByText('Not saved')).toBeInTheDocument()
    expect(screen.getByText('status is no longer IN_REVIEW')).toBeInTheDocument()
  })
})
