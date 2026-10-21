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
export type DocumentStatus = Schemas['DocumentStatus']
export type DocumentType = Schemas['DocumentType']
export type QueueCategory = Schemas['QueueCategory']
export type DocumentSummary = Schemas['DocumentSummary']
export type QueuePage = Schemas['QueuePageOut']

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

function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
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
  startRun: (body: RunCreate) => postJson<RunAccepted>('/runs', body),
  run: (runId: string) => request<RunProgress>(`/runs/${encodeURIComponent(runId)}`),
  /** Exception first, then Check, then Ready; oldest first inside each. */
  queue: (query: QueueQuery) => request<QueuePage>(`/documents?${searchParams(query)}`),
}

/** Query string from an object; undefined values are left out. */
function searchParams(values: object): URLSearchParams {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined) params.set(key, String(value))
  }
  return params
}
