import { ArchiveRestore, Archive } from 'lucide-react'
import { useState } from 'react'

import type { Client } from '@/api/client'
import { useRemoveClient, useRemovedClients, useRestoreClient } from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'

/** Decision 91: an admin removes a company. It is hidden everywhere and nothing is deleted;
 * Restore brings it back. The API refuses while it still has work in flight, and says why. */
export function RemoveCompanyDialog({ client }: { client: Client }) {
  const [open, setOpen] = useState(false)
  const remove = useRemoveClient()

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) remove.reset()
      }}
    >
      <DialogTrigger asChild>
        <Button type="button" variant="outline" size="sm" className="ml-auto">
          <Archive aria-hidden />
          Remove…
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Remove {client.name}?</DialogTitle>
          <DialogDescription>
            It disappears from every list, picker and queue. Nothing is deleted: its documents,
            history and accounting connection are kept, and an admin can restore it below at any
            time.
          </DialogDescription>
        </DialogHeader>
        {remove.isError && <ErrorAlert title="Not removed" error={remove.error} />}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={remove.isPending}
            onClick={() => remove.mutate(client.client_id, { onSuccess: () => setOpen(false) })}
          >
            {remove.isPending ? 'Removing…' : 'Remove company'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Removed companies, for an admin to restore. Shown only when there are some. */
export function RemovedCompanies() {
  const removed = useRemovedClients(true)
  const restore = useRestoreClient()

  if (removed.isError) return <ErrorAlert title="Removed companies could not be loaded" error={removed.error} />
  if (!removed.data || removed.data.length === 0) return null
  return (
    <section aria-label="Removed companies" className="space-y-2 rounded-lg border border-dashed p-4">
      <h2 className="font-medium">Removed companies</h2>
      <p className="text-sm text-muted-foreground">Hidden everywhere, nothing deleted. Restore brings one back as it was.</p>
      <ul className="divide-y">
        {removed.data.map((client) => (
          <li key={client.client_id} aria-label={client.name} className="flex items-center justify-between gap-3 py-2">
            <span className="truncate text-sm">{client.name}</span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={restore.isPending}
              onClick={() => restore.mutate(client.client_id)}
            >
              <ArchiveRestore aria-hidden />
              Restore
            </Button>
          </li>
        ))}
      </ul>
      {restore.isError && <ErrorAlert title="Not restored" error={restore.error} />}
    </section>
  )
}
