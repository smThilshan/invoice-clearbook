import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AccountingStatus, PostingSummary, Readiness, SyncResult } from '@/api/client'
import { mockApi, renderApp, reply } from '@/test/render'

const READY: Readiness = { status: 'ok', database: 'ok', redis: 'ok', model: 'ok' }
const CLIENTS = [
  { client_id: 'cafe-al-noor', name: 'Cafe Al Noor', line_grouping: 'BY_ACCOUNT', expenses_sent_as: 'BILL', paid_through: null, own_trns: [] },
  { client_id: 'spice-route', name: 'Spice Route', line_grouping: 'BY_ACCOUNT', expenses_sent_as: 'BILL', paid_through: null, own_trns: [] },
]
const NOTHING: PostingSummary = {
  ready: 0, retrying: 0, retry_at: null, retry_reason: null, not_yet: {}, sent_back: 0, posted: 0,
  paused: 0, paused_reason: null, failed: 0, failed_reason: null,
}  // prettier-ignore
const XERO = { key: 'XERO' as const, name: 'Xero', configured: true, connect_url: '/xero/connect' }
const NOT_CONNECTED: AccountingStatus = {
  providers: [XERO], configured: true, connected: false, needs_reconnect: false,
  reconnect_reason: null, provider: null, provider_name: null,
  organisation: null, connected_at: null, bills_where: null, accounts: 0, master_data_from: 'MANUAL',
  waiting: 0, posting: NOTHING, expenses_blocked: 'connect an accounting system first',
  expenses_reconnect: false, expenses_where: null,
}  // prettier-ignore
const CONNECTED: AccountingStatus = {
  providers: [XERO], configured: true, connected: true, needs_reconnect: false,
  reconnect_reason: null, provider: 'XERO', provider_name: 'Xero',
  organisation: 'Demo Company (Global)', connected_at: '2026-10-23T10:00:00Z',
  bills_where: 'Bills to pay, Awaiting approval', accounts: 0, master_data_from: 'MANUAL', waiting: 0,
  posting: NOTHING, expenses_blocked: 'Xero cannot take expenses yet: they are sent as bills',
  expenses_reconnect: false, expenses_where: null,
}  // prettier-ignore
const SYNCED_WAITING: AccountingStatus = {
  ...CONNECTED, accounts: 0, master_data_from: 'XERO', waiting: 3, posting: { ...NOTHING, ready: 3 },
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
  it('lets an admin choose how lines are sent', async () => {
    const api = mockApi({
      '/health/ready': READY, '/clients': CLIENTS.slice(0, 1), '/clients/cafe-al-noor/accounting': CONNECTED,
      'PATCH /clients/cafe-al-noor': { ...CLIENTS[0], line_grouping: 'BY_ITEM' },
    })  // prettier-ignore
    const user = userEvent.setup()
    renderApp('/clients')

    const setting = await screen.findByRole('combobox', { name: 'Lines sent to Xero' })
    expect(setting).toHaveTextContent('By account: all products with the same account and VAT in one line')
    await user.click(setting)
    await user.click(await screen.findByRole('option', { name: /^By item/ }))

    await waitFor(() =>
      expect(api.bodies['PATCH /clients/cafe-al-noor']).toEqual({ line_grouping: 'BY_ITEM' }),
    )
  })

  it('lets an admin enter the organization\'s own TRNs', async () => {
    const api = mockApi({
      '/health/ready': READY, '/clients': CLIENTS.slice(0, 1), '/clients/cafe-al-noor/accounting': CONNECTED,
      'PATCH /clients/cafe-al-noor': { ...CLIENTS[0], own_trns: ['100235614300003', '100568308900003'] },
    })  // prettier-ignore
    const user = userEvent.setup()
    renderApp('/clients')

    const row = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    expect(await within(row).findByText('none entered')).toBeInTheDocument()
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    await user.type(within(row).getByLabelText(/Own TRNs/), '100-2356-1430-0003, 100568308900003,')
    await user.click(within(row).getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(api.bodies['PATCH /clients/cafe-al-noor']).toEqual({
        own_trns: ['100-2356-1430-0003', '100568308900003'],
      }),
    )
  })

  it('says why own TRNs were not saved, and keeps what was typed', async () => {
    mockApi({
      '/health/ready': READY, '/clients': CLIENTS.slice(0, 1), '/clients/cafe-al-noor/accounting': CONNECTED,
      'PATCH /clients/cafe-al-noor': reply(422, { detail: 'a TRN is 15 digits: 1002356143 is not' }),
    })  // prettier-ignore
    const user = userEvent.setup()
    renderApp('/clients')

    const row = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    await user.click(await within(row).findByRole('button', { name: 'Edit' }))
    await user.type(within(row).getByLabelText(/Own TRNs/), '1002356143')
    await user.click(within(row).getByRole('button', { name: 'Save' }))

    expect(await within(row).findByText(/15 digits: 1002356143 is not/)).toBeInTheDocument()
    expect(within(row).getByLabelText(/Own TRNs/)).toHaveValue('1002356143')
  })

  it('lets an admin send expenses as expenses, usually paid through a Zoho account', async () => {
    const zoho: AccountingStatus = {
      ...CONNECTED, provider: 'ZOHO', provider_name: 'Zoho Books', expenses_blocked: null,
    }  // prettier-ignore
    const asExpenses = { ...CLIENTS[0], expenses_sent_as: 'EXPENSE' }
    const api = mockApi({
      '/health/ready': READY, '/clients': [asExpenses], '/clients/cafe-al-noor/accounting': zoho,
      '/clients/cafe-al-noor/coding-options': {
        accounts: [], tax_types: [],
        paid_through: [
          { code: '4600361', label: 'Petty Cash', group: 'Cash' },
          { code: '4600370', label: 'Employee Reimbursements', group: 'Other Current Liability' },
        ],
      },
      'PATCH /clients/cafe-al-noor': { ...asExpenses, paid_through: '4600361' },
    })  // prettier-ignore
    const user = userEvent.setup()
    renderApp('/clients')

    const sent = await screen.findByRole('combobox', { name: 'Expenses sent to Zoho Books' })
    expect(sent).toHaveTextContent('As expenses: paid through an account, final in Zoho Books once approved here')
    const paid = screen.getByRole('combobox', { name: 'Usually paid through' })
    expect(paid).toHaveTextContent('Chosen on each expense')
    expect(await screen.findByText('No usual account: Paid through is chosen on each expense before it is approved.')).toBeInTheDocument()
    await user.click(paid)
    expect(await screen.findByRole('group', { name: 'Other Current Liability' })).toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: 'Petty Cash' }))

    await waitFor(() =>
      expect(api.bodies['PATCH /clients/cafe-al-noor']).toEqual({ paid_through: '4600361' }),
    )
  })

  it('asks for a sync when no account to pay through is known yet', async () => {
    const zoho: AccountingStatus = {
      ...CONNECTED, provider: 'ZOHO', provider_name: 'Zoho Books', expenses_blocked: null,
    }  // prettier-ignore
    mockApi({
      '/health/ready': READY, '/clients': [{ ...CLIENTS[0], expenses_sent_as: 'EXPENSE' }],
      '/clients/cafe-al-noor/accounting': zoho,
      '/clients/cafe-al-noor/coding-options': { accounts: [], tax_types: [], paid_through: [] },
    })  // prettier-ignore
    renderApp('/clients')

    expect(await screen.findByText('No accounts to pay through yet: sync from Zoho Books first.')).toBeInTheDocument()
  })

  it('offers to connect again when only the permission for expenses is missing', async () => {
    const zoho: AccountingStatus = {
      ...CONNECTED, provider: 'ZOHO', provider_name: 'Zoho Books',
      providers: [{ key: 'ZOHO', name: 'Zoho Books', configured: true, connect_url: '/zoho/connect' }],
      expenses_blocked: 'connect Zoho Books again to allow sending expenses', expenses_reconnect: true,
    }  // prettier-ignore
    mockApi({
      '/health/ready': READY, '/clients': CLIENTS.slice(0, 1), '/clients/cafe-al-noor/accounting': zoho,
    })  // prettier-ignore
    renderApp('/clients')

    const row = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    expect(await within(row).findByText('Expenses go as bills: connect Zoho Books again to allow sending expenses.')).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'Connect Zoho Books again' })).toHaveAttribute(
      'href', '/api/zoho/connect?client_id=cafe-al-noor',
    )  // prettier-ignore
  })

  it('counts documents, and says where bills and expenses went, when expenses go as expenses', async () => {
    const zoho: AccountingStatus = {
      ...CONNECTED, provider: 'ZOHO', provider_name: 'Zoho Books', master_data_from: 'ZOHO',
      bills_where: 'Purchases, Bills, Pending Approval', expenses_where: 'Purchases, Expenses',
      expenses_blocked: null, waiting: 2, posting: { ...NOTHING, ready: 2 },
    }  // prettier-ignore
    let status = zoho
    mockApi({
      '/health/ready': READY, '/clients': [{ ...CLIENTS[0], expenses_sent_as: 'EXPENSE', paid_through: '4600361' }],
      '/clients/cafe-al-noor/accounting': () => status,
      '/clients/cafe-al-noor/coding-options': { accounts: [], tax_types: [], paid_through: [] },
      'POST /clients/cafe-al-noor/accounting/send': () => {
        status = { ...zoho, waiting: 0, posting: { ...NOTHING, posted: 2 } }
        return zoho
      },
    })  // prettier-ignore
    const user = userEvent.setup()
    renderApp('/clients')

    const row = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    expect(await within(row).findByText('2 approved documents ready to send to Zoho Books.')).toBeInTheDocument()
    await user.click(within(row).getByRole('button', { name: 'Send 2 documents to Zoho Books' }))

    const done = await within(row).findByText(/^Done\./, {}, { timeout: 6000 })
    expect(done.parentElement).toHaveTextContent(
      'Done. 2 documents sent to Zoho Books: find them under Purchases, Bills, Pending Approval (bills) and Purchases, Expenses (expenses).',
    )
    expect(within(row).getByText('2 documents sent to Zoho Books so far.')).toBeInTheDocument()
  })

  it('says why expenses still go as bills', async () => {
    mockApi({
      '/health/ready': READY, '/clients': CLIENTS.slice(0, 1), '/clients/cafe-al-noor/accounting': CONNECTED,
    })  // prettier-ignore
    renderApp('/clients')

    const sent = await screen.findByRole('combobox', { name: 'Expenses sent to Xero' })
    expect(sent).toBeDisabled()
    expect(screen.getByText('Expenses go as bills: Xero cannot take expenses yet: they are sent as bills.')).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Usually paid through' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Connect Xero again' })).not.toBeInTheDocument()  // would not help
  })

  it('shows an accountant how lines are sent, without letting them change it', async () => {
    mockApi({
      '/health/ready': READY, '/clients': CLIENTS.slice(0, 1), '/clients/cafe-al-noor/accounting': CONNECTED,
      '/auth/me': { email: 'a@missan.test', name: 'Made-up Accountant', role: 'ACCOUNTANT' },
    })  // prettier-ignore
    renderApp('/clients')

    const row = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    expect(await within(row).findByText('By account: all products with the same account and VAT in one line')).toBeInTheDocument()
    expect(within(row).queryByRole('combobox')).not.toBeInTheDocument()
    expect(within(row).getByText('none entered')).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument()
  })

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
    expect(within(row).getByText(/1 bill not sent to Xero, back in review/)).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'Show them' })).toHaveAttribute(
      'href', '/queue?client=cafe-al-noor&sending=not_sent',
    )  // prettier-ignore
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
      bills_where: 'Purchases, Bills', accounts: 0, master_data_from: 'XERO', expenses_blocked: null,
    }  // prettier-ignore
    mockApi({ '/health/ready': READY, '/clients': CLIENTS.slice(0, 1),
              '/clients/cafe-al-noor/accounting': zoho })  // prettier-ignore
    renderApp('/clients')

    const row = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    expect(await within(row).findByRole('button', { name: 'Send 3 bills to Zoho Books' })).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Sync from Zoho Books' })).toBeInTheDocument()
    expect(row).not.toHaveTextContent('Xero')
  })

  it('asks to sign in again when the access was withdrawn, and offers no Send', async () => {
    const withdrawn: AccountingStatus = {
      ...SYNCED_WAITING, needs_reconnect: true,
      reconnect_reason: 'Xero access was withdrawn (invalid_grant)',
      posting: { ...NOTHING, paused: 3, paused_reason: 'Xero access was withdrawn (invalid_grant)' },
    }  // prettier-ignore
    mockApi({ '/health/ready': READY, '/clients': CLIENTS.slice(0, 1),
              '/clients/cafe-al-noor/accounting': withdrawn })  // prettier-ignore
    renderApp('/clients')

    const row = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    const banner = await within(row).findByRole('alert', { name: 'Sign in again' })
    expect(banner).toHaveTextContent('Xero needs you to sign in again')
    expect(banner).toHaveTextContent('3 bills paused: they are sent automatically as soon as you connect again.')
    expect(banner).toHaveTextContent('Reason: Xero access was withdrawn (invalid_grant).')
    expect(within(banner).getByRole('link', { name: 'Connect Xero again' })).toHaveAttribute(
      'href', '/api/xero/connect?client_id=cafe-al-noor',
    )  // prettier-ignore
    expect(within(row).getByText('Xero: sign in again')).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /Send/ })).not.toBeInTheDocument()
  })

  it('says how many bills could not be sent, with the latest reason', async () => {
    const failed = { ...SYNCED_WAITING, waiting: 0,
                     posting: { ...NOTHING, failed: 2, failed_reason: 'unexpected error (RuntimeError)' } }  // prettier-ignore
    mockApi({ '/health/ready': READY, '/clients': CLIENTS.slice(0, 1),
              '/clients/cafe-al-noor/accounting': failed })  // prettier-ignore
    renderApp('/clients')

    const row = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    expect(
      await within(row).findByText(/2 bills could not be sent: unexpected error \(RuntimeError\)\./),
    ).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'Open the list' })).toHaveAttribute(
      'href', '/queue?client=cafe-al-noor&sending=failed',
    )  // prettier-ignore
  })

  it('says bills are paused when the client was disconnected', async () => {
    const disconnected: AccountingStatus = {
      ...NOT_CONNECTED, accounts: 0, master_data_from: 'XERO', waiting: 2,
      posting: { ...NOTHING, paused: 2, paused_reason: 'not connected: connect the accounting system again' },
    }  // prettier-ignore
    mockApi({ '/health/ready': READY, '/clients': CLIENTS.slice(0, 1),
              '/clients/cafe-al-noor/accounting': disconnected })  // prettier-ignore
    renderApp('/clients')

    const row = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    expect(
      await within(row).findByText(/2 approved bills paused: not connected: connect the accounting system again/),
    ).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'Connect Xero' })).toBeInTheDocument()
  })

  it('says how a Xero sign-in ended when Xero sends the browser back', async () => {
    backend()
    renderApp('/clients?error=Xero+sign-in+was+not+completed+(access_denied)')

    expect(await screen.findByText('Not connected')).toBeInTheDocument()
    expect(screen.getByText('Xero sign-in was not completed (access_denied)')).toBeInTheDocument()
  })
})

