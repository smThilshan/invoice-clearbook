import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { DocumentSummary, QueuePage, Readiness } from '@/api/client'
import { mockApi, renderApp, reply } from '@/test/render'

const READY: Readiness = { status: 'ok', database: 'ok', redis: 'ok', model: 'ok' }
const CLIENTS = [
  { client_id: 'cafe-al-noor', name: 'Cafe Al Noor' },
  { client_id: 'spice-route', name: 'Spice Route' },
]
const RUN_ID = '6f1c2b0e-0000-4000-8000-000000000001'

function doc(id: string, changes: Partial<DocumentSummary> = {}): DocumentSummary {
  return {
    document_id: id,
    client_id: 'cafe-al-noor',
    run_id: RUN_ID,
    file: `${id}.pdf`,
    folder: 'October 2026/21',
    status: 'IN_REVIEW',
    queue: 'READY',
    document_type: 'BILL',
    vendor_name: 'Fresh Farms Trading LLC',
    invoice_number: `INV-${id}`,
    invoice_date: '2026-10-15',
    total: '1050.00',
    currency: 'AED',
    errors: 0,
    warnings: 0,
    created_at: '2026-10-21T08:00:00Z',
    sending: null,
    external_url: null,
    not_sent: null,
    ...changes,
  }
}

const PAGE: QueuePage = {
  total: 3,
  limit: 50,
  offset: 0,
  counts: { EXCEPTION: 1, CHECK: 1, READY: 1 },
  processing: 0,
  stopped: 0,
  stopped_run_id: null,
  sending_counts: { waiting: 1, failed: 2, posted: 5, not_sent: 0 },
  documents: [
    doc('d1', { queue: 'EXCEPTION', errors: 2, warnings: 1, total: '1234567.5' }),
    doc('d2', { queue: 'CHECK', warnings: 1, vendor_name: null }),
    doc('d3'),
  ],
}

const realFetch = globalThis.fetch

beforeEach(() => localStorage.clear())
afterEach(() => {
  globalThis.fetch = realFetch
})

const RUNS = [
  { run_id: RUN_ID, folder: 'October 2026/21', folder_date: '2026-10-21',
    created_at: '2026-10-21T08:00:00Z', documents: 3 },
  { run_id: '6f1c2b0e-0000-4000-8000-000000000002', folder: '', folder_date: null,
    created_at: '2026-10-20T08:00:00Z', documents: 1 },
]  // prettier-ignore

function backend(queue: unknown = PAGE) {
  return mockApi({
    '/health/ready': READY,
    '/clients': CLIENTS,
    '/clients/cafe-al-noor/runs': RUNS,
    '/clients/spice-route/runs': [],
    '/documents': queue,
  })
}

function queueCalls(api: { calls: string[] }) {
  return api.calls
    .filter((call) => call.startsWith('GET /documents?'))
    .map((call) => Object.fromEntries(new URLSearchParams(call.split('?')[1])))
}

