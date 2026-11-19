import {
  CircleCheck,
  Clock,
  Link2,
  PauseCircle,
  RefreshCw,
  Send,
  TriangleAlert,
  Undo2,
  Unlink,
} from 'lucide-react'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import type { Client, PostingSummary, SendMode, SyncResult } from '@/api/client'
import {
  useAccounting,
  useClients,
  useCodingOptions,
  useMe,
  useDisconnectAccounting,
  useSetPaidThrough,
  useSetSendMode,
  useSetOwnTrns,
  useSyncAccounting,
} from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { PageContainer, PageHeader } from '@/components/PageHeader'
import { SharePointConnection } from '@/components/SharePointConnection'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { formatDateTime, SEND_MODE_LABELS } from '@/lib/labels'
import { TAX_TYPE_LABELS } from '@/pages/document/formModel'
import { usePageTitle } from '@/lib/usePageTitle'
import { RemoveCompanyDialog, RemovedCompanies } from '@/pages/clients/RemoveCompany'

/** Clients and their accounting system (tasks 5.3, 5.4, 5.1): connect a client, sync its
 * master data, send approved bills waiting to be posted, disconnect. The accounting system's
 * name always comes from the API (decision 43): Xero today, others later. Its sign-in sends the
 * browser back here. Above them, Missan's SharePoint sign-in, used for every client (83). */
export function ClientsPage() {
  usePageTitle('Organization or Business')
  const clients = useClients()
  const admin = useMe().data?.role === 'ADMIN'
  const [params] = useSearchParams()
  const connected = params.get('connected')
  const system = params.get('system') ?? 'the accounting system'
  const failed = params.get('error')
  const sharePoint = params.get('sharepoint')
  const sharePointFailed = params.get('sharepoint_error')

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        title="Organization or Business"
        description="Each organization or business and its accounting system. Connect it to sync its accounts, VAT rates and suppliers, and to post approved bills."
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
      {sharePoint && (
        <Alert>
          <CircleCheck className="text-success" aria-hidden />
          <AlertTitle>Connected to SharePoint</AlertTitle>
          <AlertDescription>As {sharePoint}. Read only: nothing in SharePoint is changed.</AlertDescription>
        </Alert>
      )}
      {sharePointFailed && (
        <Alert variant="destructive">
          <AlertTitle>SharePoint not connected</AlertTitle>
          <AlertDescription>{sharePointFailed}</AlertDescription>
        </Alert>
      )}
      <section aria-label="SharePoint" className="space-y-2 rounded-lg border p-4">
        <h2 className="font-medium">SharePoint</h2>
        <p className="text-sm text-muted-foreground">
          Where the Run screen finds invoice folders, for every client. Read only.
        </p>
        <SharePointConnection back="clients" />
      </section>
      {clients.isPending ? (
        <Skeleton className="h-32 w-full" />
      ) : clients.isError ? (
        <ErrorAlert title="Organizations and businesses could not be loaded" error={clients.error} />
      ) : clients.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">No organizations or businesses yet: add one on the Run screen.</p>
      ) : (
        <ul aria-label="Organizations and businesses" className="space-y-3">
          {clients.data.map((client) => (
            <ClientRow key={client.client_id} client={client} />
          ))}
        </ul>
      )}
      {admin && <RemovedCompanies />}
    </PageContainer>
  )
}

