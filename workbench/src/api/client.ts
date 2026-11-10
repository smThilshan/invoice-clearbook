/**
 * API client. Types come from the backend's OpenAPI description (`make web-types`), so a
 * changed field in the backend is a compile error here, not a runtime surprise.
 * Requests go to /api: Vite forwards them to the FastAPI server in development; live, the same
 * server answers both (decision 77).
 */
import type { components } from './schema'

export type Schemas = components['schemas']
export type Readiness = Schemas['Readiness']
export type Me = Schemas['MeOut']
export type Role = Schemas['Role']
export type LoginBody = Schemas['LoginBody']
export type ModelStatus = Schemas['ModelStatus']
export type Client = Schemas['ClientOut']
export type ClientCreate = Schemas['ClientCreate']
export type LineGrouping = Schemas['LineGrouping']
export type ExpensesSentAs = Schemas['ExpensesSentAs']
export type ClientSettings = Schemas['ClientSettings']
export type DeletionPreview = Schemas['DeletionPreviewOut']
export type CompanyDeleted = Schemas['CompanyDeleted']
export type AccountingStatus = Schemas['AccountingStatus']
export type SyncResult = Schemas['SyncOut']
export type PostingSummary = Schemas['PostingOut']
export type SharePointStatus = Schemas['SharePointStatus']
export type SharePointSite = Schemas['SiteOut']
export type SharePointLibrary = Schemas['LibraryOut']
export type SharePointFolder = Schemas['SharePointFolderOut']
export type SharePointListing = Schemas['SharePointListing']
export type SharePointRunCreate = Schemas['SharePointRunCreate']
export type RunOption = Schemas['RunOption']
export type RunAccepted = Schemas['RunAccepted']
export type UploadAccepted = Schemas['UploadAccepted']
export type FolderDate = Schemas['FolderDate']
export type RunProgress = Schemas['RunProgressOut']
export type FileStatus = Schemas['FileStatus']
export type FileStep = Schemas['Step']
export type StepTimings = Schemas['StepTimingsOut']
export type DocumentStatus = Schemas['DocumentStatus']
export type DocumentType = Schemas['DocumentType']
export type QueueCategory = Schemas['QueueCategory']
export type DocumentSummary = Schemas['DocumentSummary']
export type QueuePage = Schemas['QueuePageOut']
export type AnswerKey = Schemas['AnswerKeyOut']
export type DocumentDetail = Schemas['DocumentDetail']
export type Invoice = Schemas['Invoice-Output']
export type InvoiceInput = Schemas['Invoice-Input']
export type InvoiceLine = Schemas['Line-Output']
export type Issue = Schemas['IssueOut']
export type Charge = Schemas['ChargeOut']
export type Correction = Schemas['CorrectionOut']
export type CodingOptions = Schemas['CodingOptions']
export type DocumentUpdate = Schemas['DocumentUpdate']
export type RejectReason = Schemas['RejectReason']
export type RejectBody = Schemas['RejectBody']
export type AmountType = Schemas['AmountType']
export type TaxType = Schemas['TaxType']
export type ValueKind = Schemas['ValueKind']
export type Place = Schemas['Place']
export type Located = Schemas['Located']
export type Sending = Schemas['SendingOut']
export type SendingList = Schemas['SendingList']

export interface QueueQuery {
  client_id: string
  queue?: QueueCategory
  status?: DocumentStatus
  run_id?: string
  search?: string
  sending?: SendingList // a list of approved documents instead of `status` (decision 70)
  limit: number
  offset: number
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** Sent with every request: the API refuses a change without it, and another site cannot add
 * it, so a logged-in browser cannot be made to change anything from elsewhere (decision 71). */
const APP_HEADER = { 'X-Clearbook': '1' }

interface RequestOptions extends RequestInit {
  /** Non-2xx statuses whose JSON body is still the answer (e.g. 503 from readiness). */
  acceptStatuses?: number[]
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { acceptStatuses = [], headers, ...init } = options
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { Accept: 'application/json', ...APP_HEADER, ...headers },
  })
  if (!response.ok && !acceptStatuses.includes(response.status)) {
    throw new ApiError(response.status, await errorMessage(response))
  }
  return (await response.json()) as T
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // not JSON: fall through to the status text
  }
  return `${response.status} ${response.statusText}`.trim()
}