describe('companies: remove and restore (decision 91)', () => {
  it('removes a company after asking, saying nothing is deleted', async () => {
    let removed = false
    const api = mockApi({
      '/health/ready': READY,
      '/clients': () => (removed ? CLIENTS.slice(1) : CLIENTS),
      '/clients/removed': () => (removed ? CLIENTS.slice(0, 1) : []),
      '/clients/cafe-al-noor/accounting': CONNECTED,
      '/clients/spice-route/accounting': NOT_CONNECTED,
      'POST /clients/cafe-al-noor/remove': () => {
        removed = true
        return CLIENTS[0]
      },
    })
    const user = userEvent.setup()
    renderApp('/clients')

    const row = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    await user.click(within(row).getByRole('button', { name: 'Remove…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Remove Cafe Al Noor?' })
    expect(dialog).toHaveTextContent('Nothing is deleted')
    expect(api.calls).not.toContain('POST /clients/cafe-al-noor/remove')
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const companies = screen.getByRole('list', { name: 'Organizations and businesses' })
    await waitFor(() => expect(within(companies).queryByRole('listitem', { name: 'Cafe Al Noor' })).not.toBeInTheDocument())
    const hidden = await screen.findByRole('region', { name: 'Removed organizations and businesses' })
    expect(within(hidden).getByRole('listitem', { name: 'Cafe Al Noor' })).toBeInTheDocument()
  })

  it('cancelling removes nothing', async () => {
    const api = backend()
    const user = userEvent.setup()
    renderApp('/clients')

    const row = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    await user.click(within(row).getByRole('button', { name: 'Remove…' }))
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.calls.filter((c) => c.endsWith('/remove'))).toEqual([])
  })

  it('says why a busy company cannot be removed, and keeps it', async () => {
    mockApi({
      '/health/ready': READY, '/clients': CLIENTS.slice(0, 1), '/clients/cafe-al-noor/accounting': CONNECTED,
      'POST /clients/cafe-al-noor/remove': reply(409, {
        detail: 'Cafe Al Noor cannot be removed now: 2 approved documents are still waiting to be sent',
      }),
    })  // prettier-ignore
    const user = userEvent.setup()
    renderApp('/clients')

    const row = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    await user.click(within(row).getByRole('button', { name: 'Remove…' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }))

    expect(await within(dialog).findByText(/2 approved documents are still waiting to be sent/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(await screen.findByRole('listitem', { name: 'Cafe Al Noor' })).toBeInTheDocument()
  })

  it('restores a removed company', async () => {
    const api = mockApi({
      '/health/ready': READY, '/clients': CLIENTS.slice(1), '/clients/removed': CLIENTS.slice(0, 1),
      '/clients/spice-route/accounting': NOT_CONNECTED, 'POST /clients/cafe-al-noor/restore': CLIENTS[0],
    })  // prettier-ignore
    const user = userEvent.setup()
    renderApp('/clients')

    const hidden = await screen.findByRole('region', { name: 'Removed organizations and businesses' })
    await user.click(within(hidden).getByRole('button', { name: 'Restore' }))

    await waitFor(() => expect(api.calls).toContain('POST /clients/cafe-al-noor/restore'))
  })

  it('offers an accountant neither Remove nor the removed list', async () => {
    const api = mockApi({
      '/health/ready': READY, '/clients': CLIENTS.slice(0, 1), '/clients/cafe-al-noor/accounting': CONNECTED,
      '/clients/removed': CLIENTS.slice(1),
      '/auth/me': { email: 'a@missan.test', name: 'Made-up Accountant', role: 'ACCOUNTANT' },
    })  // prettier-ignore
    renderApp('/clients')

    const row = await screen.findByRole('listitem', { name: 'Cafe Al Noor' })
    await within(row).findByText(/An admin connects/)
    expect(within(row).queryByRole('button', { name: 'Remove…' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Removed organizations and businesses' })).not.toBeInTheDocument()
    expect(api.calls).not.toContain('GET /clients/removed')
  })
})

describe('companies: delete permanently (decision 91)', () => {
  const PREVIEW = { documents: 69, posted: 4, connected_to: 'Zoho Books', blocked: null }

  function removedBackend(overrides: Record<string, unknown> = {}) {
    let deleted = false
    return mockApi({
      '/health/ready': READY,
      '/clients': CLIENTS.slice(1),
      '/clients/spice-route/accounting': NOT_CONNECTED,
      '/clients/removed': () => (deleted ? [] : CLIENTS.slice(0, 1)),
      '/clients/cafe-al-noor/deletion': PREVIEW,
      'POST /clients/cafe-al-noor/delete': () => {
        deleted = true
        return { documents: 69, files_removed: true }
      },
      ...overrides,
    })
  }

  async function openDialog(user: ReturnType<typeof userEvent.setup>) {
    const hidden = await screen.findByRole('region', { name: 'Removed organizations and businesses' })
    await user.click(within(hidden).getByRole('button', { name: 'Delete permanently…' }))
    return screen.findByRole('dialog', { name: 'Delete Cafe Al Noor permanently?' })
  }

  it('says what goes and what stays, and deletes only once the name is typed', async () => {
    const api = removedBackend()
    const user = userEvent.setup()
    renderApp('/clients')

    const dialog = await openDialog(user)
    expect(await within(dialog).findByText(/69 documents with their files/)).toBeInTheDocument()
    expect(dialog).toHaveTextContent('4 documents were posted to Zoho Books: they stay there.')
    expect(dialog).toHaveTextContent('Its sign-in to Zoho Books is forgotten here. Nothing in Zoho Books is changed.')
    expect(dialog).toHaveTextContent('This cannot be undone.')
    const confirm = within(dialog).getByRole('button', { name: 'Delete permanently' })
    const name = within(dialog).getByLabelText('Type Cafe Al Noor to confirm')

    await user.type(name, 'Cafe Al')
    expect(confirm).toBeDisabled()
    await user.type(name, ' Noor')
    expect(confirm).toBeEnabled()
    await user.click(confirm)

    await waitFor(() => expect(api.bodies['POST /clients/cafe-al-noor/delete']).toEqual({ name: 'Cafe Al Noor' }))
    expect(await screen.findByText(/Cafe Al Noor was deleted permanently, with 69 documents\./)).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('cannot be confirmed while the company still has work in flight', async () => {
    removedBackend({
      '/clients/cafe-al-noor/deletion': { ...PREVIEW, blocked: '1 approved document is still waiting to be sent' },
    })
    const user = userEvent.setup()
    renderApp('/clients')

    const dialog = await openDialog(user)
    expect(await within(dialog).findByText('1 approved document is still waiting to be sent')).toBeInTheDocument()
    await user.type(within(dialog).getByLabelText('Type Cafe Al Noor to confirm'), 'Cafe Al Noor')
    expect(within(dialog).getByRole('button', { name: 'Delete permanently' })).toBeDisabled()
  })

  it('shows the reason when the server refuses, and deletes nothing', async () => {
    removedBackend({
      'POST /clients/cafe-al-noor/delete': reply(409, { detail: "the name typed does not match its name" }),
    })
    const user = userEvent.setup()
    renderApp('/clients')

    const dialog = await openDialog(user)
    await within(dialog).findByText(/69 documents/)
    await user.type(within(dialog).getByLabelText('Type Cafe Al Noor to confirm'), 'Cafe Al Noor')
    await user.click(within(dialog).getByRole('button', { name: 'Delete permanently' }))

    expect(await within(dialog).findByText(/does not match/)).toBeInTheDocument()
  })

  it('says when stored files could not all be removed', async () => {
    removedBackend({ 'POST /clients/cafe-al-noor/delete': { documents: 2, files_removed: false } })
    const user = userEvent.setup()
    renderApp('/clients')

    const dialog = await openDialog(user)
    await within(dialog).findByText(/69 documents/)
    await user.type(within(dialog).getByLabelText('Type Cafe Al Noor to confirm'), 'Cafe Al Noor')
    await user.click(within(dialog).getByRole('button', { name: 'Delete permanently' }))

    expect(await screen.findByText(/Some of its stored files could not be removed/)).toBeInTheDocument()
  })
})

describe('clients: SharePoint (decision 83)', () => {
  const NOT_YET = {
    configured: true, connected: false, needs_reconnect: false, reconnect_reason: null,
    account_name: null, account_email: null, connected_by: null, connected_at: null,
  }  // prettier-ignore
  const CONNECTED_AS = {
    ...NOT_YET, connected: true, account_name: 'Made-up Admin', account_email: 'admin@missan.test',
    connected_by: 'admin@missan.test', connected_at: '2026-11-05T08:00:00Z',
  }  // prettier-ignore

  function withSharePoint(status: unknown, extra: Record<string, unknown> = {}) {
    return mockApi({
      '/health/ready': READY,
      '/clients': CLIENTS,
      '/clients/cafe-al-noor/accounting': NOT_CONNECTED,
      '/clients/spice-route/accounting': NOT_CONNECTED,
      '/sharepoint/status': status,
      ...extra,
    })
  }

  it('lets an admin connect SharePoint once, for every client', async () => {
    withSharePoint(NOT_YET)
    renderApp('/clients')

    const card = await screen.findByRole('region', { name: 'SharePoint' })
    expect(await within(card).findByRole('link', { name: 'Connect SharePoint' })).toHaveAttribute(
      'href', '/api/sharepoint/connect?back=clients',
    )  // prettier-ignore
    expect(card).toHaveTextContent('never changes anything')
  })

  it('shows whose read-only access is used, and disconnects it', async () => {
    let status: unknown = CONNECTED_AS
    const api = withSharePoint(() => status, {
      'POST /sharepoint/disconnect': () => {
        status = NOT_YET
        return NOT_YET
      },
    })
    const user = userEvent.setup()
    renderApp('/clients')

    const card = await screen.findByRole('region', { name: 'SharePoint' })
    expect(await within(card).findByText(/Connected as Made-up Admin \(admin@missan.test\)/)).toBeInTheDocument()
    await user.click(within(card).getByRole('button', { name: 'Disconnect' }))

    expect(await within(card).findByRole('link', { name: 'Connect SharePoint' })).toBeInTheDocument()
    expect(api.calls).toContain('POST /sharepoint/disconnect')
  })

  it('says it is connected after the sign-in, or why not', async () => {
    withSharePoint(CONNECTED_AS)
    renderApp('/clients?sharepoint_error=SharePoint+sign-in+was+not+completed+(access_denied)')

    expect(await screen.findByText('SharePoint not connected')).toBeInTheDocument()
    expect(screen.getByText('SharePoint sign-in was not completed (access_denied)')).toBeInTheDocument()
  })
})
