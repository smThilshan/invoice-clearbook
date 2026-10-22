/**
 * API client. Types come from the backend's OpenAPI description (`make web-types`), so a
 * changed field in the backend is a compile error here, not a runtime surprise.
 * Requests go to /api, which Vite forwards to the FastAPI server (localhost only).
 */
import type { components } from './schema'

export type Schemas = components['schemas']
export type Readiness = Schemas['Readiness']
export type ModelStatus = Schemas['ModelStatus']
export type Restaurant = Schemas['ClientOut']
export type Folder = Schemas['Folder']
export type FolderListing = Schemas['FolderListing']
export type RunCreate = Schemas['RunCreate']
export type RunAccepted = Schemas['RunAccepted']
export type RunProgress = Schemas['RunProgressOut']
export type FileStatus = Schemas['FileStatus']
export type StepTimings = Schemas['StepTimingsOut']
export type DocumentStatus = Schemas['DocumentStatus']
export type DocumentType = Schemas['DocumentType']
export type QueueCategory = Schemas['QueueCategory']
export type DocumentSummary = Schemas['DocumentSummary']
export type QueuePage = Schemas['QueuePageOut']
export type DocumentDetail = Schemas['DocumentDetail']
export type Invoice = Schemas['Invoice-Output']
export type InvoiceInput = Schemas['Invoice-Input']
export type InvoiceLine = Schemas['Line-Output']
export type Issue = Schemas['IssueOut']
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

export interface QueueQuery {
  client_id: string
  queue?: QueueCategory
  status?: DocumentStatus
  run_id?: string
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

interface RequestOptions extends RequestInit {
  /** Non-2xx statuses whose JSON body is still the answer (e.g. 503 from readiness). */
  acceptStatuses?: number[]
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { acceptStatuses = [], headers, ...init } = options
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { Accept: 'application/json', ...headers },
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

const documentPath = (id: string) => `/documents/${encodeURIComponent(id)}`

/** The rendered page picture (PNG), for an <img>; pages are numbered from 1. */
export function pageImageUrl(documentId: string, page: number): string {
  return `/api${documentPath(documentId)}/pages/${page}/image`
}

export const api = {
  /** Readiness answers 503 (with the same body) when Postgres or Redis is down. */
  readiness: () => request<Readiness>('/health/ready', { acceptStatuses: [503] }),
  restaurants: () => request<Restaurant[]>('/clients'),
  /** Sub-folders of `path` inside the restaurant's shared-drive folder ('' = its top). */
  folders: (clientId: string, path: string) =>
    request<FolderListing>(
      `/clients/${encodeURIComponent(clientId)}/folders?${new URLSearchParams({ path })}`,
    ),
  startRun: (body: RunCreate) => sendJson<RunAccepted>('POST', '/runs', body),
  run: (runId: string) => request<RunProgress>(`/runs/${encodeURIComponent(runId)}`),
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
  rejectDocument: (id: string, body: RejectBody) =>
    sendJson<DocumentDetail>('POST', `${documentPath(id)}/reject`, body),
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
