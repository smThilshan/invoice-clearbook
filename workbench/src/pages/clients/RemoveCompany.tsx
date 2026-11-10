import { Archive, ArchiveRestore, CircleCheck, Trash2, TriangleAlert } from 'lucide-react'
import { useState } from 'react'

import type { Client } from '@/api/client'
import {
  useDeleteClient,
  useDeletionPreview,
  useRemoveClient,
  useRemovedClients,
  useRestoreClient,
} from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'

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

/** Removed companies, for an admin to restore or delete permanently. Shown only when there are
 * some, or right after one was deleted. */
export function RemovedCompanies() {
  const removed = useRemovedClients(true)
  const restore = useRestoreClient()
  const [deleted, setDeleted] = useState<{ name: string; documents: number; filesRemoved: boolean } | null>(null)

  if (removed.isError) return <ErrorAlert title="Removed companies could not be loaded" error={removed.error} />
  if (!removed.data || (removed.data.length === 0 && !deleted)) return null
  return (
    <section aria-label="Removed companies" className="space-y-2 rounded-lg border border-dashed p-4">
      <h2 className="font-medium">Removed companies</h2>
      <p className="text-sm text-muted-foreground">Hidden everywhere, nothing deleted. Restore brings one back as it was.</p>
      <ul className="divide-y">
        {removed.data.map((client) => (
          <li key={client.client_id} aria-label={client.name} className="flex items-center justify-between gap-3 py-2">
            <span className="truncate text-sm">{client.name}</span>
            <span className="flex gap-2">
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
              <DeleteCompanyDialog
                client={client}
                onDeleted={(result) =>
                  setDeleted({ name: client.name, documents: result.documents, filesRemoved: result.files_removed })
                }
              />
            </span>
          </li>
        ))}
      </ul>
      {restore.isError && <ErrorAlert title="Not restored" error={restore.error} />}
      {deleted && (
        <p role="status" className="flex items-start gap-2 text-sm">
          {deleted.filesRemoved ? (
            <CircleCheck className="mt-0.5 size-4 shrink-0 text-success" aria-hidden />
          ) : (
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
          )}
          <span>
            {deleted.name} was deleted permanently, with {plural(deleted.documents, 'document')}.
            {deleted.filesRemoved ? '' : ' Some of its stored files could not be removed: tell IT, nothing else is affected.'}
          </span>
        </p>
      )}
    </section>
  )
}

/** Decision 91: the second step after Remove, cannot be undone. The admin sees what goes and
 * types the company's name; the API checks the name again. */
function DeleteCompanyDialog({
  client,
  onDeleted,
}: {
  client: Client
  onDeleted: (result: { documents: number; files_removed: boolean }) => void
}) {
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState('')
  const check = useDeletionPreview(client.client_id, open)
  const remove = useDeleteClient()
  const matches = typed.trim() === client.name
  const preview = check.data
  const system = preview?.connected_to ?? 'the accounting system'

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) {
          setTyped('')
          remove.reset()
        }
      }}
    >
      <DialogTrigger asChild>
        <Button type="button" variant="destructive" size="sm">
          <Trash2 aria-hidden />
          Delete permanently…
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete {client.name} permanently?</DialogTitle>
          <DialogDescription>This cannot be undone.</DialogDescription>
        </DialogHeader>
        {check.isError ? (
          <ErrorAlert title="What would be deleted could not be checked" error={check.error} />
        ) : !preview ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <div className="space-y-2 text-sm">
            <p>
              Deleted from Clearbook: {plural(preview.documents, 'document')} with their files, AI answers,
              corrections and history, and the company&rsquo;s accounts, VAT rates, suppliers and settings.
            </p>
            {preview.posted > 0 && (
              <p>
                {plural(preview.posted, 'document')} {preview.posted > 1 ? 'were' : 'was'} posted to {system}:{' '}
                {preview.posted > 1 ? 'they stay' : 'it stays'} there. A proof record keeps{' '}
                {preview.posted > 1 ? 'their IDs' : 'its ID'}.
              </p>
            )}
            {preview.connected_to && (
              <p>
                It is disconnected from {preview.connected_to} first. Nothing in {preview.connected_to} is changed.
              </p>
            )}
            <p className="text-muted-foreground">
              Its source folders (on this Mac or in SharePoint) are not touched. Backups keep a copy until they
              expire.
            </p>
            {preview.blocked && (
              <Alert variant="destructive">
                <TriangleAlert aria-hidden />
                <AlertTitle>Cannot be deleted now</AlertTitle>
                <AlertDescription>{preview.blocked}</AlertDescription>
              </Alert>
            )}
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor={`delete-${client.client_id}`}>
            Type <span className="font-semibold">{client.name}</span> to confirm
          </Label>
          <Input
            id={`delete-${client.client_id}`}
            autoComplete="off"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
          />
        </div>
        {remove.isError && <ErrorAlert title="Not deleted" error={remove.error} />}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={!matches || !preview || Boolean(preview.blocked) || remove.isPending}
            onClick={() =>
              remove.mutate(
                { clientId: client.client_id, name: typed.trim() },
                {
                  onSuccess: (result) => {
                    setOpen(false)
                    onDeleted(result)
                  },
                },
              )
            }
          >
            {remove.isPending ? 'Deleting…' : 'Delete permanently'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function plural(count: number, one: string) {
  return `${count} ${count === 1 ? one : `${one}s`}`
}