function sendJson<T>(method: 'POST' | 'PATCH', path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** A call answered with no content (204), e.g. a delete. */
async function sendNoContent(method: 'DELETE' | 'POST', path: string): Promise<void> {
  const response = await fetch(`/api${path}`, { method, headers: { Accept: 'application/json', ...APP_HEADER } })
  if (!response.ok) throw new ApiError(response.status, await errorMessage(response))
}

const documentPath = (id: string) => `/documents/${encodeURIComponent(id)}`

/** The rendered page picture (PNG), for an <img>; pages are numbered from 1. */
export function pageImageUrl(documentId: string, page: number): string {
  return `/api${documentPath(documentId)}/pages/${page}/image`
}

export const api = {
  /** Readiness answers 503 (with the same body) when Postgres or Redis is down. */
  readiness: () => request<Readiness>('/health/ready', { acceptStatuses: [503] }),
  /** Who is logged in; 401 when nobody is (decision 71). */
  me: () => request<Me>('/auth/me'),
  login: (body: LoginBody) => sendJson<Me>('POST', '/auth/login', body),
  logout: () => sendNoContent('POST', '/auth/logout'),
  clients: () => request<Client[]>('/clients'),
  /** Is the client connected to its accounting system (task 5.3)? */
  accounting: (clientId: string) =>
    request<AccountingStatus>(`/clients/${encodeURIComponent(clientId)}/accounting`),
  disconnectAccounting: (clientId: string) =>
    sendJson<AccountingStatus>('POST', `/clients/${encodeURIComponent(clientId)}/accounting/disconnect`, {}),
  /** Read accounts, VAT rates and suppliers from the connected accounting system (task 5.4). */
  syncAccounting: (clientId: string) =>
    sendJson<SyncResult>('POST', `/clients/${encodeURIComponent(clientId)}/accounting/sync`, {}),
  /** Ask the worker to post the client's approved documents now (task 5.1). */
  sendWaiting: (clientId: string) =>
    sendJson<AccountingStatus>('POST', `/clients/${encodeURIComponent(clientId)}/accounting/send`, {}),
  /** A new client from just a name; its id is made from the name (decision 35). */
  createClient: (body: ClientCreate) => sendJson<Client>('POST', '/clients', body),
  /** Decision 91: removed companies (admin only), hidden but kept, to restore. */
  removedClients: () => request<Client[]>('/clients/removed'),
  removeClient: (clientId: string) =>
    sendJson<Client>('POST', `/clients/${encodeURIComponent(clientId)}/remove`, {}),
  restoreClient: (clientId: string) =>
    sendJson<Client>('POST', `/clients/${encodeURIComponent(clientId)}/restore`, {}),
  /** What deleting a removed company permanently would remove (admin only). */
  deletionPreview: (clientId: string) =>
    request<DeletionPreview>(`/clients/${encodeURIComponent(clientId)}/deletion`),
  /** Cannot be undone; `name` is the company's name, typed to confirm. */
  deleteClient: (clientId: string, name: string) =>
    sendJson<CompanyDeleted>('POST', `/clients/${encodeURIComponent(clientId)}/delete`, { name }),
  /** Decision 73: how approved lines are sent; documents approved from now on (admin only). */
  setLineGrouping: (clientId: string, lineGrouping: LineGrouping) =>
    sendJson<Client>('PATCH', `/clients/${encodeURIComponent(clientId)}`, { line_grouping: lineGrouping }),
  /** Decision 89: how expenses are sent and the account usually paid through (admin only). */
  setExpenseSending: (clientId: string, settings: Pick<ClientSettings, 'expenses_sent_as' | 'paid_through'>) =>
    sendJson<Client>('PATCH', `/clients/${encodeURIComponent(clientId)}`, settings),
  /** Missan's SharePoint sign-in (decision 83): read only, made once by an admin. */
  sharePointStatus: () => request<SharePointStatus>('/sharepoint/status'),
  disconnectSharePoint: () => sendJson<SharePointStatus>('POST', '/sharepoint/disconnect', {}),
  /** The SharePoint sites the connected account can open. */
  sharePointSites: () => request<SharePointSite[]>('/sharepoint/sites'),
  sharePointLibraries: (siteId: string) =>
    request<SharePointLibrary[]>(`/sharepoint/sites/${encodeURIComponent(siteId)}/libraries`),
  /** What is in a SharePoint folder ('root': the library itself). */
  sharePointFolder: (driveId: string, itemId: string) =>
    request<SharePointListing>(`/sharepoint/folder?${new URLSearchParams({ drive_id: driveId, item_id: itemId })}`),
  /** The folder a pasted SharePoint address leads to. */
  sharePointLink: (url: string) => request<SharePointFolder>(`/sharepoint/link?${new URLSearchParams({ url })}`),
  /** Latest runs that took in documents, newest first (the queue's run filter). */
  runs: (clientId: string) => request<RunOption[]>(`/clients/${encodeURIComponent(clientId)}/runs`),
  /** Recent work time per file, for the estimate before a run (task 4.17). */
  typicalTime: () => request<{ typical_ms: number | null }>('/runs/typical-time'),
  /** Run a SharePoint folder, sub-folders included: the server fetches it, then runs it. */
  startSharePointRun: (body: SharePointRunCreate) => sendJson<RunAccepted>('POST', '/runs/sharepoint', body),
  /** The date a folder's name stands for. */
  folderDate: (name: string) => request<FolderDate>(`/folders/date?${new URLSearchParams({ name })}`),
  run: (runId: string) => request<RunProgress>(`/runs/${encodeURIComponent(runId)}`),
  /** Stop a run (task 4.19): files not started stay as they are; the current step finishes. */
  stopRun: (runId: string) => sendJson<RunProgress>('POST', `/runs/${encodeURIComponent(runId)}/stop`, {}),
  /** Abandon the AI read still under way in a stopped run (task 4.20). */
  forceStopRun: (runId: string) =>
    sendJson<RunProgress>('POST', `/runs/${encodeURIComponent(runId)}/force-stop`, {}),
  resumeRun: (runId: string) => sendJson<RunProgress>('POST', `/runs/${encodeURIComponent(runId)}/resume`, {}),
  /** Exception first, then Check, then Ready; oldest first inside each. */
  queue: (query: QueueQuery) => request<QueuePage>(`/documents?${searchParams(query)}`),
  document: (id: string) => request<DocumentDetail>(documentPath(id)),
  /** Save the accountant's version; the server runs every check again (no model). */
  updateDocument: (id: string, body: DocumentUpdate) =>
    sendJson<DocumentDetail>('PATCH', documentPath(id), body),
  /** Where a value is printed on the document (task 4.6). */
  locate: (id: string, kind: ValueKind, value: string) =>
    request<Located>(`${documentPath(id)}/locate?${new URLSearchParams({ kind, value })}`),
  /** Re-check, then APPROVED + outbox in one transaction (task 4.8); the document as last saved. */
  approveDocument: (id: string) => sendJson<DocumentDetail>('POST', `${documentPath(id)}/approve`, {}),
  /** A failed document starts over from reading (task 4.14). */
  retryDocument: (id: string) => sendJson<DocumentDetail>('POST', `${documentPath(id)}/retry`, {}),
  /** The AI reads a document in review or parked again; review work on it is replaced (decision 55). */
  reextractDocument: (id: string) => sendJson<DocumentDetail>('POST', `${documentPath(id)}/reextract`, {}),
  rejectDocument: (id: string, body: RejectBody) =>
    sendJson<DocumentDetail>('POST', `${documentPath(id)}/reject`, body),
  /** A send that gave up starts again, same key and saved progress (decision 69). */
  sendAgain: (id: string) => sendJson<DocumentDetail>('POST', `${documentPath(id)}/send-again`, {}),
  /** A send that gave up goes back to review with the reason (decision 69). */
  sendBack: (id: string) => sendJson<DocumentDetail>('POST', `${documentPath(id)}/send-back`, {}),
  /** A posted document deleted in the accounting system back to review, once it confirms (76). */
  reopenDeleted: (id: string) => sendJson<DocumentDetail>('POST', `${documentPath(id)}/reopen-deleted`, {}),
  /** A rejected document back to review, checked again (decision 45). */
  restoreDocument: (id: string) => sendJson<DocumentDetail>('POST', `${documentPath(id)}/restore`, {}),
  /** Delete a rejected document permanently, files included (decision 45). */
  deleteDocument: (id: string) => sendNoContent('DELETE', documentPath(id)),
  /** Is the document in the answer key, marked correct by the accountant (decision 63)? */
  answerKey: (id: string) => request<AnswerKey>(`${documentPath(id)}/answer-key`),
  /** Keep the document as last saved as its correct answer; nothing is approved or sent. */
  markCorrect: (id: string) => sendJson<AnswerKey>('POST', `${documentPath(id)}/answer-key`, {}),
  unmarkCorrect: (id: string) => request<AnswerKey>(`${documentPath(id)}/answer-key`, { method: 'DELETE' }),
  /** Accounts and tax codes the review form may use (the client's active master data). */
  codingOptions: (clientId: string) =>
    request<CodingOptions>(`/clients/${encodeURIComponent(clientId)}/coding-options`),
}

/** Query string from an object; undefined values are left out. */
function searchParams(values: object): URLSearchParams {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined) params.set(key, String(value))
  }
  return params
}

