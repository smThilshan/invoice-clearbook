import { ChevronRight, CircleCheck, FileText, Folder as FolderIcon, Play, Plus, Upload } from 'lucide-react'
import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useBlocker, useNavigate, useSearchParams } from 'react-router'

import { api, type Client, type Folder, type FolderPlace } from '@/api/client'
import {
  useAccounting,
  useClients,
  useCreateClient,
  useFolder,
  usePlaces,
  useStartRun,
  useTypicalTime,
  useUploadRun,
} from '@/api/queries'
import { ClientSelect } from '@/components/ClientSelect'
import { ErrorAlert } from '@/components/ErrorAlert'
import { PageContainer, PageHeader } from '@/components/PageHeader'
import { ProgressBar } from '@/components/ProgressBar'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { type Choice, formatBytes, tooMuch } from '@/lib/chosenFiles'
import { blocksRun, readinessOf } from '@/lib/readiness'
import { formatDate } from '@/lib/labels'
import { lastFolder, rememberClient, rememberFolder } from '@/lib/remember'
import { formatDuration } from '@/lib/timings'
import { usePageTitle } from '@/lib/usePageTitle'
import { cn } from '@/lib/utils'

import { AccountingReadiness } from './AccountingReadiness'
import { ComputerPicker } from './ComputerPicker'

/** The client's own folder; the top of any other place (this Mac, shared drives) is too big
 * to run whole, so a folder inside it must be opened first. */
const CLIENT_PLACE = 'client'

/** One step of the path from the place down to the selected folder. */
interface Crumb {
  name: string
  path: string // relative to the place; '' = the place itself
  date: string | null // the date the folder name stands for
}

/** Where the documents are: on the user's own computer (uploaded, decision 78), or in a folder
 * the server can reach (decision 35). */
type Source = 'computer' | 'server'

/** Pick or create a client, choose the documents (a folder on this computer, or one in the
 * server's allowed places), confirm the date, start the run (tasks 4.3, 4.12; decisions 35, 78). */
