import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { FolderPlace, PlaceListing, Readiness, RunProgress } from '@/api/client'
import { RUN_REFRESH_MS } from '@/api/queries'
import { type Answer, mockApi, renderApp, reply } from '@/test/render'

const READY: Readiness = { status: 'ok', database: 'ok', redis: 'ok', model: 'ok' }
const CLIENTS = [
  { client_id: 'cafe-al-noor', name: 'Cafe Al Noor' }, // has its own folder
  { client_id: 'spice-route', name: 'Spice Route' }, // has none
]
const OWN: FolderPlace = { key: 'client', label: 'Cafe Al Noor folder', path: '/Shared/Cafe Al Noor' }
const MAC: FolderPlace = { key: 'p0', label: 'This Mac', path: '/Users/me' }
type Sub = [name: string, date: string | null, files: number | null]
const listing = (place: string, path: string, folders: Sub[], files = 0): PlaceListing => ({
  place,
  path,
  folders: folders.map(([name, date, count]) => ({
    name, path: path ? `${path}/${name}` : name, date, invoice_files: count,
  })),
  invoice_files: files,
  total_files: folders.some(([, , n]) => n === null) ? null
    : files + folders.reduce((sum, [, , n]) => sum + (n ?? 0), 0),
})  // prettier-ignore
const FOLDERS: Record<string, PlaceListing> = {
  'client:': listing('client', '', [['October 2026', '2026-10-31', 2], ['Supplier bills', null, 0]]),
  'client:October 2026': listing('client', 'October 2026', [['21', '2026-10-21', 2]]),
  'client:October 2026/21': listing('client', 'October 2026/21', [], 2),
  'client:Supplier bills': listing('client', 'Supplier bills', []),
  'p0:': listing('p0', '', [['Invoice tests', null, 3], ['Library', null, null]]),
  'p0:Invoice tests': listing('p0', 'Invoice tests', [], 3),
}
const RUN_ID = '6f1c2b0e-0000-4000-8000-000000000001'

const realFetch = globalThis.fetch

beforeEach(() => localStorage.clear()) // the remembered client and folder (task 4.17)
afterEach(() => {
  globalThis.fetch = realFetch
  vi.useRealTimers()
})

function places(request: Request) {
  return new URL(request.url).searchParams.get('client_id') === 'cafe-al-noor' ? [OWN, MAC] : [MAC]
}

function folder(request: Request) {
  const params = new URL(request.url).searchParams
  const path = params.get('path') ?? ''
  return FOLDERS[`${params.get('place')}:${path}`] ?? reply(404, { detail: `folder not found: ${path}` })
}

