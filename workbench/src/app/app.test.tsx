import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { Readiness } from '@/api/client'
import { mockApi, renderApp, reply } from '@/test/render'

const ALL_OK: Readiness = { status: 'ok', database: 'ok', redis: 'ok', model: 'ok' }

function mockReadiness(readiness: Readiness, status = 200) {
  mockApi({ '/health/ready': reply(status, readiness), '/clients': [] })
}
const realFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = realFetch
})

describe('layout and navigation', () => {
  it('opens on the Run page', async () => {
    mockReadiness(ALL_OK)
    const router = renderApp('/')

    await waitFor(() => expect(router.state.location.pathname).toBe('/run'))
    expect(screen.getByRole('heading', { name: 'Clearbook' })).toBeInTheDocument()
    expect(await screen.findByText(/pick a client and a folder/i)).toBeInTheDocument()
  })

  it('navigates to the review queue', async () => {
    mockReadiness(ALL_OK)
    const router = renderApp('/run')

    await userEvent.click(await screen.findByRole('link', { name: /review queue/i }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/queue'))
    expect(await screen.findByText(/exception first/i)).toBeInTheDocument()
    await waitFor(() => expect(document.title).toBe('Review queue – Clearbook'))
  })

  it('shows a not-found page for unknown paths', () => {
    mockReadiness(ALL_OK)
    renderApp('/nowhere')

    expect(screen.getByRole('heading', { name: /page not found/i })).toBeInTheDocument()
  })
})

describe('sidebar', () => {
  beforeEach(() => localStorage.clear())

  it('is open on most pages and folds to icons on the review screen', async () => {
    mockReadiness(ALL_OK)
    renderApp('/documents/some-document')

    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(nav).not.toHaveTextContent('Review queue') // icons only
    expect(within(nav).getByRole('link', { name: 'Review queue' })).toHaveAttribute('title', 'Review queue')
    expect(await screen.findByText('All systems ready')).toBeInTheDocument() // compact header
  })

  it('remembers the reader opening or closing it', async () => {
    mockReadiness(ALL_OK)
    const user = userEvent.setup()
    renderApp('/run')

    await user.click(await screen.findByRole('button', { name: 'Collapse sidebar' }))

    expect(screen.getByRole('navigation', { name: 'Main' })).not.toHaveTextContent('Review queue')
    expect(localStorage.getItem('clearbook.sidebar')).toBe('closed')
  })
})

describe('system status', () => {
  it('shows every dependency as ready', async () => {
    mockReadiness(ALL_OK)
    renderApp('/run')

    const status = await screen.findByRole('list', { name: /system status/i })
    expect(status).toHaveTextContent('Database: Ready')
    expect(status).toHaveTextContent('Job queue: Ready')
    expect(status).toHaveTextContent('AI model: Ready')
  })

  it('shows what is down when readiness answers 503', async () => {
    mockReadiness({ ...ALL_OK, status: 'unavailable', database: 'unavailable' }, 503)
    renderApp('/run')

    const status = await screen.findByRole('list', { name: /system status/i })
    expect(status).toHaveTextContent('Database: Unavailable')
    expect(status).toHaveTextContent('Job queue: Ready')
  })

  it('explains a wrong model variant', async () => {
    mockReadiness({ ...ALL_OK, model: 'incompatible' })
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
