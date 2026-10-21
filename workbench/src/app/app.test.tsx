import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import type { Readiness } from '@/api/client'
import { mockFetch, renderApp } from '@/test/render'

const ALL_OK: Readiness = { status: 'ok', database: 'ok', redis: 'ok', model: 'ok' }
const realFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = realFetch
})

describe('layout and navigation', () => {
  it('opens on the Run page', async () => {
    mockFetch(ALL_OK)
    const router = renderApp('/')

    await waitFor(() => expect(router.state.location.pathname).toBe('/run'))
    expect(screen.getByRole('heading', { name: 'Clearbook' })).toBeInTheDocument()
    expect(screen.getByText(/pick a restaurant and a folder/i)).toBeInTheDocument()
  })

  it('navigates to the review queue', async () => {
    mockFetch(ALL_OK)
    const router = renderApp('/run')

    await userEvent.click(screen.getByRole('link', { name: /review queue/i }))

    expect(router.state.location.pathname).toBe('/queue')
    expect(screen.getByText(/exception first/i)).toBeInTheDocument()
  })

  it('shows a not-found page for unknown paths', () => {
    mockFetch(ALL_OK)
    renderApp('/nowhere')

    expect(screen.getByRole('heading', { name: /page not found/i })).toBeInTheDocument()
  })
})

describe('system status', () => {
  it('shows every dependency as ready', async () => {
    mockFetch(ALL_OK)
    renderApp('/run')

    const status = await screen.findByRole('list', { name: /system status/i })
    expect(status).toHaveTextContent('Database: Ready')
    expect(status).toHaveTextContent('Job queue: Ready')
    expect(status).toHaveTextContent('AI model: Ready')
  })

  it('shows what is down when readiness answers 503', async () => {
    mockFetch({ ...ALL_OK, status: 'unavailable', database: 'unavailable' }, 503)
    renderApp('/run')

    const status = await screen.findByRole('list', { name: /system status/i })
    expect(status).toHaveTextContent('Database: Unavailable')
    expect(status).toHaveTextContent('Job queue: Ready')
  })

  it('explains a wrong model variant', async () => {
    mockFetch({ ...ALL_OK, model: 'incompatible' })
    renderApp('/run')

    expect(await screen.findByText(/AI model: Wrong model \(needs Instruct\)/)).toBeInTheDocument()
  })

  it('says when the API itself is not reachable', async () => {
    globalThis.fetch = (async () => {
      throw new TypeError('Failed to fetch')
    }) as typeof fetch
    renderApp('/run')

    expect(await screen.findByRole('status')).toHaveTextContent(/API not reachable/)
  })
})