function backend(extra: Record<string, Answer> = {}) {
  return mockApi({
    '/health/ready': READY,
    '/clients': CLIENTS,
    'POST /clients': reply(201, { client_id: 'blue-sky', name: 'Blue Sky' }),
    '/folders/places': places,
    '/folders': folder,
    '/runs/typical-time': { typical_ms: 240_000 },
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
    files_found: 4,
    counts: { IN_REVIEW: 1, READ: 2, FAILED: 1 },
    typical_ms: 240_000,
    force_stop: false,
    server_time: '2026-10-21T08:05:00Z',
    files: [
      { file: 'inv-1.pdf', status: 'IN_REVIEW', document_id: 'd1', document_type: 'BILL',
        pages: 1, failure_reason: null, from_earlier_run: false, step: 'done', step_since: null,
        queue_position: null,
        timings: { waiting_ms: 34_000, read_ms: 6_000, extract_ms: 200_000, coding_ms: 15_000,
                   checks_ms: 1_000, total_ms: 256_000 } },
      { file: 'inv-2.pdf', status: 'READ', document_id: 'd2', document_type: null,
        pages: 2, failure_reason: null, from_earlier_run: false, step: 'ai',
        step_since: '2026-10-21T08:02:29Z', queue_position: null,
        timings: { waiting_ms: null, read_ms: 4_200, extract_ms: null, coding_ms: null,
                   checks_ms: null, total_ms: null } },
      { file: 'inv-3.pdf', status: 'READ', document_id: 'd4', document_type: null, pages: 1,
        failure_reason: null, from_earlier_run: false, step: 'waiting_ai', step_since: null,
        queue_position: 2, timings: null },
      { file: 'scan.jpg', status: 'FAILED', document_id: 'd3', document_type: null,
        pages: null, failure_reason: 'file is damaged', from_earlier_run: false, step: 'failed',
        step_since: null, queue_position: null, timings: null },
    ],
    ...changes,
  }
}

async function pickClient(user: ReturnType<typeof userEvent.setup>, name = 'Cafe Al Noor') {
  await user.click(await screen.findByRole('combobox', { name: 'Client' }))
  await user.click(await screen.findByRole('option', { name }))
}

async function openFolder(user: ReturnType<typeof userEvent.setup>, name: string) {
  const list = await screen.findByRole('list', { name: 'Sub-folders' })
  await user.click(within(list).getByRole('button', { name: new RegExp(`^${name}`) }))
}

describe('run screen', () => {
  it("opens the client's own folder, browses into a day folder and takes its date", async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    await openFolder(user, 'October 2026')
    await openFolder(user, '21')

    const placeGroup = screen.getByRole('group', { name: 'Place' })
    expect(within(placeGroup).getByRole('button', { name: 'Cafe Al Noor folder' }))
      .toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('navigation', { name: 'Folder path' })).toHaveTextContent(
      'Cafe Al Noor folder' + 'October 2026' + '21',
    )
    expect(screen.getByLabelText('Folder date')).toHaveValue('2026-10-21')
    expect(screen.getByTestId('date-hint')).toHaveTextContent('Taken from the folder name.')
    expect(await screen.findByTestId('file-count')).toHaveTextContent('2 invoice files')
    expect(screen.getByText('No sub-folders here.')).toBeInTheDocument()
  })

  it('goes back up with the folder path and uses the month end for a month folder', async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    await openFolder(user, 'October 2026')
    await openFolder(user, '21')
    await user.click(screen.getByRole('button', { name: 'October 2026' }))

    expect(screen.getByLabelText('Folder date')).toHaveValue('2026-10-31')
    expect(await screen.findByRole('button', { name: /^21/ })).toBeInTheDocument()
  })

  it('starts the run with the place, folder and date, then shows its progress', async () => {
    const api = backend()
    const user = userEvent.setup()
    const router = renderApp('/run')

    await pickClient(user)
    await openFolder(user, 'October 2026')
    await openFolder(user, '21')
    await user.click(
      screen.getByRole('button', { name: 'Run Cafe Al Noor: Cafe Al Noor folder / October 2026 / 21' }),
    )

    await waitFor(() => expect(router.state.location.pathname).toBe(`/run/${RUN_ID}`))
    expect(api.bodies['POST /runs']).toEqual({
      client_id: 'cafe-al-noor',
      place: 'client',
      folder: 'October 2026/21',
      folder_date: '2026-10-21',
    })
    expect(await screen.findByRole('heading', { name: 'Run: Cafe Al Noor' })).toBeInTheDocument()
  })

  it('runs a folder on this Mac, but never the whole of it', async () => {
    const api = backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    await user.click(within(await screen.findByRole('group', { name: 'Place' })).getByRole('button', { name: 'This Mac' }))

    expect(await screen.findByText(/Open a folder inside This Mac first/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Run Cafe Al Noor/ })).toBeDisabled()
    await openFolder(user, 'Invoice tests')
    expect(await screen.findByTestId('file-count')).toHaveTextContent('3 invoice files')
    await user.click(screen.getByRole('button', { name: /^Run Cafe Al Noor/ }))

    await waitFor(() =>
      expect(api.bodies['POST /runs']).toMatchObject({ place: 'p0', folder: 'Invoice tests' }),
    )
  })

  it('creates a client from a name and picks it straight away', async () => {
    const api = backend()
    const user = userEvent.setup()
    renderApp('/run')

    await user.click(await screen.findByRole('button', { name: 'New client' }))
    await user.type(screen.getByLabelText('New client name'), 'Blue Sky')
    await user.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(api.bodies['POST /clients']).toEqual({ name: 'Blue Sky' }))
    expect(await screen.findByRole('button', { name: 'This Mac' })).toBeInTheDocument()
    expect(api.calls).toContain('GET /folders/places?client_id=blue-sky')
    expect(localStorage.getItem('clearbook.lastClient')).toBe('blue-sky')
  })

  it('says why a client could not be created', async () => {
    backend({ 'POST /clients': reply(422, { detail: 'name is required' }) })
    const user = userEvent.setup()
    renderApp('/run')

    await user.click(await screen.findByRole('button', { name: 'New client' }))
    await user.type(screen.getByLabelText('New client name'), 'x')
    await user.click(screen.getByRole('button', { name: 'Create' }))

    expect(await screen.findByText('The client was not created')).toBeInTheDocument()
  })

  it('sends no folder date when the user clears it', async () => {
    const api = backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    await openFolder(user, 'October 2026')
    await user.clear(screen.getByLabelText('Folder date'))

    expect(screen.getByTestId('date-hint')).toHaveTextContent('the period check is skipped')
    await user.click(screen.getByRole('button', { name: /^Run Cafe Al Noor/ }))
    await waitFor(() =>
      expect(api.bodies['POST /runs']).toEqual({
        client_id: 'cafe-al-noor',
        place: 'client',
        folder: 'October 2026',
        folder_date: null,
      }),
    )
  })

  it('asks for a date when the folder name has none', async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    await openFolder(user, 'Supplier bills')

    expect(screen.getByLabelText('Folder date')).toHaveValue('')
    expect(screen.getByTestId('date-hint')).toHaveTextContent('The folder name has no date.')
  })

  it('says why a folder could not be opened', async () => {
    backend({ '/folders': reply(404, { detail: 'folder not found: Invoice tests' }) })
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user, 'Spice Route')

    expect(await screen.findByText('This folder could not be opened')).toBeInTheDocument()
    expect(screen.getByText('folder not found: Invoice tests')).toBeInTheDocument()
  })

  it('says why the run did not start', async () => {
    backend({ 'POST /runs': reply(404, { detail: 'folder not found: October 2026' }) })
    const user = userEvent.setup()
    const router = renderApp('/run')

    await pickClient(user)
    await openFolder(user, 'October 2026')
    await user.click(screen.getByRole('button', { name: /^Run Cafe Al Noor/ }))

    expect(await screen.findByText('The run did not start')).toBeInTheDocument()
    expect(screen.getByText('folder not found: October 2026')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/run')
  })

  it('sums up the run before it starts: files and roughly how long', async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    expect(await screen.findByRole('button', { name: /^October 2026/ })).toHaveTextContent('2 invoices')
    await openFolder(user, 'October 2026')

    const run = screen.getByLabelText('Run summary')
    await waitFor(() => expect(run).toHaveTextContent('2 invoice files · about 8 min on this computer'))
    expect(screen.getByTestId('file-count')).toHaveTextContent('2 invoice files in its sub-folders')
  })

  it('cannot run a folder without invoice files', async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    await openFolder(user, 'Supplier bills')

    expect(await screen.findByText('No invoice files in this folder or below it.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Run Cafe Al Noor/ })).toBeDisabled()
  })

  it('says when a folder is too big to count, and remembers the last folder per client', async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    await user.click(within(await screen.findByRole('group', { name: 'Place' })).getByRole('button', { name: 'This Mac' }))
    expect(await screen.findByRole('button', { name: /^Library/ })).toHaveTextContent('many files')
    await openFolder(user, 'Invoice tests')

    expect(JSON.parse(localStorage.getItem('clearbook.lastFolder.cafe-al-noor') ?? '{}')).toMatchObject({
      place: 'p0', crumbs: [{ name: 'This Mac' }, { name: 'Invoice tests' }],
    })  // prettier-ignore
  })

  it('says how to add the first client', async () => {
    backend({ '/clients': [] })
    renderApp('/run')

    expect(await screen.findByText(/No clients yet/)).toHaveTextContent('add one on the Run screen')
  })
})