function ClientRow({ client }: { client: Client }) {
  const status = useAccounting(client.client_id, false)
  const disconnect = useDisconnectAccounting()
  const sync = useSyncAccounting()
  // Connecting the accounting system is an admin's, syncing everyone's (decision 71); the API
  // says so too.
  const admin = useMe().data?.role === 'ADMIN'
  const synced = Boolean(status.data?.connected && status.data.master_data_from === status.data.provider)
  const summary = status.data?.posting
  const connected = status.data?.connected === true
  // Decision 68: the access was withdrawn; nothing is sent until someone signs in again.
  const reconnect = status.data?.needs_reconnect === true
  // The connected system's name; before connecting, the systems that could be connected.
  const system = status.data?.provider_name ?? 'the accounting system'
  const available = (status.data?.providers ?? []).filter((p) => p.configured)
  const signIn = status.data?.providers.find((p) => p.key === status.data?.provider)
  // Decision 89: a client sending expenses as expenses sends bills and expenses, so the counts
  // say documents, and the sent ones are found in two places.
  const expensesWhere = status.data?.expenses_where ?? null
  const noun = expensesWhere ? 'document' : 'bill'
  const billsWhere = status.data?.bills_where ?? null
  const where = expensesWhere ? `${billsWhere ?? 'Bills'} (bills) and ${expensesWhere} (expenses)` : billsWhere
  const connectionLabel = status.isPending
    ? 'Checking connection'
    : status.isError
      ? 'Status unavailable'
      : reconnect
        ? `${system}: sign in again`
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
            <Badge variant={reconnect ? 'destructive' : connected ? 'secondary' : 'outline'}>
              {connectionLabel}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground">
            {status.isPending
              ? 'Checking…'
              : status.isError
                ? 'We could not confirm its accounting connection.'
                : status.data?.connected
                  ? `${system}: ${status.data.organisation}${status.data.connected_at ? ` · since ${formatDateTime(status.data.connected_at)}` : ''}${synced ? ` · coding uses ${system} accounts` : ' · not synced yet'}`
                  : 'No accounting system connected (CSV master data, export file)'}
          </p>
          <WhenSent client={client} system={system} admin={admin} />
          <OwnTrns client={client} admin={admin} />
          {connected && (
            <ExpensesSent
              client={client}
              system={system}
              admin={admin}
              blocked={status.data?.expenses_blocked ?? null}
              reconnectUrl={
                admin && status.data?.expenses_reconnect && signIn
                  ? `/api${signIn.connect_url}?${query}`
                  : null
              }
            />
          )}
          {summary && (
            <PostingLines
              summary={summary}
              synced={synced}
              system={system}
              clientId={client.client_id}
              showPaused={!reconnect}
              noun={noun}
              where={where}
            />
          )}
        </div>
      </div>
      {reconnect && (
        <Alert variant="destructive" aria-label="Sign in again" className="mt-3">
          <PauseCircle aria-hidden />
          <AlertTitle>{system} needs you to sign in again</AlertTitle>
          <AlertDescription className="space-y-2">
            <p>
              {summary && summary.paused > 0
                ? `${plural(summary.paused, noun)} paused: they are sent automatically as soon as you connect again.`
                : `Nothing is sent to ${system} until you connect again.`}
              {status.data?.reconnect_reason ? ` Reason: ${status.data.reconnect_reason}.` : ''}
            </p>
            {!admin && <p>Ask an admin to connect it again.</p>}
            {signIn && admin && (
              <Button asChild size="sm">
                {/* A full page change: the browser goes to its sign-in and comes back here. */}
                <a href={`/api${signIn.connect_url}?${query}`}>
                  <Link2 aria-hidden />
                  Connect {system} again
                </a>
              </Button>
            )}
          </AlertDescription>
        </Alert>
      )}
      <div className="mt-4 flex flex-wrap gap-2 border-t pt-3">
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
        ) : !admin ? (
          <span className="self-center text-xs text-muted-foreground">
            An admin connects the accounting system.
          </span>
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
        {admin && <RemoveCompanyDialog client={client} />}
      </div>
      {status.isError && <ErrorAlert title="Connection status could not be loaded" error={status.error} />}
      {disconnect.isError && <ErrorAlert title="Not disconnected" error={disconnect.error} />}
      {sync.isError && <ErrorAlert title="Not synced" error={sync.error} />}
      {sync.data && <SyncSummary result={sync.data} />}
    </li>
  )
}

/** Decision 98: approved documents go straight away, or wait for someone to press Send in the
 * review queue. Documents approved from now on; ones already approved keep theirs. */
