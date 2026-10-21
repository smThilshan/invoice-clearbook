/**
 * API client. Types come from the backend's OpenAPI description (`make web-types`), so a
 * changed field in the backend is a compile error here, not a runtime surprise.
 * Requests go to /api, which Vite forwards to the FastAPI server (localhost only).
 */
import type { components } from './schema'

export type Schemas = components['schemas']
export type Readiness = Schemas['Readiness']
export type ModelStatus = Schemas['ModelStatus']

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

export const api = {
  /** Readiness answers 503 (with the same body) when Postgres or Redis is down. */
  readiness: () => request<Readiness>('/health/ready', { acceptStatuses: [503] }),
}
