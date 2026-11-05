import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  AccountingStatus,
  Readiness,
  RunProgress,
  SharePointFolder,
  SharePointListing,
  SharePointStatus,
} from '@/api/client'
import { RUN_REFRESH_MS } from '@/api/queries'
import { type Answer, mockApi, renderApp, reply } from '@/test/render'

const READY: Readiness = { status: 'ok', database: 'ok', redis: 'ok', model: 'ok' }
const CLIENTS = [
  { client_id: 'cafe-al-noor', name: 'Cafe Al Noor' },
  { client_id: 'spice-route', name: 'Spice Route' },
]
/** Missan's SharePoint sign-in (decision 83), connected. */
const SHAREPOINT: SharePointStatus = {
  configured: true, connected: true, needs_reconnect: false, reconnect_reason: null,
  account_name: 'Made-up Admin', account_email: 'admin@missan.test', connected_by: 'admin@missan.test',
  connected_at: '2026-11-05T08:00:00Z',
}  // prettier-ignore
const DRIVE = 'b!drive-1'
const sp = (itemId: string, name: string, path: string, date: string | null, items: number | null = null): SharePointFolder =>
  ({ drive_id: DRIVE, item_id: itemId, name, path, date, items })  // prettier-ignore
const ROOT = sp('root', 'Documents', '', null)
const ROAST = sp('f-roast', 'ROAST', 'ROAST', null, 1)
const APRIL = sp('f-april', 'April', 'ROAST/April', '2026-04-30', 2)
const APRIL_11 = sp('f-a11', 'April 11', 'ROAST/April/April 11', '2026-04-11', 3)
const SUPPLIERS = sp('f-sup', 'Supplier bills', 'ROAST/Supplier bills', null, 0)
const inside = (folder: SharePointFolder, folders: SharePointFolder[], files = 0, other = 0): SharePointListing =>
  ({ folder, folders, invoice_files: files, other_files: other })  // prettier-ignore
const LISTINGS: Record<string, SharePointListing> = {
  root: inside(ROOT, [ROAST]),
  'f-roast': inside(ROAST, [APRIL, SUPPLIERS]),
  'f-april': inside(APRIL, [APRIL_11], 1),
  'f-a11': inside(APRIL_11, [], 2, 1),
  'f-sup': inside(SUPPLIERS, []),
}
const RUN_ID = '6f1c2b0e-0000-4000-8000-000000000001'
const XERO = { key: 'XERO' as const, name: 'Xero', configured: true, connect_url: '/xero/connect' }
const ZOHO = { key: 'ZOHO' as const, name: 'Zoho Books', configured: true, connect_url: '/zoho/connect' }
const NOTHING_SENT = {
  ready: 0, retrying: 0, retry_at: null, retry_reason: null, not_yet: {}, sent_back: 0, posted: 0,
  paused: 0, paused_reason: null, failed: 0, failed_reason: null,
}  // prettier-ignore
/** Connected to Zoho Books and synced: ready to run (decision 79). */
const SYNCED: AccountingStatus = {
  providers: [XERO, ZOHO], configured: true, connected: true, needs_reconnect: false,
  reconnect_reason: null, provider: 'ZOHO', provider_name: 'Zoho Books', organisation: 'Missan Trading',
  connected_at: '2026-11-01T08:00:00Z', bills_where: 'Purchases, Bills', master_data_from: 'ZOHO',
  accounts: 42, waiting: 0, posting: NOTHING_SENT,
}  // prettier-ignore
const NOT_SYNCED: AccountingStatus = { ...SYNCED, master_data_from: 'MANUAL', accounts: 0 }
const NOT_CONNECTED: AccountingStatus = {
  ...SYNCED, connected: false, provider: null, provider_name: null, organisation: null,
  connected_at: null, bills_where: null, master_data_from: 'MANUAL', accounts: 0,
}  // prettier-ignore

const realFetch = globalThis.fetch

beforeEach(() => localStorage.clear()) // the remembered client and folder (task 4.17)
afterEach(() => {
  globalThis.fetch = realFetch
  vi.useRealTimers()
})