function WhenSent({ client, system, admin }: { client: Client; system: string; admin: boolean }) {
  const change = useSetSendMode()
  const label = `Sent to ${system}`
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-muted-foreground">{label}:</span>
      {admin ? (
        <Select
          value={client.send_mode}
          disabled={change.isPending}
          onValueChange={(value) => change.mutate({ clientId: client.client_id, sendMode: value as SendMode })}
        >
          <SelectTrigger size="sm" className="w-auto max-w-full" aria-label={label}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(SEND_MODE_LABELS).map(([value, text]) => (
              <SelectItem key={value} value={value}>
                {text}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <span>{SEND_MODE_LABELS[client.send_mode]}</span>
      )}
      {change.isError && <ErrorAlert title="Not changed" error={change.error} />}
    </div>
  )
}

/** Decision 92: the business's own TRNs. On a document, one of these is the customer's TRN,
 * never the supplier's: the checks then tell the two apart and offer the supplier's. */
function OwnTrns({ client, admin }: { client: Client; admin: boolean }) {
  const change = useSetOwnTrns()
  const [editing, setEditing] = useState<string | null>(null)
  const shown = client.own_trns.length ? client.own_trns.join(', ') : 'none entered'
  const label = 'Own TRNs'
  if (editing === null) {
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">{label}:</span>
        <span>{shown}</span>
        {admin && (
          <Button type="button" variant="link" size="sm" className="h-auto p-0" onClick={() => setEditing(client.own_trns.join(', '))}>
            Edit
          </Button>
        )}
      </div>
    )
  }
  const save = () =>
    change.mutate(
      { clientId: client.client_id, ownTrns: editing.split(/[,\n]/).map((trn) => trn.trim()).filter(Boolean) },
      { onSuccess: () => setEditing(null) },
    )
  return (
    <div className="space-y-1.5 text-sm">
      <label htmlFor={`own-trns-${client.client_id}`} className="text-muted-foreground">
        {label}: every TRN its invoices are addressed to, separated by commas
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          id={`own-trns-${client.client_id}`}
          className="h-8 max-w-md"
          autoComplete="off"
          value={editing}
          onChange={(event) => setEditing(event.target.value)}
        />
        <Button type="button" size="sm" disabled={change.isPending} onClick={save}>
          {change.isPending ? 'Saving…' : 'Save'}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => {
            setEditing(null)
            change.reset()
          }}
        >
          Cancel
        </Button>
      </div>
      {change.isError && <ErrorAlert title="Not saved" error={change.error} />}
    </div>
  )
}

const EACH_TIME = 'each-time' // no default: the account is chosen on each expense

/** Decisions 89 and 101: expenses (paid when bought) always go as expenses, paid through an
 * account; not a choice. `blocked`: why they go as bills for now (the system cannot take them,
 * or its connection must be made again). */
