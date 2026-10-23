import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'

import { routes } from '@/app/routes'

/** The real app routes in memory, with a fresh query cache per test. `state`: what the page
 * was opened with (e.g. the list a review came from). */
export function renderApp(path = '/', state?: unknown) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: [{ pathname: path.split('?')[0], search: path.includes('?') ? `?${path.split('?')[1]}` : '', state }] })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

export interface Reply {
  status?: number
  body: unknown
}

/** What the fake API answers: a JSON body (200), a Reply, or a function of the request. */
export type Answer = unknown | ((request: Request) => unknown)

export interface FakeApi {
  /** Every request made, as "METHOD /path?query" (without the /api prefix). */
  calls: string[]
  /** JSON bodies sent, by "METHOD /path". */
  bodies: Record<string, unknown>
}

/**
 * Replace fetch with a fake backend. Keys are "/path" (any method) or "METHOD /path"; the
 * query string is ignored for matching. Unknown paths answer 404, like the real API.
 */
export function mockApi(answers: Record<string, Answer>): FakeApi {
  const fake: FakeApi = { calls: [], bodies: {} }
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(new URL(String(input), 'http://localhost'), init)
    const url = new URL(request.url)
    const path = url.pathname.replace(/^\/api/, '')
    fake.calls.push(`${request.method} ${path}${url.search}`)
    if (init?.body) fake.bodies[`${request.method} ${path}`] = JSON.parse(String(init.body))
    const answer = answers[`${request.method} ${path}`] ?? answers[path]
    if (answer === undefined) return json({ detail: 'Not Found' }, 404)
    const result = typeof answer === 'function' ? answer(request) : answer
    return isReply(result) ? json(result.body, result.status ?? 200) : json(result, 200)
  }) as typeof fetch
  return fake
}

/** A reply with a status, for errors: `reply(404, { detail: 'folder not found' })`. */
export function reply(status: number, body: unknown): Reply {
  return { status, body, [REPLY]: true } as Reply
}

const REPLY = Symbol('reply')

function isReply(value: unknown): value is Reply {
  return typeof value === 'object' && value !== null && REPLY in value
}

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
