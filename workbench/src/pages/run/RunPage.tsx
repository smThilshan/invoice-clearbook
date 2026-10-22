import { ChevronRight, FileText, Folder as FolderIcon, Play, Plus } from 'lucide-react'
import { type FormEvent, useState } from 'react'
import { useNavigate } from 'react-router'

import type { Client, Folder, FolderPlace } from '@/api/client'
import { useCreateClient, useFolder, usePlaces, useStartRun } from '@/api/queries'
import { ClientSelect } from '@/components/ClientSelect'
import { ErrorAlert } from '@/components/ErrorAlert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { formatDate } from '@/lib/labels'
import { rememberClient } from '@/lib/remember'
import { cn } from '@/lib/utils'

/** The client's own folder; the top of any other place (this Mac, shared drives) is too big
 * to run whole, so a folder inside it must be opened first. */
const CLIENT_PLACE = 'client'

/** One step of the path from the place down to the selected folder. */
interface Crumb {
  name: string
  path: string // relative to the place; '' = the place itself
  date: string | null // the date the folder name stands for
}

/** Pick or create a client, pick a folder anywhere in the allowed places, confirm the date,
 * start the run (tasks 4.3, 4.12; decision 35). */
export function RunPage() {
  const [client, setClient] = useState<Client>()
  const [chosenPlace, setChosenPlace] = useState<FolderPlace>()
  const [crumbs, setCrumbs] = useState<Crumb[]>([])
  const [date, setDate] = useState('')
  const places = usePlaces(client?.client_id)
  const startRun = useStartRun()
  const navigate = useNavigate()

  // The first place (the client's own folder when it has one) is open until another is picked.
  const place = chosenPlace ?? places.data?.[0]
  const trail = crumbs.length > 0 ? crumbs : place ? [{ name: place.label, path: '', date: null }] : []
  const current = trail.at(-1)
  const folderDate = current?.date ?? null
  const tooBig = place !== undefined && place.key !== CLIENT_PLACE && current?.path === ''

  function pickClient(picked: Client) {
    setClient(picked)
    setChosenPlace(undefined)
    setCrumbs([])
    setDate('')
    startRun.reset()
  }

  function pickPlace(picked: FolderPlace) {
    setChosenPlace(picked)
    setCrumbs([])
    setDate('')
    startRun.reset()
  }

  function goTo(next: Crumb[]) {
    setCrumbs(next)
    setDate(next.at(-1)?.date ?? '')
    startRun.reset()
  }

  function run() {
    if (!client || !place || !current || tooBig) return
    startRun.mutate(
      { client_id: client.client_id, place: place.key, folder: current.path, folder_date: date || null },
      { onSuccess: (accepted) => void navigate(`/run/${accepted.run_id}`) },
    )
  }

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h2 className="text-xl font-semibold">Run</h2>
        <p className="text-sm text-muted-foreground">
          Pick a client and a folder (on this Mac or a shared drive), confirm the date, and start
          processing. Sub-folders are included.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>1. Client</CardTitle>
          <CardDescription>The business these documents belong to.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <ClientSelect value={client?.client_id} onChange={pickClient} />
          <NewClient onCreated={pickClient} />
        </CardContent>
      </Card>

      {client && (
        <Card>
          <CardHeader>
            <CardTitle>2. Folder</CardTitle>
            <CardDescription>
              Open the folder to process. Everything inside it, sub-folders included, is run.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {places.isPending ? (
              <Skeleton className="h-8 w-64" />
            ) : places.isError ? (
              <ErrorAlert title="Places could not be loaded" error={places.error} />
            ) : places.data.length === 0 ? (
              <p className="text-sm text-muted-foreground">No folders can be reached from here.</p>
            ) : (
              <PlacePicker places={places.data} value={place} onPick={pickPlace} />
            )}
            {place && current && (
              <>
                <Breadcrumbs crumbs={trail} onGoTo={goTo} />
                <FolderList
                  clientId={client.client_id}
                  place={place}
                  path={current.path}
                  onOpen={(folder) =>
                    goTo([...trail, { name: folder.name, path: folder.path, date: folder.date }])
                  }
                />
              </>
            )}
          </CardContent>
        </Card>
      )}

      {client && place && current && (
        <Card>
          <CardHeader>
            <CardTitle>3. Date</CardTitle>
            <CardDescription>The date the documents in this folder belong to.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <Label htmlFor="folder-date">Folder date</Label>
            <Input
              id="folder-date"
              type="date"
              className="w-48"
              value={date}
              onChange={(event) => setDate(event.target.value)}
            />
            <p className="text-sm text-muted-foreground" data-testid="date-hint">
              {dateHint(date, folderDate)}
            </p>
          </CardContent>
        </Card>
      )}

      {client && place && current && (
        <div className="space-y-3">
          {startRun.isError && <ErrorAlert title="The run did not start" error={startRun.error} />}
          {tooBig && (
            <p className="text-sm text-muted-foreground">
              Open a folder inside {place.label} first: the whole of it is too much to run.
            </p>
          )}
          <Button size="lg" onClick={run} disabled={startRun.isPending || tooBig}>
            <Play aria-hidden />
            {startRun.isPending
              ? 'Starting…'
              : `Run ${client.name}: ${trail.map((c) => c.name).join(' / ')}`}
          </Button>
        </div>
      )}
    </div>
  )
}

