import { CircleCheck, Clock, Link2, RefreshCw, Send, Undo2, Unlink } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import type { Client, PostingSummary, SyncResult } from '@/api/client'
import {
  useAccounting,
  useClients,
  useDisconnectAccounting,
  useSendWaiting,
  useSyncAccounting,
} from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { PageContainer, PageHeader } from '@/components/PageHeader'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { formatDateTime } from '@/lib/labels'
import { TAX_TYPE_LABELS } from '@/pages/document/formModel'
import { usePageTitle } from '@/lib/usePageTitle'

/** Clients and their accounting system (tasks 5.3, 5.4, 5.1): connect a client, sync its
 * master data, send approved bills waiting to be posted, disconnect. The accounting system's
 * name always comes from the API (decision 43): Xero today, others later. Its sign-in sends the
 * browser back here. */
export function ClientsPage() {
  usePageTitle('Clients')
  const clients = useClients()
  const [params] = useSearchParams()
  const connected = params.get('connected')
  const system = params.get('system') ?? 'the accounting system'
  const failed = params.get('error')

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        title="Clients"
        description="Each client's accounting system. Connect it to sync its accounts, VAT rates and suppliers, and to post approved bills."
      />
      {connected && (
        <Alert>
          <CircleCheck className="text-success" aria-hidden />
          <AlertTitle>Connected to {system}</AlertTitle>
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
        <ul aria-label="Clients" className="space-y-3">
          {clients.data.map((client) => (
            <ClientRow key={client.client_id} client={client} />
          ))}
        </ul>
      )}
    </PageContainer>
  )
}

/** How often the card checks back while bills are being sent, and when it stops waiting. */
const SENDING_REFRESH_MS = 2_000
const SENDING_PATIENCE_MS = 45_000

function ClientRow({ client }: { client: Client }) {
  // Sending (task 5.1): what the card knew when Send was clicked, to say afterwards what changed.
  const [sending, setSending] = useState<{ before: PostingSummary; since: number } | null>(null)
  const status = useAccounting(client.client_id, sending ? SENDING_REFRESH_MS : false)
  const disconnect = useDisconnectAccounting()
  const sync = useSyncAccounting()
  const send = useSendWaiting()
  const synced = Boolean(status.data?.connected && status.data.master_data_from === status.data.provider)
  const summary = status.data?.posting
  const connected = status.data?.connected === true
  // The connected system's name; before connecting, the systems that could be connected.
  const system = status.data?.provider_name ?? 'the accounting system'
  const available = (status.data?.providers ?? []).filter((p) => p.configured)
  const connectionLabel = status.isPending
    ? 'Checking connection'
    : status.isError
      ? 'Status unavailable'
      : connected
        ? `${system} ${synced ? 'synced' : 'connected'}`
        : status.data && !status.data.configured
          ? 'No accounting system set up'
          : 'Local export'
  const query = new URLSearchParams({ client_id: client.client_id })

  return (
    <li aria-label={client.name} className="rounded-xl border bg-background p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate font-semibold">{client.name}</p>
            <Badge variant={connected ? 'secondary' : 'outline'}>{connectionLabel}</Badge>
          </div>
          <p className="text-sm text-muted-foreground">
            {status.isPending
              ? 'Checking…'
              : status.isError
                ? 'We could not confirm this client’s accounting connection.'
                : status.data?.connected
                  ? `${system}: ${status.data.organisation}${status.data.connected_at ? ` · since ${formatDateTime(status.data.connected_at)}` : ''}${synced ? ` · coding uses ${system} accounts` : ' · not synced yet'}`
                  : 'No accounting system connected (CSV master data, export file)'}
          </p>
          {summary && (
            <PostingLines summary={summary} synced={synced} system={system} clientId={client.client_id} />
          )}
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-2 border-t pt-3">
        {synced && summary && summary.ready > 0 && !sending && (
          <Button
            size="sm"
            disabled={send.isPending}
            onClick={() =>
              send.mutate(client.client_id, {
                onSuccess: () => setSending({ before: summary, since: Date.now() }),
              })
            }
          >
            <Send aria-hidden />
            Send {summary.ready} {summary.ready === 1 ? 'bill' : 'bills'} to {system}
          </Button>
        )}
        {connected && (
          <Button
            size="sm"
            variant={synced ? 'outline' : 'default'}
            disabled={sync.isPending}
            onClick={() => sync.mutate(client.client_id)}
          >
            <RefreshCw className={sync.isPending ? 'animate-spin' : undefined} aria-hidden />
            {sync.isPending ? 'Syncing…' : `Sync from ${system}`}
          </Button>
        )}
        {status.isPending ? (
          <span className="self-center text-xs text-muted-foreground">Checking connection…</span>
        ) : status.isError ? (
          <span className="self-center text-xs text-muted-foreground">Connection actions are unavailable.</span>
        ) : connected ? (
          <Button
            variant="outline"
            size="sm"
            disabled={disconnect.isPending}
            onClick={() => {
              if (window.confirm(`Disconnect ${client.name} from ${system}?`))
                disconnect.mutate(client.client_id)
            }}
          >
            <Unlink aria-hidden />
            Disconnect
          </Button>
        ) : available.length === 0 ? (
          <span className="self-center text-xs text-muted-foreground">
            No accounting system is set up: its app keys go in .env
          </span>
        ) : (
          available.map((provider) => (
            <Button key={provider.key} asChild size="sm">
              {/* A full page change: the browser goes to its sign-in and comes back here. */}
              <a href={`/api${provider.connect_url}?${query}`}>
                <Link2 aria-hidden />
                Connect {provider.name}
              </a>
            </Button>
          ))
        )}
      </div>
      {status.isError && <ErrorAlert title="Connection status could not be loaded" error={status.error} />}
      {disconnect.isError && <ErrorAlert title="Not disconnected" error={disconnect.error} />}
      {sync.isError && <ErrorAlert title="Not synced" error={sync.error} />}
      {send.isError && <ErrorAlert title="Not sent" error={send.error} />}
      {sending && summary && (
        <SendingProgress
          before={sending.before}
          now={summary}
          since={sending.since}
          system={system}
          where={status.data?.bills_where ?? null}
          onFinished={() => setSending(null)}
        />
      )}
      {sync.data && <SyncSummary result={sync.data} />}
    </li>
  )
}