describe('review queue', () => {
  it('asks for a client first', async () => {
    const api = backend()
    renderApp('/queue')

    expect(await screen.findByText('Choose an organization or business to see its documents.')).toBeInTheDocument()
    expect(queueCalls(api)).toEqual([])
  })

  it('lists the documents in the order the server gives, with category and issues', async () => {
    const api = backend()
    renderApp('/queue?client=cafe-al-noor')

    const table = await screen.findByRole('table', { name: 'Documents' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((row) => within(row).getAllByRole('cell')[0]?.textContent)).toEqual([
      'Exception', 'Check', 'Ready',
    ])  // prettier-ignore
    expect(rows[0]).toHaveTextContent('AED 1,234,567.5') // exact text, separators only
    expect(rows[0]).toHaveTextContent('2 errors1 warning')
    expect(rows[0]).toHaveTextContent('15 Oct 2026')
    expect(rows[0]).toHaveTextContent('Bill · October 2026/21 / d1.pdf') // type, folder and file
    expect(rows[1]).toHaveTextContent('Supplier not read')
    expect(rows[2]).toHaveTextContent('None') // no issues
    expect(screen.getByText('1–3 of 3')).toBeInTheDocument()
    expect(queueCalls(api)[0]).toEqual({
      client_id: 'cafe-al-noor', status: 'IN_REVIEW', limit: '50', offset: '0',
    })  // prettier-ignore
  })

  it('filters by category and parked list, kept in the address', async () => {
    const api = backend()
    const user = userEvent.setup()
    const router = renderApp('/queue?client=cafe-al-noor')

    await screen.findByRole('table', { name: 'Documents' })
    const categories = screen.getByRole('group', { name: 'Category' })
    await user.click(within(categories).getByRole('button', { name: /^Exception/ }))
    await waitFor(() => expect(router.state.location.search).toBe('?client=cafe-al-noor&queue=EXCEPTION'))
    await user.click(screen.getByRole('button', { name: 'Parked (not postable)' }))

    await waitFor(() =>
      expect(queueCalls(api).at(-1)).toMatchObject({ queue: 'EXCEPTION', status: 'NON_POSTABLE' }),
    )
    expect(within(categories).getByRole('button', { name: /^Exception/ })).toHaveAttribute('aria-pressed', 'true')
  })

  it('filters by category from the overview cards too, and a second click shows all again', async () => {
    const api = backend()
    const user = userEvent.setup()
    const router = renderApp('/queue?client=cafe-al-noor')

    const overview = await screen.findByRole('region', { name: 'Queue overview' })
    const card = within(overview).getByRole('button', { name: /^Exception/ })
    await user.click(card)

    await waitFor(() => expect(router.state.location.search).toBe('?client=cafe-al-noor&queue=EXCEPTION'))
    await waitFor(() => expect(queueCalls(api).at(-1)).toMatchObject({ queue: 'EXCEPTION' }))
    expect(within(overview).getByRole('button', { name: /^Exception/ })).toHaveAttribute('aria-pressed', 'true')
    await user.click(within(overview).getByRole('button', { name: /^Exception/ }))
    await waitFor(() => expect(router.state.location.search).toBe('?client=cafe-al-noor'))
  })

  it('shows one run when coming from the run screen, and can show all again', async () => {
    const api = backend()
    const user = userEvent.setup()
    const router = renderApp(`/queue?client=cafe-al-noor&run=${RUN_ID}`)

    await screen.findByRole('table', { name: 'Documents' })
    expect(queueCalls(api)[0]).toMatchObject({ run_id: RUN_ID })
    const run = screen.getByRole('combobox', { name: 'Run' })
    await waitFor(() => expect(run).toHaveTextContent('October 2026/21 · 3 documents'))
    await user.click(run)
    await user.click(await screen.findByRole('option', { name: 'All runs' }))

    await waitFor(() => expect(router.state.location.search).toBe('?client=cafe-al-noor'))
    expect(queueCalls(api).at(-1)).not.toHaveProperty('run_id')
  })

  it('picks one of the latest runs', async () => {
    const api = backend()
    const user = userEvent.setup()
    renderApp('/queue?client=cafe-al-noor')

    await user.click(await screen.findByRole('combobox', { name: 'Run' }))
    await user.click(await screen.findByRole('option', { name: /^All folders · 1 document/ }))

    await waitFor(() =>
      expect(queueCalls(api).at(-1)).toMatchObject({ run_id: '6f1c2b0e-0000-4000-8000-000000000002' }),
    )
  })

  it('pages through long queues', async () => {
    const api = backend((request: Request) => {
      const offset = Number(new URL(request.url).searchParams.get('offset'))
      return { ...PAGE, total: 120, offset, documents: [doc(`p${offset}`)] }
    })
    const user = userEvent.setup()
    renderApp('/queue?client=cafe-al-noor')

    expect(await screen.findByText('1–50 of 120')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /previous/i })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: /next/i }))
    await user.click(await screen.findByRole('button', { name: /next/i }))

    expect(await screen.findByText('101–120 of 120')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /next/i })).toBeDisabled()
    expect(queueCalls(api).at(-1)).toMatchObject({ offset: '100' })
  })

  it('remembers the last client on this computer', async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/queue')

    await user.click(await screen.findByRole('combobox', { name: 'Organization or Business' }))
    await user.click(await screen.findByRole('option', { name: 'Spice Route' }))
    await screen.findByRole('table', { name: 'Documents' })

    expect(localStorage.getItem('clearbook.lastClient')).toBe('spice-route')
  })

  it('opens a remembered client straight away (also one remembered under the old name)', async () => {
    localStorage.setItem('clearbook.lastRestaurant', 'spice-route')
    const api = backend()
    renderApp('/queue')

    await screen.findByRole('table', { name: 'Documents' })
    expect(queueCalls(api)[0]).toMatchObject({ client_id: 'spice-route' })
  })

  it('opens a document from its row', async () => {
    backend()
    const user = userEvent.setup()
    const router = renderApp('/queue?client=cafe-al-noor')

    const table = await screen.findByRole('table', { name: 'Documents' })
    await user.click(within(table).getAllByRole('row')[3]!.querySelector('td')!)

    await waitFor(() => expect(router.state.location.pathname).toBe('/documents/d3'))
    expect(router.state.location.state).toEqual({
      from: '/queue?client=cafe-al-noor', ids: ['d1', 'd2', 'd3'],
    })  // prettier-ignore
    expect(await screen.findByText('This document could not be loaded')).toBeInTheDocument()
  })

  it('says when there is nothing to review, and how to get documents', async () => {
    backend({ ...PAGE, total: 0, documents: [], counts: {} })
    renderApp('/queue?client=cafe-al-noor')

    expect(await screen.findByText('Nothing to review.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Start a run' })).toHaveAttribute('href', '/run')
  })

  it('shows how many documents each category holds', async () => {
    backend({ ...PAGE, counts: { EXCEPTION: 3, CHECK: 4 } })
    renderApp('/queue?client=cafe-al-noor')

    await screen.findByRole('table', { name: 'Documents' })
    const categories = screen.getByRole('group', { name: 'Category' })
    expect(within(categories).getByRole('button', { name: /^All/ })).toHaveTextContent('All7')
    expect(within(categories).getByRole('button', { name: /^Exception/ })).toHaveTextContent('Exception3')
    expect(within(categories).getByRole('button', { name: /^Ready/ })).toHaveTextContent('Ready0')
  })

  it('searches by supplier or number, kept in the address', async () => {
    const api = backend()
    const user = userEvent.setup()
    const router = renderApp('/queue?client=cafe-al-noor')

    await user.type(await screen.findByRole('searchbox', { name: 'Search' }), 'fresh')

    await waitFor(() => expect(router.state.location.search).toBe('?client=cafe-al-noor&q=fresh'))
    await waitFor(() => expect(queueCalls(api).at(-1)).toMatchObject({ search: 'fresh' }))
  })

  it('says what is still being processed, and offers to clear filters that match nothing', async () => {
    backend({ ...PAGE, total: 0, documents: [], counts: {}, processing: 2 })
    const user = userEvent.setup()
    const router = renderApp(`/queue?client=cafe-al-noor&queue=READY&run=${RUN_ID}`)

    expect(await screen.findByRole('note', { name: 'Still processing' })).toHaveTextContent(
      '2 documents are still being processed',
    )
    expect(screen.getByRole('link', { name: 'See progress' })).toHaveAttribute('href', `/run/${RUN_ID}`)
    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    await waitFor(() => expect(router.state.location.search).toBe('?client=cafe-al-noor'))
  })

  it('says which documents wait for a stopped run, with no spinner', async () => {
    backend({ ...PAGE, processing: 0, stopped: 2, stopped_run_id: RUN_ID })
    renderApp('/queue?client=cafe-al-noor')

    expect(await screen.findByRole('note', { name: 'Run stopped' })).toHaveTextContent(
      '2 documents were not finished because the run was stopped',
    )
    expect(screen.getByRole('link', { name: 'Open the run to resume' })).toHaveAttribute('href', `/run/${RUN_ID}`)
    expect(screen.queryByRole('note', { name: 'Still processing' })).not.toBeInTheDocument()
  })

  it('says why the queue could not be loaded', async () => {
    backend(reply(404, { detail: 'organization or business not found' }))
    renderApp('/queue?client=gone')

    expect(await screen.findByText('The queue could not be loaded')).toBeInTheDocument()
    expect(screen.getByText('organization or business not found')).toBeInTheDocument()
  })
})