function sharePointFolder(request: Request) {
  const itemId = new URL(request.url).searchParams.get('item_id') ?? ''
  return LISTINGS[itemId] ?? reply(409, { detail: 'not found in SharePoint (moved, renamed or deleted?)' })
}

function backend(extra: Record<string, Answer> = {}) {
  return mockApi({
    '/health/ready': READY,
    '/clients': CLIENTS,
    'POST /clients': reply(201, { client_id: 'blue-sky', name: 'Blue Sky' }),
    '/runs/typical-time': { typical_ms: 240_000 },
    '/clients/cafe-al-noor/accounting': SYNCED,
    '/clients/spice-route/accounting': SYNCED,
    '/clients/blue-sky/accounting': SYNCED,
    '/sharepoint/status': SHAREPOINT,
    '/sharepoint/sites': [{ site_id: 's-roast', name: 'ROAST HUB', web_url: 'https://missan.sharepoint.com/sites/ROASTHUB' }],
    '/sharepoint/sites/s-roast/libraries': [{ drive_id: DRIVE, name: 'Documents' }],
    '/sharepoint/folder': sharePointFolder,
    '/sharepoint/link': APRIL,
    'POST /runs/sharepoint': reply(202, { run_id: RUN_ID, status: 'RUNNING', folder: 'April 11', folder_date: '2026-04-11' }),
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
    sharepoint_path: null,
    fetched: null,
    fetch_total: null,
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

async function pickClient(user: ReturnType<typeof userEvent.setup>, name = 'Cafe Al Noor', from = 'On SharePoint') {
  await user.click(await screen.findByRole('combobox', { name: 'Client' }))
  await user.click(await screen.findByRole('option', { name }))
  await user.click(within(await screen.findByRole('group', { name: 'Where the documents are' })).getByRole('button', { name: from }))
}

async function open(user: ReturnType<typeof userEvent.setup>, list: string, name: string) {
  const choices = await screen.findByRole('list', { name: list })
  await user.click(within(choices).getByRole('button', { name: new RegExp(`^${name}`) }))
}

/** Sites › ROAST HUB › Documents › ROAST › then `folders`. */
async function browseTo(user: ReturnType<typeof userEvent.setup>, ...folders: string[]) {
  await open(user, 'Sites', 'ROAST HUB')
  await open(user, 'Libraries', 'Documents')
  await open(user, 'Sub-folders', 'ROAST')
  for (const folder of folders) await open(user, 'Sub-folders', folder)
}

describe('run screen: documents on SharePoint', () => {
  it('browses from the sites into a day folder and takes its date', async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    await browseTo(user, 'April', 'April 11')

    expect(screen.getByRole('navigation', { name: 'Folder path' })).toHaveTextContent(
      'All sites' + 'ROAST HUB' + 'Documents' + 'ROAST' + 'April' + 'April 11',
    )
    expect(screen.getByLabelText('Folder date')).toHaveValue('2026-04-11')
    expect(screen.getByTestId('date-hint')).toHaveTextContent('which has no year: 2026 is assumed.')
    expect(await screen.findByTestId('file-count')).toHaveTextContent(
      '2 invoice files (PDF or photo) in this folder. 1 other file left out.',
    )
  })

  it('shows each sub-folder with how much is in it and its date', async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    await browseTo(user)

    const april = within(await screen.findByRole('list', { name: 'Sub-folders' })).getByRole('button', { name: /^April/ })
    expect(april).toHaveTextContent('2 items')
    expect(april).toHaveTextContent('30 Apr 2026')
  })

  it('starts the run with the folder and date, then shows its progress', async () => {
    const api = backend()
    const user = userEvent.setup()
    const router = renderApp('/run')

    await pickClient(user)
    await browseTo(user, 'April', 'April 11')
    const summary = screen.getByLabelText('Run summary')
    await waitFor(() => expect(summary).toHaveTextContent('2 invoice files · about 8 min on this computer'))
    expect(summary).toHaveTextContent('ROAST HUB / Documents / ROAST / April / April 11')
    await user.click(screen.getByRole('button', { name: 'Run Cafe Al Noor: April 11' }))

    await waitFor(() => expect(router.state.location.pathname).toBe(`/run/${RUN_ID}`))
    expect(api.bodies['POST /runs/sharepoint']).toEqual({
      client_id: 'cafe-al-noor', drive_id: DRIVE, item_id: 'f-a11', folder_date: '2026-04-11',
    })  // prettier-ignore
    expect(await screen.findByRole('heading', { name: 'Run: Cafe Al Noor' })).toBeInTheDocument()
  })

  it('runs a folder with sub-folders, saying the run takes everything inside', async () => {
    const api = backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    await browseTo(user, 'April')

    expect(await screen.findByTestId('file-count')).toHaveTextContent(
      '1 invoice file here and 1 sub-folder: a run takes everything inside.',
    )
    expect(screen.getByLabelText('Folder date')).toHaveValue('2026-04-30')
    await user.click(screen.getByRole('button', { name: 'Run Cafe Al Noor: April' }))
    await waitFor(() => expect(api.bodies['POST /runs/sharepoint']).toMatchObject({ item_id: 'f-april' }))
  })

  it('never runs a whole library', async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    await open(user, 'Sites', 'ROAST HUB')
    await open(user, 'Libraries', 'Documents')

    expect(await screen.findByText(/Open a folder inside Documents first/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Run Cafe Al Noor/ })).toBeDisabled()
  })

  it('cannot run a folder without invoice files', async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    await browseTo(user, 'Supplier bills')

    expect(await screen.findByText('No invoice files in this folder.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Run Cafe Al Noor/ })).toBeDisabled()
    expect(screen.getByTestId('date-hint')).toHaveTextContent('The folder name has no date.')
  })

  it('goes back up with the folder path', async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    await browseTo(user, 'April', 'April 11')
    await user.click(within(screen.getByRole('navigation', { name: 'Folder path' })).getByRole('button', { name: 'April' }))

    expect(screen.getByLabelText('Folder date')).toHaveValue('2026-04-30')
    expect(await screen.findByRole('button', { name: /^April 11/ })).toBeInTheDocument()
  })

  it('opens a pasted SharePoint link straight at its folder', async () => {
    const api = backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    await user.type(await screen.findByRole('textbox', { name: 'SharePoint folder link' }), 'https://missan.sharepoint.com/:f:/s/ROASTHUB/April')
    await user.click(screen.getByRole('button', { name: 'Open' }))

    expect(await screen.findByRole('navigation', { name: 'Folder path' })).toHaveTextContent('All sites' + 'April')
    expect(api.calls).toContain('GET /sharepoint/link?url=https%3A%2F%2Fmissan.sharepoint.com%2F%3Af%3A%2Fs%2FROASTHUB%2FApril')
    expect(screen.getByLabelText('Folder date')).toHaveValue('2026-04-30')
  })

  it('says why a pasted link could not be opened', async () => {
    backend({ '/sharepoint/link': reply(409, { detail: 'this link is to a file: paste the link of the folder it is in' }) })
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    await user.type(await screen.findByRole('textbox', { name: 'SharePoint folder link' }), 'https://missan.sharepoint.com/:b:/x')
    await user.click(screen.getByRole('button', { name: 'Open' }))

    expect(await screen.findByText('This link could not be opened')).toBeInTheDocument()
    expect(screen.getByText(/link is to a file/)).toBeInTheDocument()
  })

  it('sends no folder date when the user clears it', async () => {
    const api = backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    await browseTo(user, 'April', 'April 11')
    await user.clear(screen.getByLabelText('Folder date'))

    expect(screen.getByTestId('date-hint')).toHaveTextContent('the period check is skipped')
    await user.click(screen.getByRole('button', { name: /^Run Cafe Al Noor/ }))
    await waitFor(() => expect(api.bodies['POST /runs/sharepoint']).toMatchObject({ folder_date: null }))
  })

  it('says why the run did not start', async () => {
    backend({ 'POST /runs/sharepoint': reply(409, { detail: 'not found in SharePoint (moved, renamed or deleted?)' }) })
    const user = userEvent.setup()
    const router = renderApp('/run')

    await pickClient(user)
    await browseTo(user, 'April', 'April 11')
    await user.click(screen.getByRole('button', { name: /^Run Cafe Al Noor/ }))

    expect(await screen.findByText('The run did not start')).toBeInTheDocument()
    expect(screen.getByText(/not found in SharePoint/)).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/run')
  })

  it('says why a folder could not be opened', async () => {
    backend({ '/sharepoint/sites': reply(409, { detail: 'you do not have access to this in SharePoint' }) })
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)

    expect(await screen.findByText('SharePoint sites could not be loaded')).toBeInTheDocument()
    expect(screen.getByText('you do not have access to this in SharePoint')).toBeInTheDocument()
  })

  it('remembers the last SharePoint folder per client and opens it again', async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)
    await browseTo(user, 'April', 'April 11')
    expect(JSON.parse(localStorage.getItem('clearbook.lastSharePoint.cafe-al-noor') ?? '[]')).toMatchObject([
      { kind: 'site', name: 'ROAST HUB' }, { name: 'Documents' }, { name: 'ROAST' }, { name: 'April' },
      { name: 'April 11', itemId: 'f-a11' },
    ])  // prettier-ignore
    cleanup()

    renderApp('/run')
    await pickClient(user)
    expect(await screen.findByRole('navigation', { name: 'Folder path' })).toHaveTextContent('April 11')
    expect(screen.getByLabelText('Folder date')).toHaveValue('2026-04-11')
  })

  it('asks an admin to connect SharePoint, coming back to the Run page', async () => {
    backend({ '/sharepoint/status': { ...SHAREPOINT, connected: false, account_name: null } })
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)

    expect(await screen.findByRole('link', { name: 'Connect SharePoint' })).toHaveAttribute(
      'href', '/api/sharepoint/connect?back=run',
    )  // prettier-ignore
    expect(screen.queryByRole('textbox', { name: 'SharePoint folder link' })).not.toBeInTheDocument()
  })

  it('tells an accountant an admin connects SharePoint, and offers this computer meanwhile', async () => {
    backend({
      '/auth/me': { email: 'acc@missan.test', name: 'Accountant', role: 'ACCOUNTANT' },
      '/sharepoint/status': { ...SHAREPOINT, needs_reconnect: true, reconnect_reason: 'Microsoft no longer accepts the SharePoint sign-in' },
    })
    const user = userEvent.setup()
    renderApp('/run')

    await pickClient(user)

    expect(await screen.findByText(/An admin connects it on the Clients page/)).toHaveTextContent(
      'SharePoint needs to be connected again: Microsoft no longer accepts the SharePoint sign-in.',
    )
    expect(screen.queryByRole('link', { name: /Connect SharePoint/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'On my computer' }))
    expect(screen.getByRole('button', { name: 'Choose folder' })).toBeInTheDocument()
  })

  it('offers only this computer when SharePoint is not set up', async () => {
    backend({ '/sharepoint/status': { ...SHAREPOINT, configured: false, connected: false } })
    const user = userEvent.setup()
    renderApp('/run')

    await user.click(await screen.findByRole('combobox', { name: 'Client' }))
    await user.click(await screen.findByRole('option', { name: 'Cafe Al Noor' }))

    expect(await screen.findByRole('button', { name: 'Choose folder' })).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Where the documents are' })).not.toBeInTheDocument()
  })

  it('says it is connected after the SharePoint sign-in, or why not', async () => {
    backend()
    renderApp('/run?sharepoint=Made-up+Admin')
    expect(await screen.findByText('Connected to SharePoint')).toBeInTheDocument()
    expect(screen.getByText(/As Made-up Admin. Read only/)).toBeInTheDocument()
  })
})

