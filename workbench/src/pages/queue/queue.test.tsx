import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { DocumentSummary, QueuePage, Readiness } from '@/api/client'
import { mockApi, renderApp, reply } from '@/test/render'

const READY: Readiness = { status: 'ok', database: 'ok', redis: 'ok', model: 'ok' }
const RESTAURANTS = [
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
    ...changes,
  }
}

const PAGE: QueuePage = {
  total: 3,
  limit: 50,
  offset: 0,
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
    '/clients': RESTAURANTS,
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
  it('asks for a restaurant first', async () => {
    const api = backend()
    renderApp('/queue')

    expect(await screen.findByText('Choose a restaurant to see its documents.')).toBeInTheDocument()
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
    await user.click(screen.getByRole('button', { name: 'Exception' }))
    await waitFor(() => expect(router.state.location.search).toBe('?client=cafe-al-noor&queue=EXCEPTION'))
    await user.click(screen.getByRole('button', { name: 'Parked (not postable)' }))

    await waitFor(() =>
      expect(queueCalls(api).at(-1)).toMatchObject({ queue: 'EXCEPTION', status: 'NON_POSTABLE' }),
    )
    expect(screen.getByRole('button', { name: 'Exception' })).toHaveAttribute('aria-pressed', 'true')
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

  it('remembers the last restaurant on this computer', async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/queue')

    await user.click(await screen.findByRole('combobox', { name: 'Restaurant' }))
    await user.click(await screen.findByRole('option', { name: 'Spice Route' }))
    await screen.findByRole('table', { name: 'Documents' })

    expect(localStorage.getItem('clearbook.lastRestaurant')).toBe('spice-route')
  })

  it('opens a remembered restaurant straight away', async () => {
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

    expect(router.state.location.pathname).toBe('/documents/d3')
    expect(await screen.findByText('This document could not be loaded')).toBeInTheDocument()
  })

  it('says when there is nothing to review', async () => {
    backend({ ...PAGE, total: 0, documents: [] })
    renderApp('/queue?client=cafe-al-noor')

    expect(await screen.findByText('Nothing to review.')).toBeInTheDocument()
  })

  it('says why the queue could not be loaded', async () => {
    backend(reply(404, { detail: 'client not found' }))
    renderApp('/queue?client=gone')

    expect(await screen.findByText('The queue could not be loaded')).toBeInTheDocument()
    expect(screen.getByText('client not found')).toBeInTheDocument()
  })
})
