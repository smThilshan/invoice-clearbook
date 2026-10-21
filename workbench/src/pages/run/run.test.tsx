import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { FolderListing, Readiness, RunProgress } from '@/api/client'
import { RUN_REFRESH_MS } from '@/api/queries'
import { type Answer, mockApi, renderApp, reply } from '@/test/render'

const READY: Readiness = { status: 'ok', database: 'ok', redis: 'ok', model: 'ok' }
const RESTAURANTS = [
  { client_id: 'cafe-al-noor', name: 'Cafe Al Noor' },
  { client_id: 'spice-route', name: 'Spice Route' },
]
const FOLDERS: Record<string, FolderListing> = {
  '': {
    path: '',
    folders: [
      { name: 'October 2026', path: 'October 2026', date: '2026-10-31' },
      { name: 'Supplier bills', path: 'Supplier bills', date: null },
    ],
  },
  'October 2026': {
    path: 'October 2026',
    folders: [{ name: '21', path: 'October 2026/21', date: '2026-10-21' }],
  },
  'October 2026/21': { path: 'October 2026/21', folders: [] },
  'Supplier bills': { path: 'Supplier bills', folders: [] },
}
const RUN_ID = '6f1c2b0e-0000-4000-8000-000000000001'

const realFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = realFetch
  vi.useRealTimers()
})

function folders(request: Request) {
  const path = new URL(request.url).searchParams.get('path') ?? ''
  return FOLDERS[path] ?? reply(404, { detail: `folder not found: ${path}` })
}

function backend(extra: Record<string, Answer> = {}) {
  return mockApi({
    '/health/ready': READY,
    '/clients': RESTAURANTS,
    '/clients/cafe-al-noor/folders': folders,
    'POST /runs': reply(202, {
      run_id: RUN_ID,
      status: 'RUNNING',
      folder: 'October 2026/21',
      folder_date: '2026-10-21',
    }),
    [`/runs/${RUN_ID}`]: progress(),
    ...extra,
  })
}

function progress(changes: Partial<RunProgress> = {}): RunProgress {
  return {
    run_id: RUN_ID,
    client_id: 'cafe-al-noor',
    folder: 'October 2026/21',
    folder_date: '2026-10-21',
    status: 'RUNNING',
    failure_reason: null,
    state: 'processing',
    created_at: '2026-10-21T08:00:00Z',
    files_found: 3,
    counts: { IN_REVIEW: 1, READ: 1, FAILED: 1 },
    files: [
      { file: 'inv-1.pdf', status: 'IN_REVIEW', document_id: 'd1', document_type: 'BILL',
        pages: 1, failure_reason: null, from_earlier_run: false },
      { file: 'inv-2.pdf', status: 'READ', document_id: 'd2', document_type: null,
        pages: 2, failure_reason: null, from_earlier_run: false },
      { file: 'scan.jpg', status: 'FAILED', document_id: 'd3', document_type: null,
        pages: null, failure_reason: 'file is damaged', from_earlier_run: false },
    ],
    ...changes,
  }
}

async function pickRestaurant(user: ReturnType<typeof userEvent.setup>, name = 'Cafe Al Noor') {
  await user.click(await screen.findByRole('combobox', { name: 'Restaurant' }))
  await user.click(await screen.findByRole('option', { name }))
}

async function openFolder(user: ReturnType<typeof userEvent.setup>, name: string) {
  const list = await screen.findByRole('list', { name: 'Sub-folders' })
  await user.click(within(list).getByRole('button', { name: new RegExp(`^${name}`) }))
}

