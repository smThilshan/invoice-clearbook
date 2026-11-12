import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import type { AccountingStatus, Readiness } from '@/api/client'
import { mockApi, renderApp, reply } from '@/test/render'

const READY: Readiness = { status: 'ok', database: 'ok', redis: 'ok', model: 'ok' }
const ME = { email: 'accountant@missan.test', name: 'Made-up Accountant', role: 'ACCOUNTANT', must_change_password: false }
const NOBODY = reply(401, { detail: 'log in first' })

const realFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = realFetch
})

describe('login (decision 71)', () => {
  it('sends a reader who is not logged in to the login page, then back where they were', async () => {
    let loggedIn = false
    const api = mockApi({
      '/health/ready': READY,
      '/auth/me': () => (loggedIn ? ME : NOBODY),
      'POST /auth/login': () => {
        loggedIn = true
        return ME
      },
      '/clients': [],
    })
    const user = userEvent.setup()
    const router = renderApp('/queue?client=cafe')

    const form = await screen.findByRole('form', { name: 'Log in' })
    await user.type(within(form).getByLabelText('Email'), 'accountant@missan.test')
    await user.type(within(form).getByLabelText('Password'), 'made-up password 1')
    await user.click(within(form).getByRole('button', { name: 'Log in' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/queue'))
    expect(router.state.location.search).toBe('?client=cafe')
    expect(api.bodies['POST /auth/login']).toEqual({ email: 'accountant@missan.test',
                                                     password: 'made-up password 1' })  // prettier-ignore
    expect(await screen.findByText('Made-up Accountant')).toBeInTheDocument()
  })

  it('says why a login was refused', async () => {
    mockApi({
      '/health/ready': READY, '/auth/me': NOBODY,
      'POST /auth/login': reply(401, { detail: 'Wrong email or password.' }),
    })  // prettier-ignore
    const user = userEvent.setup()
    renderApp('/run')

    const form = await screen.findByRole('form', { name: 'Log in' })
    await user.type(within(form).getByLabelText('Email'), 'nobody@missan.test')
    await user.type(within(form).getByLabelText('Password'), 'not the password')
    await user.click(within(form).getByRole('button', { name: 'Log in' }))

    expect(await within(form).findByText('Wrong email or password.')).toBeInTheDocument()
  })

  it('goes to the login page when the session ends in the middle of the work', async () => {
    mockApi({ '/health/ready': READY, '/clients': NOBODY })
    const router = renderApp('/clients')

    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(await screen.findByRole('form', { name: 'Log in' })).toBeInTheDocument()
  })

  it('logs out from the header', async () => {
    let loggedIn = true
    const api = mockApi({
      '/health/ready': READY,
      '/auth/me': () => (loggedIn ? ME : NOBODY),
      'POST /auth/logout': () => {
        loggedIn = false
        return reply(204, null)
      },
      '/clients': [],
    })
    const user = userEvent.setup()
    const router = renderApp('/run')

    await user.click(await screen.findByRole('button', { name: 'Log out' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(api.calls).toContain('POST /auth/logout')
  })

  it('names this app on every request, so the API accepts its changes', async () => {
    const headers: (string | null)[] = []
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      headers.push(new Headers(init?.headers).get('X-Clearbook'))
      const path = new URL(String(input), 'http://localhost').pathname
      const body = path === '/api/auth/me' ? ME : path === '/api/health/ready' ? READY : []
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }) as typeof fetch
    renderApp('/run')

    await screen.findByText('Made-up Accountant')
    expect(headers.length).toBeGreaterThan(0)
    expect(headers.every((h) => h === '1')).toBe(true)
  })

  it('shows the accounting connection to an accountant without the admin buttons', async () => {
    const connected: AccountingStatus = {
      providers: [{ key: 'XERO', name: 'Xero', configured: true, connect_url: '/xero/connect' }],
      configured: true, connected: true, needs_reconnect: false, reconnect_reason: null,
      provider: 'XERO', provider_name: 'Xero', organisation: 'Made-up Org',
      connected_at: '2026-10-23T10:00:00Z', bills_where: 'Bills to pay, Awaiting approval',
      accounts: 0, master_data_from: 'XERO', waiting: 0,
      expenses_blocked: 'Xero cannot take expenses yet: they are sent as bills',
      expenses_reconnect: false, expenses_where: null,
      posting: { ready: 0, retrying: 0, retry_at: null, retry_reason: null, not_yet: {}, sent_back: 0,
                 posted: 0, paused: 0, paused_reason: null, failed: 0, failed_reason: null },
    }  // prettier-ignore
    mockApi({
      '/health/ready': READY, '/auth/me': ME,
      '/clients': [{ client_id: 'cafe', name: 'Cafe Al Noor', line_grouping: 'BY_ITEM', expenses_sent_as: 'BILL', paid_through: null, own_trns: [] }],
      '/clients/cafe/accounting': connected,
    })  // prettier-ignore
    renderApp('/clients')

    const row = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    expect(await within(row).findByText('An admin connects the accounting system.')).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: /Sync from Xero/ })).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: 'Disconnect' })).not.toBeInTheDocument()
  })

  it('makes a new account choose its own password before anything else opens (decision 93)', async () => {
    let me = { ...ME, must_change_password: true }
    const api = mockApi({
      '/health/ready': READY,
      '/auth/me': () => me,
      'POST /auth/password': () => {
        me = { ...ME, must_change_password: false }
        return me
      },
      '/clients': [],
    })
    const user = userEvent.setup()
    renderApp('/queue')

    const form = await screen.findByRole('form', { name: 'Change password' })
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument()
    await user.type(within(form).getByLabelText('Password you were given'), 'made-up first pass')
    await user.type(within(form).getByLabelText('New password'), 'short one')
    expect(within(form).getByText('At least 14 characters.')).toBeInTheDocument()
    expect(within(form).getByRole('button', { name: 'Change password' })).toBeDisabled()
    await user.clear(within(form).getByLabelText('New password'))
    await user.type(within(form).getByLabelText('New password'), 'a made-up new password')
    await user.type(within(form).getByLabelText('New password again'), 'a made-up new password')
    await user.click(within(form).getByRole('button', { name: 'Change password' }))

    expect(await screen.findByText('Made-up Accountant')).toBeInTheDocument()
    expect(api.bodies['POST /auth/password']).toEqual({ current_password: 'made-up first pass',
                                                        new_password: 'a made-up new password' })  // prettier-ignore
  })

  it('says why a password was not changed, and stays on the form', async () => {
    mockApi({
      '/health/ready': READY, '/auth/me': { ...ME, must_change_password: true },
      'POST /auth/password': reply(400, { detail: 'The current password is wrong.' }),
    })  // prettier-ignore
    const user = userEvent.setup()
    renderApp('/run')

    const form = await screen.findByRole('form', { name: 'Change password' })
    await user.type(within(form).getByLabelText('Password you were given'), 'not the password')
    await user.type(within(form).getByLabelText('New password'), 'a made-up new password')
    await user.type(within(form).getByLabelText('New password again'), 'a made-up new password')
    await user.click(within(form).getByRole('button', { name: 'Change password' }))

    expect(await within(form).findByText('The current password is wrong.')).toBeInTheDocument()
  })

  it('lets anyone change their password from the user menu', async () => {
    const api = mockApi({
      '/health/ready': READY, '/auth/me': ME, '/clients': [],
      'POST /auth/password': ME,
    })  // prettier-ignore
    const user = userEvent.setup()
    renderApp('/run')

    await user.click(await screen.findByRole('link', { name: 'Change password' }))
    const form = await screen.findByRole('form', { name: 'Change password' })
    await user.type(within(form).getByLabelText('Current password'), 'made-up password 1')
    await user.type(within(form).getByLabelText('New password'), 'a made-up new password')
    await user.type(within(form).getByLabelText('New password again'), 'a made-up new passwrd')
    expect(within(form).getByText('The two new passwords differ.')).toBeInTheDocument()
    await user.clear(within(form).getByLabelText('New password again'))
    await user.type(within(form).getByLabelText('New password again'), 'a made-up new password')
    await user.click(within(form).getByRole('button', { name: 'Change password' }))

    expect(await within(form).findByText(/Password changed/)).toBeInTheDocument()
    expect(api.bodies['POST /auth/password']).toEqual({ current_password: 'made-up password 1',
                                                        new_password: 'a made-up new password' })  // prettier-ignore
  })
})
