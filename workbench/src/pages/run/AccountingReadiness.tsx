import { CircleCheck, Link2, RefreshCw } from 'lucide-react'
import { useEffect, useRef } from 'react'

import { useAccounting, useMe, useSyncAccounting } from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { Button } from '@/components/ui/button'
import { readinessOf } from '@/lib/readiness'

/** The client's accounting system on the Run page, with what to do before choosing documents:
 * connect (any configured system, coming back here), sync, or sign in again. `autoSync`: the
 * user just connected from here, so the sync starts by itself. */
export function AccountingReadiness({ clientId, autoSync }: { clientId: string; autoSync: boolean }) {
  const status = useAccounting(clientId)
  const sync = useSyncAccounting()
  const admin = useMe().data?.role === 'ADMIN'
  const readiness = readinessOf(status.data, status.isError)
  const started = useRef(false)

  useEffect(() => {
    if (!autoSync || started.current || readiness !== 'not-synced' || !admin) return
    started.current = true
    sync.mutate(clientId)
  }, [autoSync, readiness, admin, clientId, sync])

  const data = status.data
  const system = data?.provider_name ?? 'the accounting system'
  const query = new URLSearchParams({ client_id: clientId, back: 'run' })
  const connectTo = (connectUrl: string) => `/api${connectUrl}?${query}`
  const current = data?.providers.find((p) => p.key === data.provider)
  const available = (data?.providers ?? []).filter((p) => p.configured)

  return (
    <div aria-label="Accounting system" className="space-y-2 rounded-lg border bg-muted/30 p-3 text-sm">
      {status.isPending && <p className="text-muted-foreground">Checking the accounting system…</p>}
      {status.isError && <ErrorAlert title="The accounting system could not be checked" error={status.error} />}

      {readiness === 'ready' && data && (
        <p className="flex items-center gap-1.5">
          <CircleCheck className="size-4 text-success" aria-hidden />
          {data.connected
            ? `${system}: ${data.organisation ?? ''}. Accounts synced (${data.accounts}).`
            : `No accounting system: uses the ${data.accounts} imported accounts.`}
        </p>
      )}

      {readiness === 'not-synced' && data && (
        <>
          <p>
            {system}: {data.organisation}.{' '}
            {sync.isPending
              ? `Bringing in accounts, VAT rates and suppliers from ${system}…`
              : 'Its accounts are not synced yet: the AI needs them to suggest accounts.'}
          </p>
          {admin ? (
            <Button size="sm" disabled={sync.isPending} onClick={() => sync.mutate(clientId)}>
              <RefreshCw className={sync.isPending ? 'animate-spin' : undefined} aria-hidden />
              {sync.isPending ? 'Syncing…' : 'Sync now'}
            </Button>
          ) : (
            <p className="text-muted-foreground">Ask an admin to sync this client.</p>
          )}
        </>
      )}

      {readiness === 'reconnect' && data && (
        <>
          <p>
            {system} needs a new sign-in{data.reconnect_reason ? `: ${data.reconnect_reason}` : ''}.
          </p>
          {admin && current ? (
            <Button asChild size="sm">
              {/* A full page change: the browser goes to its sign-in and comes back here. */}
              <a href={connectTo(current.connect_url)}>
                <Link2 aria-hidden />
                Connect {system} again
              </a>
            </Button>
          ) : (
            <p className="text-muted-foreground">Ask an admin to connect this client again.</p>
          )}
        </>
      )}

      {readiness === 'not-connected' && (
        <>
          <p>No accounting system is connected, and no accounts are imported for this client.</p>
          {!admin ? (
            <p className="text-muted-foreground">Ask an admin to connect this client to its accounting system.</p>
          ) : available.length === 0 ? (
            <p className="text-muted-foreground">
              No accounting system is set up here (its app keys go in .env). Accounts can be imported on the
              Clients page.
            </p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {available.map((provider) => (
                <Button key={provider.key} asChild size="sm">
                  {/* A full page change: the browser goes to its sign-in and comes back here. */}
                  <a href={connectTo(provider.connect_url)}>
                    <Link2 aria-hidden />
                    Connect {provider.name}
                  </a>
                </Button>
              ))}
            </div>
          )}
        </>
      )}

      {sync.isError && <ErrorAlert title="Not synced" error={sync.error} />}
    </div>
  )
}
