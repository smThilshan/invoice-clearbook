import { CircleCheck, Link2, RefreshCw, Send, Unlink } from 'lucide-react'
import { useSearchParams } from 'react-router'

import type { Client, SyncResult } from '@/api/client'
import {
  useAccounting,
  useClients,
  useDisconnectAccounting,
  useSendWaiting,
  useSyncAccounting,
} from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { PageHeader } from '@/components/PageHeader'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { formatDateTime } from '@/lib/labels'
import { TAX_TYPE_LABELS } from '@/pages/document/formModel'
import { usePageTitle } from '@/lib/usePageTitle'

/** Clients and their accounting system (tasks 5.3, 5.4, 5.1): connect a client to Xero, sync
 * its master data, send approved bills waiting to be posted, disconnect. Xero sends the
 * browser back here. */
export function ClientsPage() {
  usePageTitle('Clients')
  const clients = useClients()
  const [params] = useSearchParams()
  const connected = params.get('connected')
  const failed = params.get('error')

  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader
        title="Clients"
        description="Each client's accounting system. Connect Xero to sync its accounts, VAT rates and suppliers, and to post approved bills."
      />
      {connected && (
        <Alert>
          <CircleCheck className="text-success" aria-hidden />
          <AlertTitle>Connected to Xero</AlertTitle>
          <AlertDescription>Organisation: {connected}</AlertDescription>
        </Alert>
      )}
      {failed && (
        <Alert variant="destructive">
          <AlertTitle>Not connected</AlertTitle>
          <AlertDescription>{failed}</AlertDescription>
        </Alert>
      )}
      {clients.isPending ? (
        <Skeleton className="h-32 w-full" />
      ) : clients.isError ? (
        <ErrorAlert title="Clients could not be loaded" error={clients.error} />
      ) : clients.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">No clients yet: add one on the Run screen.</p>
      ) : (
        <ul aria-label="Clients" className="divide-y rounded-lg border">
          {clients.data.map((client) => (
            <ClientRow key={client.client_id} client={client} />
          ))}
        </ul>
      )}
    </div>
  )
}

function ClientRow({ client }: { client: Client }) {
  const status = useAccounting(client.client_id)
  const disconnect = useDisconnectAccounting()
  const sync = useSyncAccounting()
  const send = useSendWaiting()
  const synced = status.data?.connected && status.data.master_data_from === 'XERO'
  const waiting = status.data?.waiting ?? 0
  const connectHref = `/api/xero/connect?${new URLSearchParams({ client_id: client.client_id })}`

  return (
    <li aria-label={client.name} className="flex flex-wrap items-center gap-3 px-4 py-3">
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium">{client.name}</p>
        <p className="text-sm text-muted-foreground">
          {status.isPending
            ? 'Checking…'
            : status.data?.connected
              ? `Xero: ${status.data.organisation}${status.data.connected_at ? ` · since ${formatDateTime(status.data.connected_at)}` : ''}${status.data.master_data_from === 'XERO' ? ' · coding uses Xero accounts' : ' · not synced yet'}`
              : 'No accounting system connected (CSV master data, export file)'}
        </p>
        {waiting > 0 && (
          <p className="text-sm text-muted-foreground">
            {waiting} approved {waiting === 1 ? 'document' : 'documents'} not posted yet
          </p>
        )}
      </div>
      {synced && waiting > 0 && (
        <Button size="sm" disabled={send.isPending} onClick={() => send.mutate(client.client_id)}>
          <Send aria-hidden />
          Send waiting bills
        </Button>
      )}
      {status.data?.connected && (
        <Button
          size="sm"
          variant={synced ? 'outline' : 'default'}
          disabled={sync.isPending}
          onClick={() => sync.mutate(client.client_id)}
        >
          <RefreshCw className={sync.isPending ? 'animate-spin' : undefined} aria-hidden />
          {sync.isPending ? 'Syncing…' : 'Sync from Xero'}
        </Button>
      )}
      {status.data?.connected ? (
        <Button
          variant="outline"
          size="sm"
          disabled={disconnect.isPending}
          onClick={() => {
            if (window.confirm(`Disconnect ${client.name} from Xero?`))
              disconnect.mutate(client.client_id)
          }}
        >
          <Unlink aria-hidden />
          Disconnect
        </Button>
      ) : status.data && !status.data.configured ? (
        <span className="text-xs text-muted-foreground">Xero app keys not set in .env</span>
      ) : (
        <Button asChild size="sm">
          {/* A full page change: the browser goes to Xero's sign-in and comes back here. */}
          <a href={connectHref}>
            <Link2 aria-hidden />
            Connect Xero
          </a>
        </Button>
      )}
      {disconnect.isError && <ErrorAlert title="Not disconnected" error={disconnect.error} />}
      {sync.isError && <ErrorAlert title="Not synced" error={sync.error} />}
      {send.isError && <ErrorAlert title="Not sent" error={send.error} />}
      {send.isSuccess && (
        <p role="status" className="w-full text-sm text-muted-foreground">
          Sending: each bill appears in Xero under Bills to pay, Awaiting approval. A bill Xero
          cannot take goes back to review with the reason.
        </p>
      )}
      {sync.data && <SyncSummary result={sync.data} />}
    </li>
  )
}

function counted(label: string, counts: SyncResult['accounts']) {
  const total = counts.created + counts.updated + counts.unchanged
  return `${total} ${label} (${counts.created} new, ${counts.updated} changed)`
}

/** What the sync brought, and how each Xero VAT rate was matched to ours: an unmatched rate
 * cannot be used for coding, so the accountant should see the list. */
function SyncSummary({ result }: { result: SyncResult }) {
  const purchase = result.tax_mapping.filter((m) => m.tax_type)
  const other = result.tax_mapping.length - purchase.length
  return (
    <div
      role="status"
      aria-label="Sync result"
      className="w-full space-y-2 rounded-md bg-muted/50 p-3 text-sm"
    >
      <p>
        Synced: {counted('accounts', result.accounts)}, {counted('VAT rates', result.tax_rates)},{' '}
        {counted('contacts', result.contacts)}
        {result.deactivated > 0 && `; ${result.deactivated} no longer in Xero, now inactive`}.
      </p>
      <table className="w-full text-left">
        <caption className="sr-only">VAT rates used for bills</caption>
        <thead className="text-xs text-muted-foreground">
          <tr>
            <th className="font-normal">Xero VAT rate</th>
            <th className="font-normal">Used here as</th>
          </tr>
        </thead>
        <tbody>
          {purchase.map((m) => (
            <tr key={m.code}>
              <td>{m.name}</td>
              <td>{TAX_TYPE_LABELS[m.tax_type!]}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {other > 0 && (
        <p className="text-xs text-muted-foreground">
          {other} other rate(s) are for sales or not matched, so they are not offered for bills.
        </p>
      )}
    </div>
  )
}