describe('approved documents (decision 70)', () => {
  const XERO_STATUS = { provider_name: 'Xero' }
  const SENT: QueuePage = {
    ...PAGE, total: 2, counts: {},
    documents: [
      doc('p1', { status: 'POSTED', sending: { state: 'posted', reason: null, next_try_at: null,
                  can_send_back: false, as_expense: false },
                  external_url: 'https://go.xero.com/AccountsPayable/View.aspx?InvoiceID=bill-1' }),
      doc('f1', { status: 'POSTING', sending: { state: 'failed', reason: 'made-up error',
                  next_try_at: null, can_send_back: true, as_expense: false } }),
    ],
  }  // prettier-ignore

  it('lists where each one is, links the posted one, and counts what could not be sent', async () => {
    const api = mockApi({
      '/health/ready': READY, '/clients': CLIENTS, '/clients/cafe-al-noor/runs': RUNS,
      '/clients/cafe-al-noor/accounting': XERO_STATUS, '/documents': SENT,
    })  // prettier-ignore
    const user = userEvent.setup()
    const router = renderApp('/queue?client=cafe-al-noor')

    const sent = await screen.findByRole('group', { name: 'Sent' })
    await waitFor(() =>
      expect(within(sent).getByRole('button', { name: /^Could not send/ })).toHaveTextContent('Could not send2'),
    )
    await user.click(within(sent).getByRole('button', { name: /^Posted/ }))

    await waitFor(() => expect(router.state.location.search).toBe('?client=cafe-al-noor&sending=posted'))
    await waitFor(() => expect(queueCalls(api).at(-1)).toMatchObject({ sending: 'posted' }))
    expect(screen.queryByRole('group', { name: 'Category' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Queue overview' })).not.toBeInTheDocument()
    const [posted, failed] = screen.getAllByRole('row').slice(1) as [HTMLElement, HTMLElement]
    expect(await within(posted).findByRole('link', { name: 'Open in Xero' })).toHaveAttribute(
      'href', 'https://go.xero.com/AccountsPayable/View.aspx?InvoiceID=bill-1',
    )  // prettier-ignore
    expect(failed).toHaveTextContent('Could not send')
    expect(failed).toHaveTextContent('made-up error')
  })

  const REASON = 'Not sent to Xero: bill 108 from this supplier is already in Xero (submitted)'
  const CAME_BACK: QueuePage = {
    ...PAGE,
    sending_counts: { waiting: 0, failed: 0, posted: 5, not_sent: 1 },
    documents: [doc('n1', { not_sent: REASON }), ...PAGE.documents],
  }

  it('shows what came back not sent on top, in red with its reason, not as Ready', async () => {
    mockApi({
      '/health/ready': READY, '/clients': CLIENTS, '/clients/cafe-al-noor/runs': RUNS,
      '/clients/cafe-al-noor/accounting': XERO_STATUS, '/documents': CAME_BACK,
    })  // prettier-ignore
    renderApp('/queue?client=cafe-al-noor')

    await screen.findByRole('table', { name: 'Documents' })
    const [first, second] = screen.getAllByRole('row').slice(1) as [HTMLElement, HTMLElement]
    expect(within(first).getByText('Not sent')).toBeInTheDocument()
    expect(within(first).queryByText('Ready')).not.toBeInTheDocument()
    expect(within(first).getByText(REASON)).toBeInTheDocument()
    expect(within(second).queryByText('Not sent')).not.toBeInTheDocument()
    expect(within(second).getByText('Exception')).toBeInTheDocument()
  })

  it('lists only what came back not sent, with their issues, from its count', async () => {
    const api = mockApi({
      '/health/ready': READY, '/clients': CLIENTS, '/clients/cafe-al-noor/runs': RUNS,
      '/clients/cafe-al-noor/accounting': XERO_STATUS, '/documents': CAME_BACK,
    })  // prettier-ignore
    const user = userEvent.setup()
    const router = renderApp('/queue?client=cafe-al-noor')

    const sent = await screen.findByRole('group', { name: 'Sent' })
    const notSent = await within(sent).findByRole('button', { name: /^Not sent/ })
    expect(notSent).toHaveTextContent('Not sent1')
    await user.click(notSent)

    await waitFor(() => expect(router.state.location.search).toBe('?client=cafe-al-noor&sending=not_sent'))
    await waitFor(() => expect(queueCalls(api).at(-1)).toMatchObject({ sending: 'not_sent' }))
    expect(screen.getByRole('columnheader', { name: 'Issues' })).toBeInTheDocument()
  })

  it('shows no Not sent button when nothing came back', async () => {
    backend()
    renderApp('/queue?client=cafe-al-noor')

    const sent = await screen.findByRole('group', { name: 'Sent' })
    await within(sent).findByRole('button', { name: /^Posted/ })
    expect(within(sent).queryByRole('button', { name: /^Not sent/ })).not.toBeInTheDocument()
  })

  it('goes back to the documents in review from a list of sent ones', async () => {
    const api = backend()
    const user = userEvent.setup()
    const router = renderApp('/queue?client=cafe-al-noor&sending=failed')

    await screen.findByRole('table', { name: 'Documents' })
    expect(queueCalls(api)[0]).toMatchObject({ sending: 'failed' })
    await user.click(screen.getByRole('button', { name: 'In review' }))

    await waitFor(() => expect(router.state.location.search).toBe('?client=cafe-al-noor'))
    expect(screen.getByRole('group', { name: 'Category' })).toBeInTheDocument()
  })
})