function plural(count: number, one: string, many = `${one}s`) {
  return `${count} ${count === 1 ? one : many}`
}

const NOT_YET_LABELS: Record<string, [string, string]> = {
  RECEIPT: ['receipt', 'receipts'],
  CREDIT_NOTE: ['credit note', 'credit notes'],
}

/** Every approved document, in plain words: ready, retrying, sent back, not possible yet, done. */
function PostingLines({
  summary,
  synced,
  system,
  clientId,
}: {
  summary: PostingSummary
  synced: boolean
  system: string
  clientId: string
}) {
  const queue = `/queue?${new URLSearchParams({ client: clientId })}`
  return (
    <ul aria-label="Approved documents" className="space-y-0.5 text-sm">
      {summary.ready > 0 && (
        <li className="font-medium text-warning">
          {synced
            ? `${plural(summary.ready, 'approved bill')} ready to send to ${system}.`
            : `${plural(summary.ready, 'approved bill')} waiting: connect and sync the accounting system to send them.`}
        </li>
      )}
      {summary.retrying > 0 && (
        <li className="flex items-start gap-1.5 text-warning">
          <Clock className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>
            {plural(summary.retrying, 'bill')} will be sent again automatically
            {summary.retry_at ? ` at ${formatTime(summary.retry_at)}` : ''}
            {summary.retry_reason ? `: ${summary.retry_reason}` : ''}.
          </span>
        </li>
      )}
      {summary.sent_back > 0 && (
        <li className="flex items-start gap-1.5 text-destructive">
          <Undo2 className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>
            {plural(summary.sent_back, 'bill')} sent back to review, not sent to {system}.{' '}
            <Link to={queue} className="underline underline-offset-2">
              Open the Review queue
            </Link>{' '}
            to see why.
          </span>
        </li>
      )}
      {Object.entries(summary.not_yet).map(([type, count]) => {
        const [one, many] = NOT_YET_LABELS[type] ?? ['document', 'documents']
        return (
          <li key={type} className="text-muted-foreground">
            {plural(count, `approved ${one}`, `approved ${many}`)}: {many} are not sent to {system}{' '}
            in this version yet. Nothing to do now.
          </li>
        )
      })}
      {summary.posted > 0 && (
        <li className="flex items-center gap-1.5 text-muted-foreground">
          <CircleCheck className="size-3.5 text-success" aria-hidden />
          {plural(summary.posted, 'bill')} sent to {system} so far.
        </li>
      )}
    </ul>
  )
}

/** After Send: live progress, then what happened, in one sentence. */
function SendingProgress({
  before,
  now,
  since,
  system,
  where,
  onFinished,
}: {
  before: PostingSummary
  now: PostingSummary
  since: number
  system: string
  where: string | null
  onFinished: () => void
}) {
  const [, tick] = useState(0)
  useEffect(() => {
    const timer = window.setInterval(() => tick((n) => n + 1), SENDING_REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [])
  const sent = now.posted - before.posted
  const back = now.sent_back - before.sent_back
  const moved = sent > 0 || back > 0 || now.retrying > before.retrying
  const finished = now.ready === 0
  const gaveUp = !finished && Date.now() - since > SENDING_PATIENCE_MS

  if (!finished && !gaveUp) {
    return (
      <p role="status" className="mt-3 flex items-center gap-2 text-sm">
        <RefreshCw className="size-3.5 animate-spin" aria-hidden />
        Sending {plural(now.ready, 'bill')} to {system}…{sent > 0 ? ` ${sent} sent so far.` : ''}
      </p>
    )
  }
  const parts = [
    sent > 0 &&
      `${plural(sent, 'bill')} sent to ${system}${where ? `: find them under ${where}` : ''}.`,
    back > 0 && `${plural(back, 'bill')} came back to review with the reason.`,
    now.retrying > 0 && `${plural(now.retrying, 'bill')} will be sent again automatically.`,
  ].filter(Boolean)
  return (
    <div role="status" className="mt-3 flex flex-wrap items-center gap-2 text-sm">
      {gaveUp && !moved ? (
        <span className="text-destructive">
          Not sent yet: the sending service does not seem to be running. Ask the person who runs
          the system to start it, then click Send again.
        </span>
      ) : (
        <span>
          <span className="font-medium">{gaveUp ? 'Still sending. ' : 'Done. '}</span>
          {parts.join(' ')}
        </span>
      )}
      <Button variant="ghost" size="sm" onClick={onFinished}>
        OK
      </Button>
    </div>
  )
}

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function counted(label: string, counts: SyncResult['accounts']) {
  const total = counts.created + counts.updated + counts.unchanged
  return `${total} ${label} (${counts.created} new, ${counts.updated} changed)`
}

/** What the sync brought, and how each of its VAT rates was matched to ours: an unmatched rate
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
        {result.deactivated > 0 && `; ${result.deactivated} no longer there, now inactive`}.
      </p>
      <table className="w-full text-left">
        <caption className="sr-only">VAT rates used for bills</caption>
        <thead className="text-xs text-muted-foreground">
          <tr>
            <th className="font-normal">Their VAT rate</th>
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