describe('run screen', () => {
  it('browses into a day folder and takes its date from the name', async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickRestaurant(user)
    await openFolder(user, 'October 2026')
    await openFolder(user, '21')

    expect(screen.getByRole('navigation', { name: 'Folder path' })).toHaveTextContent(
      'Cafe Al Noor' + 'October 2026' + '21',
    )
    expect(screen.getByLabelText('Folder date')).toHaveValue('2026-10-21')
    expect(screen.getByTestId('date-hint')).toHaveTextContent('Taken from the folder name.')
    expect(await screen.findByText('No sub-folders here.')).toBeInTheDocument()
  })

  it('goes back up with the folder path and uses the month end for a month folder', async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickRestaurant(user)
    await openFolder(user, 'October 2026')
    await openFolder(user, '21')
    await user.click(screen.getByRole('button', { name: 'October 2026' }))

    expect(screen.getByLabelText('Folder date')).toHaveValue('2026-10-31')
    expect(await screen.findByRole('button', { name: /^21/ })).toBeInTheDocument()
  })

  it('starts the run with the confirmed folder and date, then shows its progress', async () => {
    const api = backend()
    const user = userEvent.setup()
    const router = renderApp('/run')

    await pickRestaurant(user)
    await openFolder(user, 'October 2026')
    await openFolder(user, '21')
    await user.click(screen.getByRole('button', { name: 'Run Cafe Al Noor / October 2026 / 21' }))

    await waitFor(() => expect(router.state.location.pathname).toBe(`/run/${RUN_ID}`))
    expect(api.bodies['POST /runs']).toEqual({
      client_id: 'cafe-al-noor',
      folder: 'October 2026/21',
      folder_date: '2026-10-21',
    })
    expect(await screen.findByRole('heading', { name: 'Run: Cafe Al Noor' })).toBeInTheDocument()
  })

  it('sends no folder date when the user clears it', async () => {
    const api = backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickRestaurant(user)
    await openFolder(user, 'October 2026')
    await user.clear(screen.getByLabelText('Folder date'))

    expect(screen.getByTestId('date-hint')).toHaveTextContent('the period check is skipped')
    await user.click(screen.getByRole('button', { name: /^Run Cafe Al Noor/ }))
    await waitFor(() =>
      expect(api.bodies['POST /runs']).toEqual({
        client_id: 'cafe-al-noor',
        folder: 'October 2026',
        folder_date: null,
      }),
    )
  })

  it('asks for a date when the folder name has none', async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickRestaurant(user)
    await openFolder(user, 'Supplier bills')

    expect(screen.getByLabelText('Folder date')).toHaveValue('')
    expect(screen.getByTestId('date-hint')).toHaveTextContent('The folder name has no date.')
  })

  it('says why a folder could not be opened', async () => {
    backend({ '/clients/spice-route/folders': reply(404, { detail: 'folder not found: (client folder)' }) })
    const user = userEvent.setup()
    renderApp('/run')

    await pickRestaurant(user, 'Spice Route')

    expect(await screen.findByText('This folder could not be opened')).toBeInTheDocument()
    expect(screen.getByText('folder not found: (client folder)')).toBeInTheDocument()
  })

  it('says why the run did not start', async () => {
    backend({ 'POST /runs': reply(404, { detail: 'folder not found: October 2026' }) })
    const user = userEvent.setup()
    const router = renderApp('/run')

    await pickRestaurant(user)
    await openFolder(user, 'October 2026')
    await user.click(screen.getByRole('button', { name: /^Run Cafe Al Noor/ }))

    expect(await screen.findByText('The run did not start')).toBeInTheDocument()
    expect(screen.getByText('folder not found: October 2026')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/run')
  })

  it('explains how to add a restaurant when there is none', async () => {
    backend({ '/clients': [] })
    renderApp('/run')

    expect(await screen.findByText(/No restaurants yet/)).toHaveTextContent('make add-client')
  })
})

describe('run progress', () => {
  it('shows every file with its status, and counts by status', async () => {
    backend()
    renderApp(`/run/${RUN_ID}`)

    const table = await screen.findByRole('table', { name: 'Files' })
    const rows = within(table).getAllByRole('row')
    expect(rows).toHaveLength(4) // header + 3 files
    expect(rows[1]).toHaveTextContent('inv-1.pdf' + 'In review' + 'Bill' + '1')
    expect(rows[3]).toHaveTextContent('scan.jpg' + 'Failed')
    expect(rows[3]).toHaveTextContent('file is damaged')
    const counts = screen.getByRole('list', { name: 'Files by status' })
    expect(counts).toHaveTextContent('In review: 1')
    expect(counts).toHaveTextContent('Failed: 1')
    expect(screen.getByRole('status')).toHaveTextContent('3 files found, 1 still being worked on')
    expect(screen.queryByRole('link', { name: /open review queue/i })).not.toBeInTheDocument()
  })

  it('refreshes until the run is finished, then offers the review queue', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let calls = 0
    const finished = progress({ state: 'finished', status: 'COMPLETED',
      counts: { IN_REVIEW: 3 } })  // prettier-ignore
    const api = backend({ [`/runs/${RUN_ID}`]: () => (++calls < 2 ? progress() : finished) })
    renderApp(`/run/${RUN_ID}`)

    expect(await screen.findByText('Processing')).toBeInTheDocument()
    await act(() => vi.advanceTimersByTimeAsync(RUN_REFRESH_MS))

    const queue = await screen.findByRole('link', { name: /open review queue/i })
    expect(queue).toHaveAttribute('href', `/queue?client=cafe-al-noor&run=${RUN_ID}`)
    await act(() => vi.advanceTimersByTimeAsync(RUN_REFRESH_MS * 3))
    expect(api.calls.filter((call) => call === `GET /runs/${RUN_ID}`)).toHaveLength(2)
  })

  it('shows why a run stopped', async () => {
    backend({
      [`/runs/${RUN_ID}`]: progress({ status: 'FAILED', state: 'finished', files: [], counts: {},
        failure_reason: 'folder not found: October 2026/21' }),  // prettier-ignore
    })
    renderApp(`/run/${RUN_ID}`)

    expect(await screen.findByText('The run stopped')).toBeInTheDocument()
    expect(screen.getByText('folder not found: October 2026/21')).toBeInTheDocument()
  })

  it('says when a run does not exist', async () => {
    backend({ [`/runs/${RUN_ID}`]: reply(404, { detail: 'run not found' }) })
    renderApp(`/run/${RUN_ID}`)

    expect(await screen.findByText('This run could not be loaded')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /start another run/i })).toHaveAttribute('href', '/run')
  })
})