describe('run screen', () => {
  it('creates a client from a name and picks it straight away', async () => {
    const api = backend()
    const user = userEvent.setup()
    renderApp('/run')

    await user.click(await screen.findByRole('button', { name: 'New client' }))
    await user.type(screen.getByLabelText('New client name'), 'Blue Sky')
    await user.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(api.bodies['POST /clients']).toEqual({ name: 'Blue Sky' }))
    expect(await screen.findByRole('textbox', { name: 'SharePoint folder link' })).toBeInTheDocument()
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

  it('says how to add the first client', async () => {
    backend({ '/clients': [] })
    renderApp('/run')

    expect(await screen.findByText(/No clients yet/)).toHaveTextContent('add one on the Run screen')
  })
})

/** A file as the browser gives it for a chosen folder: its path inside the folder included. */
function chosen(path: string, size = 1000): File {
  const file = new File([new Uint8Array(size)], path.split('/').at(-1) ?? path)
  Object.defineProperty(file, 'webkitRelativePath', { value: path })
  return file
}

/** Stands in for the browser's XMLHttpRequest, which the upload uses for its progress. */
class FakeUpload {
  static sent: { url: string; form: FormData; headers: Record<string, string> }[] = []
  static answer: { status: number; body: unknown } = { status: 202, body: {} }
  upload: { onprogress: ((event: ProgressEvent) => void) | null } = { onprogress: null }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  status = 0
  statusText = ''
  responseText = ''
  private url = ''
  private headers: Record<string, string> = {}

  open(_method: string, url: string) {
    this.url = url
  }

  setRequestHeader(name: string, value: string) {
    this.headers[name] = value
  }

  send(form: FormData) {
    FakeUpload.sent.push({ url: this.url, form, headers: this.headers })
    this.upload.onprogress?.({ lengthComputable: true, loaded: 1, total: 2 } as ProgressEvent)
    setTimeout(() => {
      this.status = FakeUpload.answer.status
      this.responseText = JSON.stringify(FakeUpload.answer.body)
      this.onload?.()
    }, 0)
  }
}

describe('run screen: documents on my computer', () => {
  const realXhr = globalThis.XMLHttpRequest
  beforeEach(() => {
    FakeUpload.sent = []
    FakeUpload.answer = {
      status: 202,
      body: { run_id: RUN_ID, status: 'RUNNING', folder: 'October 2026', folder_date: '2026-10-31', files: 2, skipped: [] },
    }
    globalThis.XMLHttpRequest = FakeUpload as unknown as typeof XMLHttpRequest
  })
  afterEach(() => {
    globalThis.XMLHttpRequest = realXhr
  })

  async function chooseFolder(user: ReturnType<typeof userEvent.setup>, files: File[]) {
    await pickClient(user, 'Cafe Al Noor', 'On my computer')
    await user.upload(screen.getByLabelText('Folder to upload'), files)
  }

  it('uploads a chosen folder with its sub-folders and the date from its name, then shows the run', async () => {
    backend({ '/folders/date': { date: '2026-10-31' } })
    const user = userEvent.setup()
    const router = renderApp('/run')

    await chooseFolder(user, [
      chosen('October 2026/21/bill.pdf', 2048),
      chosen('October 2026/photo.jpg', 2048),
      chosen('October 2026/totals.xlsx'),
    ])

    expect(screen.getByTestId('chosen')).toHaveTextContent('2 invoice files (4 KB) in “October 2026”, sub-folders included.')
    expect(screen.getByTestId('chosen')).toHaveTextContent('1 other file is left out: totals.xlsx')
    expect(await screen.findByDisplayValue('2026-10-31')).toBeInTheDocument()
    expect(screen.getByTestId('date-hint')).toHaveTextContent('Taken from the folder name.')

    await user.click(screen.getByRole('button', { name: 'Upload and run Cafe Al Noor: October 2026' }))

    await waitFor(() => expect(router.state.location.pathname).toBe(`/run/${RUN_ID}`))
    const [sent] = FakeUpload.sent
    expect(sent?.url).toBe('/api/runs/upload')
    expect(sent?.headers['X-Clearbook']).toBe('1')
    expect(sent?.form.get('client_id')).toBe('cafe-al-noor')
    expect(sent?.form.get('folder_date')).toBe('2026-10-31')
    expect(JSON.parse(String(sent?.form.get('paths')))).toEqual(['October 2026/21/bill.pdf', 'October 2026/photo.jpg'])
    expect(sent?.form.getAll('files')).toHaveLength(2)
  })

  it('cannot upload a choice without invoice files', async () => {
    backend({ '/folders/date': { date: null } })
    const user = userEvent.setup()
    renderApp('/run')

    await chooseFolder(user, [chosen('Notes/plan.docx')])

    expect(screen.getByTestId('chosen')).toHaveTextContent('No invoice files (PDF or photo) in what you chose.')
    expect(screen.getByRole('button', { name: /^Upload and run/ })).toBeDisabled()
  })

  it('says why the upload did not finish', async () => {
    backend({ '/folders/date': { date: null } })
    FakeUpload.answer = { status: 422, body: { detail: 'at most 1024 MB in one upload' } }
    const user = userEvent.setup()
    renderApp('/run')

    await chooseFolder(user, [chosen('Bills/a.pdf')])
    await user.click(screen.getByRole('button', { name: 'Upload and run Cafe Al Noor: Bills' }))

    expect(await screen.findByText('The upload did not finish')).toBeInTheDocument()
    expect(screen.getByText('at most 1024 MB in one upload')).toBeInTheDocument()
  })
})

describe('run screen: the client\'s accounting system', () => {
  async function pickOnly(user: ReturnType<typeof userEvent.setup>, name = 'Cafe Al Noor') {
    await user.click(await screen.findByRole('combobox', { name: 'Client' }))
    await user.click(await screen.findByRole('option', { name }))
  }

  it('shows a synced client as ready and lets the documents be chosen', async () => {
    backend()
    const user = userEvent.setup()
    renderApp('/run')

    await pickOnly(user)

    const status = await screen.findByLabelText('Accounting system')
    expect(await within(status).findByText('Zoho Books: Missan Trading. Accounts synced (42).')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'SharePoint folder link' })).toBeInTheDocument()
  })

  it('syncs on the page before the documents can be chosen', async () => {
    let status = NOT_SYNCED
    const api = backend({
      '/clients/cafe-al-noor/accounting': () => status,
      'POST /clients/cafe-al-noor/accounting/sync': () => {
        status = SYNCED
        return { accounts: {}, tax_rates: {}, contacts: {}, deactivated: 0, tax_mapping: [] }
      },
    })
    const user = userEvent.setup()
    renderApp('/run')

    await pickOnly(user)
    expect(await screen.findByText(/not synced yet/)).toBeInTheDocument()
    expect(screen.getByText(/First get the client's accounting system ready/)).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'SharePoint folder link' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Sync now' }))

    expect(await screen.findByRole('textbox', { name: 'SharePoint folder link' })).toBeInTheDocument()
    expect(api.calls).toContain('POST /clients/cafe-al-noor/accounting/sync')
  })

  it('offers every set-up accounting system, coming back to the Run page', async () => {
    backend({ '/clients/cafe-al-noor/accounting': NOT_CONNECTED })
    const user = userEvent.setup()
    renderApp('/run')

    await pickOnly(user)

    expect(await screen.findByRole('link', { name: 'Connect Xero' })).toHaveAttribute(
      'href', '/api/xero/connect?client_id=cafe-al-noor&back=run',
    )  // prettier-ignore
    expect(screen.getByRole('link', { name: 'Connect Zoho Books' })).toHaveAttribute(
      'href', '/api/zoho/connect?client_id=cafe-al-noor&back=run',
    )  // prettier-ignore
    expect(screen.queryByRole('textbox', { name: 'SharePoint folder link' })).not.toBeInTheDocument()
  })

  it('runs a client without an accounting system on its imported accounts', async () => {
    backend({ '/clients/cafe-al-noor/accounting': { ...NOT_CONNECTED, accounts: 12 } })
    const user = userEvent.setup()
    renderApp('/run')

    await pickOnly(user)

    expect(await screen.findByText('No accounting system: uses the 12 imported accounts.')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'SharePoint folder link' })).toBeInTheDocument()
  })

  it('syncs a synced client again for new accounts or suppliers', async () => {
    let status = SYNCED
    const api = backend({
      '/clients/cafe-al-noor/accounting': () => status,
      'POST /clients/cafe-al-noor/accounting/sync': () => {
        status = { ...SYNCED, accounts: 44 }
        return { accounts: {}, tax_rates: {}, contacts: {}, deactivated: 0, tax_mapping: [] }
      },
    })
    const user = userEvent.setup()
    renderApp('/run')

    await pickOnly(user)
    await user.click(await screen.findByRole('button', { name: 'Sync again' }))

    expect(await screen.findByText('Zoho Books: Missan Trading. Accounts synced (44).')).toBeInTheDocument()
    expect(api.calls).toContain('POST /clients/cafe-al-noor/accounting/sync')
  })

  it('lets an accountant sync, but not connect', async () => {
    backend({
      '/auth/me': { email: 'acc@missan.test', name: 'Accountant', role: 'ACCOUNTANT' },
      '/clients/cafe-al-noor/accounting': NOT_SYNCED,
    })
    const user = userEvent.setup()
    renderApp('/run')

    await pickOnly(user)

    expect(await screen.findByRole('button', { name: 'Sync now' })).toBeInTheDocument()
  })

  it('asks an accountant to get an admin, without buttons they cannot use', async () => {
    backend({
      '/auth/me': { email: 'acc@missan.test', name: 'Accountant', role: 'ACCOUNTANT' },
      '/clients/cafe-al-noor/accounting': NOT_CONNECTED,
    })
    const user = userEvent.setup()
    renderApp('/run')

    await pickOnly(user)

    expect(await screen.findByText('Ask an admin to connect this client to its accounting system.')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /^Connect/ })).not.toBeInTheDocument()
  })

  it('back from connecting: the client is picked again and its accounts are synced', async () => {
    let status = NOT_SYNCED
    const api = backend({
      '/clients/cafe-al-noor/accounting': () => status,
      'POST /clients/cafe-al-noor/accounting/sync': () => {
        status = SYNCED
        return { accounts: {}, tax_rates: {}, contacts: {}, deactivated: 0, tax_mapping: [] }
      },
    })
    const router = renderApp('/run?client=cafe-al-noor&connected=Missan+Trading&system=Zoho+Books')

    expect(await screen.findByText('Connected to Zoho Books')).toBeInTheDocument()
    expect(await screen.findByRole('textbox', { name: 'SharePoint folder link' })).toBeInTheDocument()
    expect(api.calls).toContain('POST /clients/cafe-al-noor/accounting/sync')
    expect(screen.getByRole('combobox', { name: 'Client' })).toHaveTextContent('Cafe Al Noor')
    expect(router.state.location.search).toBe('')
  })

  it('says why connecting failed, on the Run page', async () => {
    backend({ '/clients/cafe-al-noor/accounting': NOT_CONNECTED })
    renderApp('/run?client=cafe-al-noor&error=Zoho+Books+sign-in+was+not+completed')

    expect(await screen.findByText('Zoho Books sign-in was not completed')).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Connect Zoho Books' })).toBeInTheDocument()
  })

  it('asks before leaving with chosen documents that were not uploaded', async () => {
    backend({ '/folders/date': { date: null } })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = userEvent.setup()
    const router = renderApp('/run')

    await pickOnly(user)
    await user.click(within(await screen.findByRole('group', { name: 'Where the documents are' })).getByRole('button', { name: 'On my computer' }))
    await user.upload(await screen.findByLabelText('Folder to upload'), [chosen('Bills/a.pdf')])
    await user.click(screen.getByRole('link', { name: /review queue/i }))

    expect(confirm).toHaveBeenCalledWith('You chose documents but did not upload them. Leave anyway?')
    expect(router.state.location.pathname).toBe('/run')
    confirm.mockRestore()
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

  it('shows how far fetching from SharePoint got before the files are run', async () => {
    backend({
      [`/runs/${RUN_ID}`]: progress({
        state: 'fetching', files: [], files_found: 0, counts: {}, folder: 'April 11',
        sharepoint_path: 'ROAST/April/April 11', fetched: 12, fetch_total: 37,
      }),
    })  // prettier-ignore
    renderApp(`/run/${RUN_ID}`)

    expect(await screen.findByText('Fetching from SharePoint: 12 of 37 files')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Fetching from SharePoint' })).toBeInTheDocument()
    expect(screen.getByText('on SharePoint')).toHaveAttribute('title', 'ROAST/April/April 11')
    expect(screen.queryByText('No documents found in this folder.')).not.toBeInTheDocument()
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
