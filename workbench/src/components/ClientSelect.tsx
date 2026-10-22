import type { Client } from '@/api/client'
import { useClients } from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { rememberClient } from '@/lib/remember'

/** The client dropdown used on every screen; remembers the last pick on this computer. */
export function ClientSelect({
  value,
  onChange,
}: {
  value: string | undefined
  onChange: (client: Client) => void
}) {
  const clients = useClients()

  if (clients.isPending) return <Skeleton className="h-8 w-72" />
  if (clients.isError) {
    return <ErrorAlert title="Clients could not be loaded" error={clients.error} />
  }
  if (clients.data.length === 0) {
    return <p className="text-sm text-muted-foreground">No clients yet: add one on the Run screen.</p>
  }

  function pick(clientId: string) {
    const picked = clients.data?.find((c) => c.client_id === clientId)
    if (!picked) return
    rememberClient(picked.client_id)
    onChange(picked)
  }

  return (
    <Select value={value ?? ''} onValueChange={pick}>
      <SelectTrigger className="w-72 max-w-full" aria-label="Client">
        <SelectValue placeholder="Choose a client" />
      </SelectTrigger>
      <SelectContent>
        {clients.data.map((c) => (
          <SelectItem key={c.client_id} value={c.client_id}>
            {c.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
