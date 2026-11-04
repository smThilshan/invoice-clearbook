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
    expect(await screen.findByText(/pick a client, choose the documents/i)).toBeInTheDocument()
  })

  it('navigates to the review queue', async () => {
    mockReadiness(ALL_OK)
    const router = renderApp('/run')

    await userEvent.click(await screen.findByRole('link', { name: /review queue/i }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/queue'))
    expect(await screen.findByText(/exception first/i)).toBeInTheDocument()
    await waitFor(() => expect(document.title).toBe('Review queue – Clearbook'))
  })

  it('shows a not-found page for unknown paths', async () => {
    mockReadiness(ALL_OK)
    renderApp('/nowhere')

    expect(await screen.findByRole('heading', { name: /page not found/i })).toBeInTheDocument()
  })
})

describe('sidebar', () => {
  beforeEach(() => localStorage.clear())

  it('is open on most pages and folds to icons on the review screen', async () => {
    mockReadiness(ALL_OK)
    renderApp('/documents/some-document')

    // Folded: the labels are hidden by CSS on wide screens (kept on phones, where the menu is a
    // top bar), so the test reads the fold from what the reader can use: the toggle offers to
    // expand, and each icon keeps its name and a hover title.
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(await screen.findByRole('button', { name: 'Expand sidebar' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Review queue' })).toHaveAttribute('title', 'Review queue')
  })

  it('remembers the reader opening or closing it', async () => {
    mockReadiness(ALL_OK)
    const user = userEvent.setup()
    renderApp('/run')

    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: 'Run' })).not.toHaveAttribute('title') // open
    await user.click(await screen.findByRole('button', { name: 'Collapse sidebar' }))

    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Run' })).toHaveAttribute('title', 'Run') // icons
    expect(localStorage.getItem('clearbook.sidebar')).toBe('closed')
  })
})

describe('theme', () => {
  beforeEach(() => localStorage.clear())

  it('is light unless dark is chosen, and remembers the choice', async () => {
    mockReadiness(ALL_OK)
    const user = userEvent.setup()
    renderApp('/run')

    const theme = await screen.findByRole('group', { name: 'Theme' })
    expect(within(theme).getByRole('button', { name: 'Light' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(within(theme).getByRole('button', { name: 'Dark' }))

    expect(document.documentElement).toHaveClass('dark')
    expect(localStorage.getItem('clearbook.theme')).toBe('dark')
    await user.click(within(theme).getByRole('button', { name: 'Light' }))
    expect(document.documentElement).not.toHaveClass('dark')
  })
})

describe('system status', () => {
  it('shows nothing while everything is running', async () => {
    mockReadiness(ALL_OK)
    renderApp('/run')

    expect(await screen.findByRole('heading', { name: 'Clearbook' })).toBeInTheDocument()
    expect(screen.queryByRole('alert', { name: 'System problem' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Database|Job queue|AI model/)).not.toBeInTheDocument()
  })

  it('says in plain words what does not work and who to ask', async () => {
    mockReadiness({ ...ALL_OK, model: 'unreachable' })
    renderApp('/run')

    const banner = await screen.findByRole('alert', { name: 'System problem' })
    expect(banner).toHaveTextContent(
      'Invoices cannot be read right now. Reviews, approvals and sending still work. Ask the person who runs the system to start it.',
    )
    expect(banner).toHaveAttribute('title', 'AI model: model server not reachable (make llm-load)')
  })

  it('puts the worst problem first when several are down', async () => {
    mockReadiness({ ...ALL_OK, status: 'unavailable', database: 'unavailable', redis: 'unavailable' }, 503)
    renderApp('/run')

    const banner = await screen.findByRole('alert', { name: 'System problem' })
    expect(banner.textContent).toMatch(/^Nothing can be loaded or saved right now\. New runs/)
  })

  it('says when the API itself is not reachable', async () => {
    globalThis.fetch = (async () => {
      throw new TypeError('Failed to fetch')
    }) as typeof fetch
    renderApp('/run')

    expect(await screen.findByRole('alert', { name: 'System problem' })).toHaveTextContent(
      /cannot reach its service/,
    )
  })
})