describe('run progress', () => {
  it('shows where each file is, with a live timer, the progress and the time left', async () => {
    backend()
    renderApp(`/run/${RUN_ID}`)

    const files = await screen.findByRole('list', { name: 'Files' })
    expect(within(files).getByRole('listitem', { name: 'inv-1.pdf' })).toHaveTextContent('In review')
    expect(within(files).getByRole('listitem', { name: 'inv-2.pdf' })).toHaveTextContent(
      /AI reading · 2:3\d \(a file usually takes about 4 min\)/,
    )
    expect(within(files).getByRole('listitem', { name: 'inv-3.pdf' })).toHaveTextContent(
      'Waiting for the AI · 2nd in line',
    )
    expect(within(files).getByRole('listitem', { name: 'scan.jpg' })).toHaveTextContent('file is damaged')
    expect(screen.getByRole('status')).toHaveTextContent('Processing: 2 of 4 files done · 1 failed')
    expect(screen.getByRole('status')).toHaveTextContent(/about 5 min 2\d s left/)  // 1:29 + 4:00
    expect(screen.getByRole('progressbar', { name: 'Run progress' })).toHaveAttribute('aria-valuenow', '50')
    await waitFor(() => expect(document.title).toBe('(2/4) Run · Cafe Al Noor – Clearbook'))
  })

  it('opens a finished file and retries a failed one from the run', async () => {
    const api = backend({ 'POST /documents/d3/retry': {} })
    const user = userEvent.setup()
    renderApp(`/run/${RUN_ID}`)

    const done = await screen.findByRole('listitem', { name: 'inv-1.pdf' })
    expect(within(done).getByRole('link', { name: 'Open' })).toHaveAttribute('href', '/documents/d1')
    await user.click(within(screen.getByRole('listitem', { name: 'scan.jpg' })).getByRole('button', { name: 'Retry' }))

    await waitFor(() => expect(api.calls).toContain('POST /documents/d3/retry'))
    expect(screen.getByRole('link', { name: /review the finished ones/i })).toHaveAttribute(
      'href', `/queue?client=cafe-al-noor&run=${RUN_ID}`,
    )  // prettier-ignore
  })

  it('shows the time each step took per file, and the average without waiting', async () => {
    backend()
    const user = userEvent.setup()
    renderApp(`/run/${RUN_ID}`)

    await user.click(await screen.findByText('Time per step', { selector: 'summary' }))
    const table = screen.getByRole('table', { name: 'Time per step' })
    const rows = within(table).getAllByRole('row')
    expect(rows).toHaveLength(3) // header + the 2 timed files
    expect(rows[1]).toHaveTextContent(
      'inv-1.pdf' + '34 s' + '6.0 s' + '3 min 20 s' + '15 s' + '1.0 s' + '4 min 16 s',
    )
    expect(rows[2]).toHaveTextContent('inv-2.pdf' + '—' + '4.2 s' + '—')
    expect(screen.getByText(/Per invoice, without waiting/)).toHaveTextContent(
      '3 min 42 s on average (1 timed',
    )
  })

  it('refreshes until the run is finished, then offers the review queue', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let calls = 0
    const finished = progress({ state: 'finished', status: 'COMPLETED', typical_ms: null,
      files: progress().files.map((f) => ({ ...f, step: 'done' as const, status: 'IN_REVIEW' })) })  // prettier-ignore
    const api = backend({ [`/runs/${RUN_ID}`]: () => (++calls < 2 ? progress() : finished) })
    renderApp(`/run/${RUN_ID}`)

    expect(await screen.findByText(/Processing: 2 of 4/)).toBeInTheDocument()
    await act(() => vi.advanceTimersByTimeAsync(RUN_REFRESH_MS))

    const queue = await screen.findByRole('link', { name: /open review queue/i })
    expect(queue).toHaveAttribute('href', `/queue?client=cafe-al-noor&run=${RUN_ID}`)
    expect(screen.getByRole('status')).toHaveTextContent('Finished: 4 of 4 files done')
    await act(() => vi.advanceTimersByTimeAsync(RUN_REFRESH_MS * 3))
    expect(api.calls.filter((call) => call === `GET /runs/${RUN_ID}`)).toHaveLength(2)
  })

  it('stops a run after asking, and says the file being read finishes first', async () => {
    const stopping = progress({ status: 'STOPPED',
      files: progress().files.map((f) => (f.step === 'waiting_ai' ? { ...f, step: 'stopped' as const } : f)) })  // prettier-ignore
    const api = backend({ [`POST /runs/${RUN_ID}/stop`]: stopping })
    const user = userEvent.setup()
    renderApp(`/run/${RUN_ID}`)

    await user.click(await screen.findByRole('button', { name: 'Stop run' }))
    const dialog = await screen.findByRole('dialog', { name: 'Stop this run?' })
    await user.click(within(dialog).getByRole('button', { name: 'Stop run' }))

    await waitFor(() => expect(api.calls).toContain(`POST /runs/${RUN_ID}/stop`))
    expect(await screen.findByText(/Stopping: finishing the current file/)).toBeInTheDocument()
    expect(screen.getByRole('listitem', { name: 'inv-3.pdf' })).toHaveTextContent('Stopped: Resume continues it')
    expect(screen.getByRole('button', { name: 'Resume' })).toBeInTheDocument()
  })

  it('force-stops the AI read still under way, after asking', async () => {
    const stopping = progress({ status: 'STOPPED',
      files: progress().files.map((f) => (f.step === 'waiting_ai' ? { ...f, step: 'stopped' as const } : f)) })  // prettier-ignore
    const api = backend({ [`/runs/${RUN_ID}`]: stopping,
      [`POST /runs/${RUN_ID}/force-stop`]: { ...stopping, force_stop: true } })  // prettier-ignore
    const user = userEvent.setup()
    renderApp(`/run/${RUN_ID}`)

    await user.click(await screen.findByRole('button', { name: 'Force stop now' }))
    const dialog = await screen.findByRole('dialog', { name: 'End the AI reading now?' })
    await user.click(within(dialog).getByRole('button', { name: 'Force stop now' }))

    await waitFor(() => expect(api.calls).toContain(`POST /runs/${RUN_ID}/force-stop`))
    expect(await screen.findByText(/Force stopping: ending the AI read now/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Force stopping/ })).toBeDisabled()
  })

  it('resumes a stopped run', async () => {
    const stopped = progress({ status: 'STOPPED',
      files: progress().files.map((f) => (['ai', 'waiting_ai'].includes(f.step) ? { ...f, step: 'stopped' as const } : f)) })  // prettier-ignore
    const api = backend({ [`/runs/${RUN_ID}`]: stopped, [`POST /runs/${RUN_ID}/resume`]: progress() })
    const user = userEvent.setup()
    renderApp(`/run/${RUN_ID}`)

    expect(await screen.findByRole('status')).toHaveTextContent('Stopped: 2 of 4 files done · 1 failed · 2 stopped')
    await user.click(screen.getByRole('button', { name: 'Resume' }))

    await waitFor(() => expect(api.calls).toContain(`POST /runs/${RUN_ID}/resume`))
    expect(await screen.findByRole('button', { name: 'Stop run' })).toBeInTheDocument()
  })

  it('shows why a run could not continue', async () => {
    backend({
      [`/runs/${RUN_ID}`]: progress({ status: 'FAILED', state: 'finished', files: [], counts: {},
        failure_reason: 'folder not found: October 2026/21' }),  // prettier-ignore
    })
    renderApp(`/run/${RUN_ID}`)

    expect(await screen.findByText('The run could not continue')).toBeInTheDocument()
    expect(screen.getByText('folder not found: October 2026/21')).toBeInTheDocument()
  })

  it('says when a run does not exist', async () => {
    backend({ [`/runs/${RUN_ID}`]: reply(404, { detail: 'run not found' }) })
    renderApp(`/run/${RUN_ID}`)

    expect(await screen.findByText('This run could not be loaded')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /start another run/i })).toHaveAttribute('href', '/run')
  })
})