/** "+ New client": a name is enough; the new client is picked straight away. */
function NewClient({ onCreated }: { onCreated: (client: Client) => void }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const create = useCreateClient()

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!name.trim()) return
    create.mutate(
      { name: name.trim() },
      {
        onSuccess: (created) => {
          rememberClient(created.client_id)
          onCreated(created)
          setName('')
          setOpen(false)
        },
      },
    )
  }

  if (!open) {
    return (
      <Button type="button" variant="outline" size="sm" onClick={() => setOpen(true)}>
        <Plus aria-hidden />
        New client
      </Button>
    )
  }
  return (
    <form onSubmit={submit} aria-label="New client" className="space-y-2">
      <Label htmlFor="new-client-name">New client name</Label>
      <div className="flex flex-wrap gap-2">
        <Input
          id="new-client-name"
          className="w-72 max-w-full"
          placeholder="e.g. Blue Sky Trading LLC"
          value={name}
          autoFocus
          onChange={(event) => setName(event.target.value)}
        />
        <Button type="submit" disabled={create.isPending || !name.trim()}>
          {create.isPending ? 'Creating…' : 'Create'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
      {create.isError && <ErrorAlert title="The client was not created" error={create.error} />}
    </form>
  )
}

function PlacePicker({
  places,
  value,
  onPick,
}: {
  places: FolderPlace[]
  value: FolderPlace | undefined
  onPick: (place: FolderPlace) => void
}) {
  return (
    <div role="group" aria-label="Place" className="flex flex-wrap gap-2">
      {places.map((place) => (
        <button
          key={place.key}
          type="button"
          aria-pressed={place.key === value?.key}
          title={place.path}
          onClick={() => onPick(place)}
          className={cn(
            'rounded-md border px-3 py-1.5 text-sm',
            place.key === value?.key
              ? 'border-primary bg-secondary font-medium'
              : 'text-muted-foreground hover:bg-muted',
          )}
        >
          {place.label}
        </button>
      ))}
    </div>
  )
}

function Breadcrumbs({ crumbs, onGoTo }: { crumbs: Crumb[]; onGoTo: (trail: Crumb[]) => void }) {
  return (
    <nav aria-label="Folder path" className="flex flex-wrap items-center gap-1 text-sm">
      {crumbs.map((crumb, index) => {
        const last = index === crumbs.length - 1
        return (
          <span key={crumb.path} className="flex items-center gap-1">
            {index > 0 && <ChevronRight className="size-3.5 text-muted-foreground" aria-hidden />}
            {last ? (
              <span className="font-medium" aria-current="location">
                {crumb.name}
              </span>
            ) : (
              <button
                type="button"
                className="text-muted-foreground underline-offset-4 hover:underline"
                onClick={() => onGoTo(crumbs.slice(0, index + 1))}
              >
                {crumb.name}
              </button>
            )}
          </span>
        )
      })}
    </nav>
  )
}

function FolderList({
  clientId,
  place,
  path,
  onOpen,
}: {
  clientId: string
  place: FolderPlace
  path: string
  onOpen: (folder: Folder) => void
}) {
  const listing = useFolder(clientId, place.key, path)

  if (listing.isPending) return <Skeleton className="h-24 w-full" />
  if (listing.isError) {
    return <ErrorAlert title="This folder could not be opened" error={listing.error} />
  }
  const files = listing.data.invoice_files
  return (
    <div className="space-y-2">
      <p className="flex items-center gap-1.5 text-sm text-muted-foreground" data-testid="file-count">
        <FileText className="size-4" aria-hidden />
        {files === 0
          ? 'No invoice files directly in this folder.'
          : `${files} invoice file${files === 1 ? '' : 's'} (PDF or photo) directly in this folder.`}
      </p>
      {listing.data.folders.length === 0 ? (
        <p className="text-sm text-muted-foreground">No sub-folders here.</p>
      ) : (
        <ul aria-label="Sub-folders" className="max-h-80 divide-y overflow-y-auto rounded-lg border">
          {listing.data.folders.map((folder) => (
            <li key={folder.path}>
              <button
                type="button"
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted"
                onClick={() => onOpen(folder)}
              >
                <FolderIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                <span className="flex-1 truncate">{folder.name}</span>
                {folder.date && (
                  <span className="text-xs text-muted-foreground">{formatDate(folder.date)}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function dateHint(date: string, folderDate: string | null): string {
  if (folderDate && date === folderDate) return 'Taken from the folder name.'
  if (date === '' && folderDate) return 'No date: the period check is skipped for this run.'
  if (date === '') {
    return (
      'The folder name has no date. Enter the date the documents belong to, or leave it ' +
      'empty: the period check is then skipped.'
    )
  }
  return 'Entered by you.'
}
