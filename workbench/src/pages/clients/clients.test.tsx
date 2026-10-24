import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AccountingStatus, PostingSummary, Readiness, SyncResult } from '@/api/client'
import { mockApi, renderApp } from '@/test/render'

const READY: Readiness = { status: 'ok', database: 'ok', redis: 'ok', model: 'ok' }
const CLIENTS = [
  { client_id: 'cafe-al-noor', name: 'Cafe Al Noor' },
  { client_id: 'spice-route', name: 'Spice Route' },
]
const NOTHING: PostingSummary = {
  ready: 0, retrying: 0, retry_at: null, retry_reason: null, not_yet: {}, sent_back: 0, posted: 0,
}  // prettier-ignore
const XERO = { key: 'XERO' as const, name: 'Xero', configured: true, connect_url: '/xero/connect' }
const NOT_CONNECTED: AccountingStatus = {
  providers: [XERO], configured: true, connected: false, provider: null, provider_name: null,
  organisation: null, connected_at: null, bills_where: null, master_data_from: 'MANUAL',
  waiting: 0, posting: NOTHING,
}  // prettier-ignore
const CONNECTED: AccountingStatus = {
  providers: [XERO], configured: true, connected: true, provider: 'XERO', provider_name: 'Xero',
  organisation: 'Demo Company (Global)', connected_at: '2026-10-23T10:00:00Z',
  bills_where: 'Bills to pay, Awaiting approval', master_data_from: 'MANUAL', waiting: 0,
  posting: NOTHING,
}  // prettier-ignore
const SYNCED_WAITING: AccountingStatus = {
  ...CONNECTED, master_data_from: 'XERO', waiting: 3, posting: { ...NOTHING, ready: 3 },
}  // prettier-ignore
const SENT: AccountingStatus = {
  ...SYNCED_WAITING, waiting: 0, posting: { ...NOTHING, posted: 2, sent_back: 1 },
}  // prettier-ignore
const SYNCED: SyncResult = {
  accounts: { created: 2, updated: 0, unchanged: 0 },
  tax_rates: { created: 2, updated: 0, unchanged: 0 },
  contacts: { created: 1, updated: 0, unchanged: 0 },
  deactivated: 0,
  tax_mapping: [
    { code: 'INPUT', name: 'Standard Rated Expenses', tax_type: 'STANDARD_5' },
    { code: 'OUTPUT', name: 'Standard Rated Income', tax_type: null },
  ],
}

const realFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = realFetch
  vi.restoreAllMocks()
})

function backend() {
  return mockApi({
    '/health/ready': READY,
    '/clients': CLIENTS,
    '/clients/cafe-al-noor/accounting': CONNECTED,
    '/clients/spice-route/accounting': NOT_CONNECTED,
    'POST /clients/cafe-al-noor/accounting/disconnect': NOT_CONNECTED,
    'POST /clients/cafe-al-noor/accounting/sync': SYNCED,
  })
}

describe('clients', () => {
  it('shows each client\'s accounting connection, and connects through Xero', async () => {
    backend()
    renderApp('/clients')

    const connected = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    await waitFor(() => expect(connected).toHaveTextContent('Xero: Demo Company (Global)'))
    const other = screen.getByRole('listitem', { name: 'Spice Route' })
    await waitFor(() =>
      expect(within(other).getByRole('link', { name: 'Connect Xero' })).toHaveAttribute(
        'href', '/api/xero/connect?client_id=spice-route',
      ),
    )  // prettier-ignore
  })

  it('disconnects after asking', async () => {
    const api = backend()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = userEvent.setup()
    renderApp('/clients')

    const connected = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    await user.click(await within(connected).findByRole('button', { name: 'Disconnect' }))

    await waitFor(() => expect(api.calls).toContain('POST /clients/cafe-al-noor/accounting/disconnect'))
    expect(await within(connected).findByRole('link', { name: 'Connect Xero' })).toBeInTheDocument()
  })

  it('syncs from Xero and shows how its VAT rates are used for bills', async () => {
    const api = backend()
    const user = userEvent.setup()
    renderApp('/clients')

    const connected = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    await user.click(await within(connected).findByRole('button', { name: 'Sync from Xero' }))

    const result = await within(connected).findByRole('status', { name: 'Sync result' })
    expect(api.calls).toContain('POST /clients/cafe-al-noor/accounting/sync')
    expect(result).toHaveTextContent('2 accounts (2 new, 0 changed)')
    expect(within(result).getByRole('row', { name: 'Standard Rated Expenses Standard 5%' })).toBeInTheDocument()
    expect(result).toHaveTextContent('1 other rate(s) are for sales or not matched')
  })

  it('sends the bills ready to go, then says what happened to them', async () => {
    let sent = false
    const api = mockApi({
      '/health/ready': READY,
      '/clients': CLIENTS.slice(0, 1),
      '/clients/cafe-al-noor/accounting': () => (sent ? SENT : SYNCED_WAITING),
      'POST /clients/cafe-al-noor/accounting/send': () => {
        sent = true
        return SYNCED_WAITING
      },
    })
    const user = userEvent.setup()
    renderApp('/clients')

    const row = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    expect(await within(row).findByText('3 approved bills ready to send to Xero.')).toBeInTheDocument()
    await user.click(within(row).getByRole('button', { name: 'Send 3 bills to Xero' }))

    await waitFor(() => expect(api.calls).toContain('POST /clients/cafe-al-noor/accounting/send'))
    const done = await within(row).findByText(/^Done\./, {}, { timeout: 6000 })
    expect(done.parentElement).toHaveTextContent(
      'Done. 2 bills sent to Xero: find them under Bills to pay, Awaiting approval. 1 bill came back to review with the reason.',
    )
    expect(within(row).getByText(/1 bill sent back to review/)).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /Send .* to Xero/ })).not.toBeInTheDocument()
  })

  it('says plainly when approved documents cannot be sent yet, with no button', async () => {
    const receipts = { ...SYNCED_WAITING, waiting: 2, posting: { ...NOTHING, not_yet: { RECEIPT: 2 } } }
    mockApi({ '/health/ready': READY, '/clients': CLIENTS.slice(0, 1),
              '/clients/cafe-al-noor/accounting': receipts })  // prettier-ignore
    renderApp('/clients')

    const row = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    expect(
      await within(row).findByText(/2 approved receipts: receipts are not sent to Xero in this version yet/),
    ).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /Send/ })).not.toBeInTheDocument()
  })

  it('names whichever accounting system the client is connected to', async () => {
    const zoho: AccountingStatus = {
      ...SYNCED_WAITING, provider_name: 'Zoho Books', organisation: 'Made-up Trading',
      bills_where: 'Purchases, Bills', master_data_from: 'XERO',
    }  // prettier-ignore
    mockApi({ '/health/ready': READY, '/clients': CLIENTS.slice(0, 1),
              '/clients/cafe-al-noor/accounting': zoho })  // prettier-ignore
    renderApp('/clients')

    const row = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    expect(await within(row).findByRole('button', { name: 'Send 3 bills to Zoho Books' })).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Sync from Zoho Books' })).toBeInTheDocument()
    expect(row).not.toHaveTextContent('Xero')
  })

  it('says how a Xero sign-in ended when Xero sends the browser back', async () => {
    backend()
    renderApp('/clients?error=Xero+sign-in+was+not+completed+(access_denied)')

    expect(await screen.findByText('Not connected')).toBeInTheDocument()
    expect(screen.getByText('Xero sign-in was not completed (access_denied)')).toBeInTheDocument()
  })
})