function ExpensesSent({
  client,
  system,
  admin,
  blocked,
  reconnectUrl,
}: {
  client: Client
  system: string
  admin: boolean
  blocked: string | null
  reconnectUrl: string | null // signing in again would allow expenses (an admin's)
}) {
  const change = useSetPaidThrough()
  const options = useCodingOptions(client.client_id)
  const accounts = options.data?.paid_through ?? []
  const groups = [...new Set(accounts.map((a) => a.group))]
  const payLabel = 'Usually paid through'
  const asExpenses = !blocked
  const paidThrough = accounts.find((a) => a.code === client.paid_through)?.label ?? 'Chosen on each expense'
  return (
    <div className="flex flex-col gap-1 text-sm">
      {asExpenses && (
        <p className="text-muted-foreground">
          Expenses go to {system} as expenses: final there once sent. Bills go as bills.
        </p>
      )}
      {blocked && (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-muted-foreground">Expenses go as bills: {blocked}.</p>
          {reconnectUrl && (
            <Button asChild size="sm" variant="outline">
              {/* A full page change: the browser goes to its sign-in and comes back here. */}
              <a href={reconnectUrl}>
                <Link2 aria-hidden />
                Connect {system} again
              </a>
            </Button>
          )}
        </div>
      )}
      {asExpenses && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-muted-foreground">{payLabel}:</span>
          {admin ? (
            <Select
              value={client.paid_through ?? EACH_TIME}
              disabled={change.isPending}
              onValueChange={(value) =>
                change.mutate({ clientId: client.client_id, paidThrough: value === EACH_TIME ? null : value })
              }
            >
              <SelectTrigger size="sm" className="w-auto max-w-full" aria-label={payLabel}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={EACH_TIME}>Chosen on each expense</SelectItem>
                {groups.map((group) => (
                  <SelectGroup key={group}>
                    <SelectLabel>{group}</SelectLabel>
                    {accounts
                      .filter((a) => a.group === group)
                      .map((a) => (
                        <SelectItem key={a.code} value={a.code}>
                          {a.label}
                        </SelectItem>
                      ))}
                  </SelectGroup>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <span>{paidThrough}</span>
          )}
        </div>
      )}
      {asExpenses && !client.paid_through && accounts.length > 0 && (
        <p className="text-muted-foreground">
          No usual account: Paid through is chosen on each expense before it is approved.
        </p>
      )}
      {asExpenses && options.isSuccess && accounts.length === 0 && (
        <p className="text-muted-foreground">
          No accounts to pay through yet: sync from {system} first.
        </p>
      )}
      {change.isError && <ErrorAlert title="Not changed" error={change.error} />}
    </div>
  )
}

function plural(count: number, one: string, many = `${one}s`) {
  return `${count} ${count === 1 ? one : many}`
}

const NOT_YET_LABELS: Record<string, [string, string]> = {
  RECEIPT: ['receipt', 'receipts'],
  CREDIT_NOTE: ['credit note', 'credit notes'],
}

/** Every approved document, in plain words: ready, paused, retrying, sent back, not possible yet,
 * done. */
function PostingLines({
  summary,
  synced,
  system,
  clientId,
  showPaused,
  noun,
  where,
}: {
  summary: PostingSummary
  synced: boolean
  system: string
  clientId: string
  showPaused: boolean // false while the sign-in banner says it already
  noun: string // 'bill', or 'document' when expenses go as expenses too
  where: string | null // where the accountant finds them in the accounting system
}) {
  return (
    <ul aria-label="Approved documents" className="space-y-0.5 text-sm">
      {summary.held > 0 && (
        <li className="flex items-start gap-1.5 font-medium text-warning">
          <Send className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>
            {plural(summary.held, `approved ${noun}`)} waiting for Send.{' '}
            <Link
              to={`/queue?${new URLSearchParams({ client: clientId, sending: 'held' })}`}
              className="underline underline-offset-2"
            >
              Open the list
            </Link>{' '}
            to check and send them.
          </span>
        </li>
      )}
      {summary.ready > 0 && (
        <li className="font-medium text-warning">
          {synced
            ? `${plural(summary.ready, `approved ${noun}`)} on the way to ${system}.`
            : `${plural(summary.ready, `approved ${noun}`)} waiting: connect and sync the accounting system to send them.`}
        </li>
      )}
      {showPaused && summary.paused > 0 && (
        <li className="flex items-start gap-1.5 font-medium text-destructive">
          <PauseCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>
            {plural(summary.paused, `approved ${noun}`)} paused
            {summary.paused_reason ? `: ${summary.paused_reason}` : ''}.
          </span>
        </li>
      )}
      {summary.retrying > 0 && (
        <li className="flex items-start gap-1.5 text-warning">
          <Clock className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>
            {plural(summary.retrying, noun)} will be sent again automatically
            {summary.retry_at ? ` at ${formatTime(summary.retry_at)}` : ''}
            {summary.retry_reason ? `: ${summary.retry_reason}` : ''}.
          </span>
        </li>
      )}
      {summary.failed > 0 && (
        <li className="flex items-start gap-1.5 font-medium text-destructive">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>
            {plural(summary.failed, noun)} could not be sent
            {summary.failed_reason ? `: ${summary.failed_reason}` : ''}.{' '}
            <Link
              to={`/queue?${new URLSearchParams({ client: clientId, sending: 'failed' })}`}
              className="underline underline-offset-2"
            >
              Open the list
            </Link>{' '}
            to try again.
          </span>
        </li>
      )}
      {summary.sent_back > 0 && (
        <li className="flex items-start gap-1.5 text-destructive">
          <Undo2 className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>
            {plural(summary.sent_back, noun)} not sent to {system}, back in review.{' '}
            <Link
              to={`/queue?${new URLSearchParams({ client: clientId, sending: 'not_sent' })}`}
              className="underline underline-offset-2"
            >
              Show them
            </Link>{' '}
            with the reason.
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
          {plural(summary.posted, noun)} sent to {system} so far{where ? `: find them in ${where}` : ''}.
        </li>
      )}
    </ul>
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
