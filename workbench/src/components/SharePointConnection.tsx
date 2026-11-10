import { CircleCheck, Link2, Unplug } from 'lucide-react'

import { useDisconnectSharePoint, useMe, useSharePointStatus } from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { Button } from '@/components/ui/button'

/** Missan's SharePoint sign-in (decision 83): whose read-only access the Workbench uses, and,
 * for an admin, Connect / Connect again / Disconnect. `back`: the page the sign-in returns to.
 * `showConnected`: false hides the line once connected (the Run page shows the picker instead). */
export function SharePointConnection({ back, showConnected = true }: { back: 'clients' | 'run'; showConnected?: boolean }) {
  const status = useSharePointStatus()
  const disconnect = useDisconnectSharePoint()
  const admin = useMe().data?.role === 'ADMIN'
  const connectUrl = `/api/sharepoint/connect?${new URLSearchParams({ back })}`

  if (status.isPending) return <p className="text-sm text-muted-foreground">Checking SharePoint…</p>
  if (status.isError) return <ErrorAlert title="SharePoint could not be checked" error={status.error} />
  const data = status.data
  if (!data.configured) {
    return <p className="text-sm text-muted-foreground">SharePoint is not set up on this server.</p>
  }

  if (data.connected && !data.needs_reconnect) {
    if (!showConnected) return null
    return (
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <p className="flex items-center gap-1.5">
          <CircleCheck className="size-4 text-success" aria-hidden />
          Connected as {data.account_name}
          {data.account_email ? ` (${data.account_email})` : ''}. Read only: nothing in SharePoint is changed.
        </p>
        {admin && (
          <Button size="sm" variant="ghost" disabled={disconnect.isPending} onClick={() => disconnect.mutate()}>
            <Unplug aria-hidden />
            {disconnect.isPending ? 'Disconnecting…' : 'Disconnect'}
          </Button>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-2 text-sm">
      <p>
        {data.needs_reconnect
          ? `SharePoint needs to be connected again: ${data.reconnect_reason ?? 'the sign-in stopped working'}.`
          : 'SharePoint is not connected yet.'}{' '}
        {admin
          ? 'Sign in with your Microsoft account: the Workbench then reads the sites and folders you can open, and never changes anything.'
          : 'An admin connects it on the Organization or Business page. Until then, choose documents on your computer.'}
      </p>
      {admin && (
        <Button asChild size="sm">
          <a href={connectUrl}>
            <Link2 aria-hidden />
            {data.needs_reconnect ? 'Connect SharePoint again' : 'Connect SharePoint'}
          </a>
        </Button>
      )}
    </div>
  )
}
