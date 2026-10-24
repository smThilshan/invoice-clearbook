import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

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
        message: `The supplier VAT registration no printed on the document is ${PRINTED_TRN}, not 100444555666003.`,
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
    cleaned_descriptions: 0,
    repaired_unit_prices: 0,
    updated_at: '2026-10-21T08:01:00Z',
    corrections: [],
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
  vi.restoreAllMocks()
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
    expect(screen.getByLabelText('Supplier VAT registration no')).toHaveAttribute('aria-invalid', 'true')
    const summary = screen.getByRole('region', { name: 'Document issues' })
    expect(within(summary).getByText(/supplier VAT registration no printed on the document is/))
      .toBeInTheDocument()  // also under the field itself
    expect(within(screen.getByRole('region', { name: 'Document issues' })).getByText(/looks blurry/))
      .toBeInTheDocument()
    expect(screen.getByText('Checked')).toBeInTheDocument() // invoice_date already verified
    expect(within(screen.getByRole('listitem', { name: 'Line 2' })).getByText('Choose an account for this line.'))
      .toBeInTheDocument()
  })

  it('adds charges printed outside the lines as lines, with the subtotal, in one click', async () => {
    const charged = detail({
      invoice: { ...detail().invoice!, total: '1065.99' },
      issues: [{ code: 'total_has_charges', severity: 'ERROR', field: 'total',
        message: 'The total includes charge printed outside the lines: Delivery charge 15.99.',
        suggestion: null, charges: [{ label: 'Delivery charge', amount: '15.99' }] }],
    })  // prettier-ignore
    const api = await open(backend(charged))
    const user = userEvent.setup()

    const note = screen.getByRole('note', { name: 'Charges outside the lines' })
    expect(note).toHaveTextContent('Delivery charge 15.99')
    await user.click(within(note).getByRole('button', { name: 'Add it as lines' }))
    await user.click(screen.getByRole('button', { name: 'Save & re-check' }))

    await waitFor(() => expect(sent(api)).toBeDefined())
    const lines = sent(api).invoice.lines as { description: string; amount: string }[]
    expect(lines.at(-1)).toMatchObject({ description: 'Delivery charge', amount: '15.99' })
    expect(sent(api).invoice.subtotal).toBe('1015.99')
    expect(screen.queryByRole('note', { name: 'Charges outside the lines' })).not.toBeInTheDocument()
  })

  it('lists every issue at the top and jumps to its field', async () => {
    await open()
    const user = userEvent.setup()

    const summary = screen.getByRole('region', { name: 'Document issues' })
    expect(summary).toHaveTextContent('2 errors · 2 warnings')
    await user.click(within(summary).getByText('Supplier VAT registration no:'))

    expect(screen.getByLabelText('Supplier VAT registration no')).toHaveFocus()
  })

  it('goes back to the list it was opened from', async () => {
    backend()
    const user = userEvent.setup()
    const router = renderApp(`/documents/${ID}`, { from: '/queue?client=cafe&queue=EXCEPTION', ids: [ID] })

    await user.click(await screen.findByRole('button', { name: 'Back' }))

    await waitFor(() => expect(router.state.location.search).toBe('?client=cafe&queue=EXCEPTION'))
  })

  it('names the line columns once above the lines', async () => {
    await open()

    const lines = screen.getByRole('region', { name: 'Lines' })
    for (const heading of ['Description', 'Qty', 'Unit price', 'Amount']) {
      expect(within(lines).getByText(heading, { selector: 'span' })).toBeInTheDocument()
    }
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
    await user.click(screen.getByRole('checkbox', { name: 'Supplier VAT registration no checked against the original' }))
    await user.click(screen.getByRole('button', { name: 'Save & re-check' }))

    await waitFor(() => expect(sent(api)).toBeDefined())
    expect(sent(api).invoice.vendor_trn).toBe(PRINTED_TRN)
    expect(sent(api).confirmed).toEqual(['vendor_trn'])
    expect(await screen.findByRole('status')).toHaveTextContent('Saved and checked again: now Ready')
  })

  it('offers the VAT type the amounts show and sends it', async () => {
    const vatType = detail({
      invoice: { ...detail().invoice!, amount_type: 'INCLUSIVE' },
      issues: [{ code: 'vat_type_mismatch', severity: 'WARNING', field: 'amount_type',
        message: 'The lines plus VAT add up to the total, so the line amounts are before VAT.',
        suggestion: 'EXCLUSIVE' }],
    })  // prettier-ignore
    const api = await open(backend(vatType))
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Use: VAT added on top' }))
    await user.click(screen.getByRole('button', { name: 'Save & re-check' }))

    await waitFor(() => expect(sent(api)).toBeDefined())
    expect(sent(api).invoice.amount_type).toBe('EXCLUSIVE')
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

  it('codes a line from the client accounts and says where a code came from', async () => {
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

  it('says when item codes were removed from descriptions', async () => {
    await open(backend(detail({ cleaned_descriptions: 2 })))

    expect(screen.getByText(/Item codes or quantity prefixes removed from 2 descriptions/)).toBeInTheDocument()
  })

  it('says when unit prices were worked out from line totals', async () => {
    await open(backend(detail({ repaired_unit_prices: 2 })))

    expect(screen.getByText(/Unit price worked out from the line total on 2 lines/)).toBeInTheDocument()
  })

  it('says why a bill came back from posting', async () => {
    const reason = "Not sent to Xero: line 1: account 5010 is not in Xero's chart of accounts: choose another"
    await open(backend(detail({ failure_reason: reason })))

    expect(screen.getByText('Sent back: not posted')).toBeInTheDocument()
    expect(screen.getByText(reason)).toBeInTheDocument()
  })

  it('cannot change a document that is no longer in review', async () => {
    await open(backend(detail({ status: 'POSTED' })))

    expect(screen.getByText(/it can no longer be changed/)).toBeInTheDocument()
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

describe('reject', () => {
  function rejectBackend() {
    return mockApi({
      '/health/ready': READY,
      '/clients': [],
      [`/documents/${ID}`]: detail(),
      [`POST /documents/${ID}/reject`]: detail({ status: 'REJECTED', updated_at: '2026-10-21T08:03:00Z' }),
      '/clients/cafe/coding-options': OPTIONS,
    })
  }

  it('brings a rejected document back to review', async () => {
    const api = mockApi({
      '/health/ready': READY, '/clients': [], '/clients/cafe/coding-options': OPTIONS,
      [`/documents/${ID}`]: detail({ status: 'REJECTED' }),
      [`POST /documents/${ID}/restore`]: detail(),
    })  // prettier-ignore
    const user = userEvent.setup()
    renderApp(`/documents/${ID}`)

    const panel = await screen.findByRole('region', { name: 'Rejected document' })
    await user.click(within(panel).getByRole('button', { name: 'Bring back to review' }))

    await waitFor(() => expect(api.calls).toContain(`POST /documents/${ID}/restore`))
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Rejected document' })).not.toBeInTheDocument())
  })

  it('deletes a rejected document permanently only after asking', async () => {
    const api = mockApi({
      '/health/ready': READY, '/clients': [], '/clients/cafe/coding-options': OPTIONS,
      [`/documents/${ID}`]: detail({ status: 'REJECTED' }),
      [`DELETE /documents/${ID}`]: {},
      '/documents': { documents: [], total: 0, counts: { EXCEPTION: 0, CHECK: 0, READY: 0 } },
    })  // prettier-ignore
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    const user = userEvent.setup()
    renderApp(`/documents/${ID}`)

    const panel = await screen.findByRole('region', { name: 'Rejected document' })
    await user.click(within(panel).getByRole('button', { name: 'Delete permanently' }))
    expect(api.calls).not.toContain(`DELETE /documents/${ID}`)  // said no
    await user.click(within(panel).getByRole('button', { name: 'Delete permanently' }))

    await waitFor(() => expect(api.calls).toContain(`DELETE /documents/${ID}`))
    expect(confirm.mock.calls[0]?.[0]).toMatch(/cannot be undone/)
  })

  it('rejects with a reason from the list', async () => {
    const api = rejectBackend()
    const user = userEvent.setup()
    renderApp(`/documents/${ID}`)

    await user.click(await screen.findByRole('button', { name: 'Reject…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Reject this document' })
    expect(within(dialog).getByRole('button', { name: 'Reject document' })).toBeDisabled()
    await user.click(within(dialog).getByRole('radio', { name: /Duplicate/ }))
    await user.click(within(dialog).getByRole('button', { name: 'Reject document' }))

    await waitFor(() =>
      expect(api.bodies[`POST /documents/${ID}/reject`]).toEqual({ reason: 'DUPLICATE', note: null }),
    )
    expect(await screen.findByRole('region', { name: 'Rejected document' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('needs a note for the reason Other', async () => {
    const api = rejectBackend()
    const user = userEvent.setup()
    renderApp(`/documents/${ID}`)

    await user.click(await screen.findByRole('button', { name: 'Reject…' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('radio', { name: /Other/ }))
    const confirm = within(dialog).getByRole('button', { name: 'Reject document' })
    expect(confirm).toBeDisabled()
    await user.type(within(dialog).getByLabelText('Note'), '  Personal purchase  ')
    await user.click(confirm)

    await waitFor(() =>
      expect(api.bodies[`POST /documents/${ID}/reject`]).toEqual({
        reason: 'OTHER', note: 'Personal purchase',
      }),
    )  // prettier-ignore
  })

  it('is not offered for a parked document', async () => {
    await open(backend(detail({ status: 'NON_POSTABLE', document_type: 'NON_POSTABLE' })))

    expect(screen.getByRole('button', { name: 'Save & re-check' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reject…' })).not.toBeInTheDocument()
  })
})

describe('unsaved changes', () => {
  it('asks before leaving with unsaved edits, and stays when told to', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = userEvent.setup()
    backend()
    const router = renderApp(`/documents/${ID}`)
    await screen.findByRole('form', { name: 'Review' })

    await user.type(screen.getByLabelText('Supplier'), ' X')
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument()
    void router.navigate('/queue')

    await waitFor(() =>
      expect(confirm).toHaveBeenCalledWith('You have unsaved changes. Leave without saving?'),
    )
    expect(router.state.location.pathname).toBe(`/documents/${ID}`)
    expect(screen.getByLabelText('Supplier')).toHaveValue('Fresh Farms Trading LLC X')
  })

  it('leaves freely when nothing was changed', async () => {
    const confirm = vi.spyOn(window, 'confirm')
    backend()
    const router = renderApp(`/documents/${ID}`)
    await screen.findByRole('form', { name: 'Review' })

    await router.navigate('/queue')

    expect(confirm).not.toHaveBeenCalled()
    expect(router.state.location.pathname).toBe('/queue')
  })
})

describe('field highlight', () => {
  const PLACES: Record<string, unknown> = {
    'amount:1050.00': [{ page: 1, box: [0.6, 0.8, 0.7, 0.82], exact: true }],
    'amount:1060.00': [],
    'trn:100444555666003': [{ page: 2, box: [0.1, 0.2, 0.4, 0.22], exact: true }],
    'text:Fresh Farms Trading LLC': [{ page: 1, box: [0.1, 0.05, 0.5, 0.08], exact: false }],
    'amount:400.00': [
      { page: 1, box: [0.6, 0.5, 0.7, 0.52], exact: true },
      { page: 2, box: [0.6, 0.1, 0.7, 0.12], exact: true },
    ],
  }

  function locatingBackend() {
    return mockApi({
      '/health/ready': READY,
      '/clients': [],
      [`/documents/${ID}`]: detail(),
      '/clients/cafe/coding-options': OPTIONS,
      [`/documents/${ID}/locate`]: (request: Request) => {
        const params = new URL(request.url).searchParams
        return { places: PLACES[`${params.get('kind')}:${params.get('value')}`] ?? [] }
      },
    })
  }

  async function openLocating() {
    const api = locatingBackend()
    renderApp(`/documents/${ID}`)
    await screen.findByRole('form', { name: 'Review' })
    return api
  }

  it('highlights where the focused field is printed', async () => {
    await openLocating()
    const user = userEvent.setup()

    await user.click(screen.getByLabelText('Total'))

    const [box] = await screen.findAllByTestId('highlight')
    expect(box).toHaveStyle({ left: '59.6%', top: '79.6%' })
    expect(box).toHaveAttribute('data-exact', 'true')
    expect(screen.getByText('Total:').parentElement).toHaveTextContent('Total: 1 place')
  })

  it('turns to the page where the value is printed', async () => {
    await openLocating()
    const user = userEvent.setup()

    await user.click(screen.getByLabelText('Supplier VAT registration no'))

    await screen.findByTestId('highlight')
    expect(screen.getByText('Page 2 of 2')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Page 2 of the document' })).toBeInTheDocument()
  })

  it('says when a value is not printed, and follows what is typed', async () => {
    const api = await openLocating()
    const user = userEvent.setup()

    await user.click(screen.getByLabelText('Total'))
    await screen.findByTestId('highlight')
    await user.clear(screen.getByLabelText('Total'))
    await user.type(screen.getByLabelText('Total'), '1060.00')

    expect(await screen.findByText(/not found in the page text/)).toBeInTheDocument()
    expect(screen.queryByTestId('highlight')).not.toBeInTheDocument()
    const lookups = api.calls.filter((call) => call.includes('/locate?'))
    expect(lookups.some((call) => call.includes('value=1060.00'))).toBe(true)
    expect(lookups.some((call) => call.includes('value=106&'))).toBe(false) // waits for a pause
  })

  it('marks names found only approximately', async () => {
    await openLocating()
    const user = userEvent.setup()

    await user.click(screen.getByLabelText('Supplier'))

    expect(await screen.findByTestId('highlight')).toHaveAttribute('data-exact', 'false')
    expect(screen.getByText(/dashed: most of the words/)).toBeInTheDocument()
  })

  it('highlights line values and offers the other pages they are on', async () => {
    await openLocating()
    const user = userEvent.setup()
    const line = within(screen.getByRole('listitem', { name: 'Line 2' }))

    await user.click(line.getByLabelText('Amount'))

    expect(await screen.findByText('Line 2 amount:')).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'also on page 2' }))
    expect(screen.getByText('Page 2 of 2')).toBeInTheDocument()
    expect(screen.getAllByTestId('highlight')).toHaveLength(1)
  })
})

describe('corrections', () => {
  it('shows what the AI read under the fields that were changed', async () => {
    await open(backend(detail({
      corrections: [
        { field: 'total', model_value: '1060.00', final_value: '1050.00' },
        { field: 'invoice_date', model_value: '4520-05-04', final_value: '2026-05-04' },
        { field: 'vendor_name', model_value: null, final_value: 'Fresh Farms Trading LLC' },
        { field: 'lines[1].amount', model_value: '410.00', final_value: '400.00' },
        { field: 'lines[1].account_code', model_value: '5000', final_value: '5010' },
      ],
    })))  // prettier-ignore

    expect(screen.getByText('AI read: 1,060.00')).toBeInTheDocument()
    expect(screen.getByText('AI read: 4 May 4520')).toBeInTheDocument()
    expect(screen.getByText('AI read: nothing')).toBeInTheDocument()
    const line = within(screen.getByRole('listitem', { name: 'Line 2' }))
    expect(line.getByText('Amount: AI read: 410.00')).toBeInTheDocument()
    expect(line.getByText('Account: suggested: 5000')).toBeInTheDocument()
  })
})

describe('approve', () => {
  const CLEAN = detail({ queue: 'CHECK', errors: 0, warnings: 1,
    issues: [{ code: 'vendor_new', severity: 'WARNING', field: 'vendor_name',
      message: 'New supplier.', suggestion: null }] })  // prettier-ignore

  function approveBackend(answer: unknown) {
    return mockApi({
      '/health/ready': READY,
      '/clients': [],
      [`/documents/${ID}`]: CLEAN,
      [`POST /documents/${ID}/approve`]: answer,
      '/clients/cafe/coding-options': OPTIONS,
    })
  }

  it('approves a document opened directly, then goes to its client\'s queue', async () => {
    const api = approveBackend(detail({ status: 'APPROVED', updated_at: '2026-10-21T09:00:00Z' }))
    const user = userEvent.setup()
    const router = renderApp(`/documents/${ID}`)

    await user.click(await screen.findByRole('button', { name: 'Approve' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/queue'))
    expect(router.state.location.search).toBe('?client=cafe')
    expect(api.calls).toContain(`POST /documents/${ID}/approve`)
    expect(await screen.findByRole('status', { name: 'Done' })).toHaveTextContent(/^Approved: /)
  })

  it('approves and opens the next document of the list it came from', async () => {
    const api = approveBackend(detail({ status: 'APPROVED', updated_at: '2026-10-21T09:00:00Z' }))
    const user = userEvent.setup()
    const router = renderApp(`/documents/${ID}`, { from: '/queue?client=cafe&queue=CHECK', ids: [ID, 'next-doc'] })

    expect(await screen.findByRole('navigation', { name: 'Documents in this list' })).toHaveTextContent('1 of 2')
    await user.click(screen.getByRole('button', { name: 'Approve & next' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/documents/next-doc'))
    expect(router.state.location.state).toMatchObject({ from: '/queue?client=cafe&queue=CHECK', flash: expect.stringMatching(/^Approved: /) })
    expect(api.calls).toContain(`POST /documents/${ID}/approve`)
  })

  it('cannot approve while errors remain', async () => {
    await open() // the default document has errors

    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()
    expect(screen.getByText('Fix the errors first')).toBeInTheDocument()
  })

  it('asks to save changes before approving', async () => {
    approveBackend(CLEAN)
    const user = userEvent.setup()
    renderApp(`/documents/${ID}`)

    await user.type(await screen.findByLabelText('Supplier'), ' X')

    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()
    expect(screen.getByText('Save your changes first')).toBeInTheDocument()
  })

  it('says why the server refused', async () => {
    approveBackend(reply(422, { detail: '1 error must be fixed before approval' }))
    const user = userEvent.setup()
    renderApp(`/documents/${ID}`)

    await user.click(await screen.findByRole('button', { name: 'Approve' }))

    expect(await screen.findByText('Not approved')).toBeInTheDocument()
    expect(screen.getByText('1 error must be fixed before approval')).toBeInTheDocument()
  })
})