export function RunPage() {
  const [client, setClient] = useState<Client>()
  const [source, setSource] = useState<Source>('computer')
  const [choice, setChoice] = useState<Choice>()
  const [choiceDate, setChoiceDate] = useState<string | null>(null) // from the chosen folder's name
  const [sent, setSent] = useState(0) // how much of the upload has gone, 0 to 1
  const latestChoice = useRef<Choice>(undefined)
  const uploaded = useRef(false) // the choice went up: leaving is fine
  // Back from connecting an accounting system (decision 79): ?client=…&connected=… or &error=…
  const [params, setParams] = useSearchParams()
  const [returned] = useState(() => ({
    client: params.get('client'),
    connected: params.get('connected'),
    system: params.get('system'),
    error: params.get('error'),
  }))
  const clients = useClients()
  const accounting = useAccounting(client?.client_id ?? '')
  const blocked = client !== undefined && blocksRun(readinessOf(accounting.data, accounting.isError))
  const [chosenPlace, setChosenPlace] = useState<FolderPlace>()
  const [savedPlace, setSavedPlace] = useState<string>()
  const [crumbs, setCrumbs] = useState<Crumb[]>([])
  const [date, setDate] = useState('')
  const places = usePlaces(client?.client_id)
  usePageTitle('Run')
  const startRun = useStartRun()
  const uploadRun = useUploadRun()
  const navigate = useNavigate()

  // The first place (the client's own folder when it has one) is open until another is picked.
  // A remembered folder (task 4.17) opens in its place when that place is still there.
  const place = chosenPlace ?? places.data?.find((p) => p.key === savedPlace) ?? places.data?.[0]
  const trail = crumbs.length > 0 ? crumbs : place ? [{ name: place.label, path: '', date: null }] : []
  const current = trail.at(-1)
  const folderDate = current?.date ?? null
  const tooBig = place !== undefined && place.key !== CLIENT_PLACE && current?.path === ''
  const listing = useFolder(client?.client_id, place?.key, current?.path ?? '')
  const typical = useTypicalTime().data?.typical_ms ?? null
  const files = source === 'server' ? listing.data?.total_files : choice?.invoices.length
  const estimate = files && typical ? files * typical : null
  const ready = source === 'server' ? place !== undefined && current !== undefined : choice !== undefined
  const chosenName = choice ? choice.folder || `${choice.invoices.length} chosen files` : ''

  function adopt(picked: Client) {
    const saved = lastFolder(picked.client_id)
    setClient(picked)
    setChosenPlace(undefined)
    setSavedPlace(saved?.place)
    setCrumbs(saved?.crumbs ?? [])
    if (source === 'server') setDate(saved?.crumbs.at(-1)?.date ?? '')
  }

  function pickClient(picked: Client) {
    adopt(picked)
    startRun.reset()
    uploadRun.reset()
  }

  // The client the sign-in was started for is picked again on arrival (decision 79).
  const returnedClient = returned.client ? clients.data?.find((c) => c.client_id === returned.client) : undefined
  if (returnedClient && !client) adopt(returnedClient)

  // Its outcome stays on screen (kept above); the address is cleaned up.
  useEffect(() => {
    if (params.size > 0 && (client || clients.data)) setParams({}, { replace: true })
  }, [params, client, clients.data, setParams])

  useLeaveGuard(() => choice !== undefined && choice.invoices.length > 0 && !uploaded.current)

  function pickSource(picked: Source) {
    setSource(picked)
    setDate(picked === 'server' ? (current?.date ?? '') : (choiceDate ?? ''))
    startRun.reset()
    uploadRun.reset()
  }

  function choose(chosen: Choice) {
    latestChoice.current = chosen
    setChoice(chosen)
    setChoiceDate(null)
    setDate('')
    uploadRun.reset()
    if (!chosen.folder) return
    void api
      .folderDate(chosen.folder)
      .then((found) => {
        if (latestChoice.current !== chosen) return // another folder was chosen meanwhile
        setChoiceDate(found.date)
        setDate(found.date ?? '')
      })
      .catch(() => undefined) // no date offered: the user can still type one
  }

  function pickPlace(picked: FolderPlace) {
    setChosenPlace(picked)
    setCrumbs([])
    setDate('')
    startRun.reset()
    if (client) rememberFolder(client.client_id, { place: picked.key, crumbs: [] })
  }

  function goTo(next: Crumb[]) {
    setCrumbs(next)
    setDate(next.at(-1)?.date ?? '')
    startRun.reset()
    if (client && place) rememberFolder(client.client_id, { place: place.key, crumbs: next })
  }

  function upload() {
    if (!client || !choice || choice.invoices.length === 0 || tooMuch(choice)) return
    setSent(0)
    uploadRun.mutate(
      { clientId: client.client_id, files: choice.invoices, folderDate: date || null, onProgress: setSent },
      {
        onSuccess: (accepted) => {
          uploaded.current = true
          void navigate(`/run/${accepted.run_id}`)
        },
      },
    )
  }

  function run() {
    if (!client || !place || !current || tooBig) return
    startRun.mutate(
      { client_id: client.client_id, place: place.key, folder: current.path, folder_date: date || null },
      { onSuccess: (accepted) => void navigate(`/run/${accepted.run_id}`) },
    )
  }

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        title="Run"
        description="Pick a client, choose the documents (a folder on your computer, or one on the server), confirm the date, and start processing. Sub-folders are included."
      />

      {returned.connected && (
        <Alert>
          <CircleCheck className="text-success" aria-hidden />
          <AlertTitle>Connected to {returned.system ?? 'the accounting system'}</AlertTitle>
          <AlertDescription>Organisation: {returned.connected}</AlertDescription>
        </Alert>
      )}
      {returned.error && (
        <Alert variant="destructive">
          <AlertTitle>Not connected</AlertTitle>
          <AlertDescription>{returned.error}</AlertDescription>
        </Alert>
      )}

      <Card className="overflow-hidden shadow-sm">
        <CardHeader>
          <CardTitle>1. Client</CardTitle>
          <CardDescription>The business these documents belong to.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <ClientSelect value={client?.client_id} onChange={pickClient} />
          <NewClient onCreated={pickClient} />
          {client && (
            <AccountingReadiness
              clientId={client.client_id}
              autoSync={returned.connected !== null && returned.client === client.client_id}
            />
          )}
        </CardContent>
      </Card>

      {client && (
        <Card className="overflow-hidden shadow-sm">
          <CardHeader>
            <CardTitle>2. Documents</CardTitle>
            <CardDescription>
              {source === 'computer'
                ? 'Choose a folder on your computer: every invoice in it, sub-folders included, is uploaded and run.'
                : 'Open the folder to process. Everything inside it, sub-folders included, is run.'}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <SourcePicker value={source} onPick={pickSource} />
            {blocked ? (
              <p className="text-sm text-muted-foreground">
                First get the client's accounting system ready (above): then choose the documents.
              </p>
            ) : source === 'computer' ? (
              <ComputerPicker choice={choice} onChoose={choose} />
            ) : places.isPending ? (
              <Skeleton className="h-8 w-64" />
            ) : places.isError ? (
              <ErrorAlert title="Places could not be loaded" error={places.error} />
            ) : places.data.length === 0 ? (
              <p className="text-sm text-muted-foreground">No folders can be reached from here.</p>
            ) : (
              <PlacePicker places={places.data} value={place} onPick={pickPlace} />
            )}
            {!blocked && source === 'server' && place && current && (
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

      {client && !blocked && ready && (
        <Card className="overflow-hidden shadow-sm">
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
              {source === 'server'
                ? dateHint(date, folderDate, current?.path ?? '')
                : dateHint(date, choiceDate, choice?.folder ?? '')}
            </p>
          </CardContent>
        </Card>
      )}

      {client && !blocked && source === 'computer' && choice && (
        <div className="space-y-3">
          {uploadRun.isError && <ErrorAlert title="The upload did not finish" error={uploadRun.error} />}
          <RunSummary client={client.name} folder={chosenName} date={date} files={files} estimate={estimate} />
          {uploadRun.isPending && (
            <div className="space-y-1">
              <ProgressBar value={sent} label="Upload progress" />
              <p className="text-sm text-muted-foreground">
                {sent < 1
                  ? `Uploading ${Math.round(sent * 100)}% of ${formatBytes(choice.bytes)}…`
                  : 'Uploaded. Starting the run…'}
              </p>
            </div>
          )}
          <Button
            size="lg"
            onClick={upload}
            disabled={uploadRun.isPending || choice.invoices.length === 0 || tooMuch(choice) !== null}
          >
            <Upload aria-hidden />
            {uploadRun.isPending ? 'Uploading…' : `Upload and run ${client.name}: ${chosenName}`}
          </Button>
        </div>
      )}

      {client && !blocked && source === 'server' && place && current && (
        <div className="space-y-3">
          {startRun.isError && <ErrorAlert title="The run did not start" error={startRun.error} />}
          <RunSummary
            client={client.name}
            folder={trail.map((c) => c.name).join(' / ')}
            date={date}
            files={files}
            estimate={estimate}
          />
          {tooBig && (
            <p className="text-sm text-muted-foreground">
              Open a folder inside {place.label} first: the whole of it is too much to run.
            </p>
          )}
          {!tooBig && files === 0 && (
            <p className="text-sm text-muted-foreground">No invoice files in this folder or below it.</p>
          )}
          <Button size="lg" onClick={run} disabled={startRun.isPending || tooBig || files === 0}>
            <Play aria-hidden />
            {startRun.isPending
              ? 'Starting…'
              : `Run ${client.name}: ${trail.map((c) => c.name).join(' / ')}`}
          </Button>
        </div>
      )}
    </PageContainer>
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

function SourcePicker({ value, onPick }: { value: Source; onPick: (source: Source) => void }) {
  const choices: [Source, string][] = [
    ['computer', 'On my computer'],
    ['server', 'On the server'],
  ]
  return (
    <div role="group" aria-label="Where the documents are" className="flex flex-wrap gap-2">
      {choices.map(([key, label]) => (
        <button
          key={key}
          type="button"
          aria-pressed={key === value}
          onClick={() => onPick(key)}
          className={cn(
            'rounded-md border px-3 py-1.5 text-sm',
            key === value ? 'border-primary bg-secondary font-medium' : 'text-muted-foreground hover:bg-muted',
          )}
        >
          {label}
        </button>
      ))}
    </div>
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
  return (
    <div className="space-y-2">
      <p className="flex items-center gap-1.5 text-sm text-muted-foreground" data-testid="file-count">
        <FileText className="size-4" aria-hidden />
        {fileCountText(listing.data.invoice_files, listing.data.total_files)}
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
                <span className="text-xs tabular-nums text-muted-foreground">
                  {folder.invoice_files === null
                    ? 'many files'
                    : folder.invoice_files > 0
                      ? `${folder.invoice_files} invoice${folder.invoice_files === 1 ? '' : 's'}`
                      : ''}
                </span>
                {folder.date && (
                  <span className="w-24 text-right text-xs text-muted-foreground">{formatDate(folder.date)}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** What a run would take in, said plainly (task 4.17). */
function fileCountText(direct: number, total: number | null): string {
  if (total === null) return 'Too many files below this folder to count: open a folder inside it.'
  if (total === 0) return 'No invoice files here or in its sub-folders.'
  const inside = total - direct
  const plural = (n: number) => `${n} invoice file${n === 1 ? '' : 's'}`
  if (inside === 0) return `${plural(total)} (PDF or photo) in this folder.`
  if (direct === 0) return `${plural(total)} in its sub-folders: a run takes them all.`
  return `${plural(total)}: ${direct} here and ${inside} in its sub-folders.`
}

/** The choices so far, and what a run will take: files and roughly how long on this computer. */
function RunSummary({
  client,
  folder,
  date,
  files,
  estimate,
}: {
  client: string
  folder: string
  date: string
  files: number | null | undefined
  estimate: number | null
}) {
  return (
    <dl aria-label="Run summary" className="grid gap-x-4 gap-y-1 rounded-lg border bg-muted/30 p-3 text-sm sm:grid-cols-[auto_1fr]">
      <dt className="text-muted-foreground">Client</dt>
      <dd className="font-medium">{client}</dd>
      <dt className="text-muted-foreground">Folder</dt>
      <dd className="truncate font-medium" title={folder}>{folder}</dd>
      <dt className="text-muted-foreground">Date</dt>
      <dd className="font-medium">{date ? formatDate(date) : 'none (period check skipped)'}</dd>
      {files !== undefined && files !== null && files > 0 && (
        <>
          <dt className="text-muted-foreground">Files</dt>
          <dd className="font-medium">
            {files} invoice file{files === 1 ? '' : 's'}
            {estimate !== null && (
              <span className="font-normal text-muted-foreground"> · about {formatDuration(estimate)} on this computer</span>
            )}
          </dd>
        </>
      )}
    </dl>
  )
}

function dateHint(date: string, folderDate: string | null, path: string): string {
  if (folderDate && date === folderDate && !/\b(19|20)\d\d\b/.test(path)) {
    // Decision 31: the most recent such date is assumed; old documents need the year changed.
    return (
      `Taken from the folder name, which has no year: ${folderDate.slice(0, 4)} is assumed. ` +
      'Change it if the documents are from another year.'
    )
  }
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

const LEAVE_WARNING = 'You chose documents but did not upload them. Leave anyway?'

/** Ask before leaving the page (in the app or closing the tab) while `pending()` is true. */
function useLeaveGuard(pending: () => boolean) {
  const blocker = useBlocker(pending)
  useEffect(() => {
    if (blocker.state !== 'blocked') return
    if (window.confirm(LEAVE_WARNING)) blocker.proceed()
    else blocker.reset()
  }, [blocker])
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (pending()) event.preventDefault()
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  })
}