/** A file chosen on this computer, with its path inside the chosen folder ("October/21/a.pdf"). */
export interface ChosenFile {
  path: string
  file: File
}

/** Upload files chosen on this computer and start their run (decision 78). XMLHttpRequest, not
 * fetch: only it reports how much of the upload has been sent. */
export function uploadRun(
  clientId: string,
  files: ChosenFile[],
  folderDate: string | null,
  onProgress: (fraction: number) => void,
): Promise<UploadAccepted> {
  const form = new FormData()
  form.append('client_id', clientId)
  form.append('paths', JSON.stringify(files.map((chosen) => chosen.path)))
  if (folderDate) form.append('folder_date', folderDate)
  for (const chosen of files) form.append('files', chosen.file, chosen.file.name)

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/runs/upload')
    xhr.setRequestHeader('Accept', 'application/json')
    for (const [name, value] of Object.entries(APP_HEADER)) xhr.setRequestHeader(name, value)
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total)
    }
    xhr.onload = () => {
      let body: { detail?: unknown } | undefined
      try {
        body = JSON.parse(xhr.responseText) as { detail?: unknown }
      } catch {
        body = undefined
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as UploadAccepted)
      else {
        const detail = typeof body?.detail === 'string' ? body.detail : `${xhr.status} ${xhr.statusText}`.trim()
        reject(new ApiError(xhr.status, detail))
      }
    }
    xhr.onerror = () => reject(new ApiError(0, 'The upload was interrupted. Check the connection and try again.'))
    xhr.send(form)
  })
}
