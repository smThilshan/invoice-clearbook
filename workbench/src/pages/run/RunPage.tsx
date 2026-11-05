import { CircleCheck, Play, Plus, Upload } from 'lucide-react'
import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useBlocker, useNavigate, useSearchParams } from 'react-router'

import { api, type Client } from '@/api/client'
import {
  useAccounting,
  useClients,
  useCreateClient,
  useSharePointFolder,
  useSharePointStatus,
  useStartSharePointRun,
  useTypicalTime,
  useUploadRun,
} from '@/api/queries'
import { ClientSelect } from '@/components/ClientSelect'
import { ErrorAlert } from '@/components/ErrorAlert'
import { SharePointConnection } from '@/components/SharePointConnection'
import { PageContainer, PageHeader } from '@/components/PageHeader'
import { ProgressBar } from '@/components/ProgressBar'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { type Choice, formatBytes, tooMuch } from '@/lib/chosenFiles'
import { blocksRun, readinessOf } from '@/lib/readiness'
import { formatDate } from '@/lib/labels'
import { lastTrail, rememberClient, rememberTrail } from '@/lib/remember'
import { isStep, type Step } from '@/lib/sharePoint'
import { formatDuration } from '@/lib/timings'
import { usePageTitle } from '@/lib/usePageTitle'
import { cn } from '@/lib/utils'

import { AccountingReadiness } from './AccountingReadiness'
import { ComputerPicker } from './ComputerPicker'
import { SharePointPicker } from './SharePointPicker'

/** Where the documents are: a folder on SharePoint, read live (decision 83), or on the user's
 * own computer (uploaded, decision 78). */
type Source = 'sharepoint' | 'computer'

/** Pick or create a client, choose the documents (a folder on SharePoint, or one on this
 * computer), confirm the date, start the run (tasks 4.3, 4.12; decisions 78, 83). */
export function RunPage() {
  const [client, setClient] = useState<Client>()
  const [picked, setPicked] = useState<Source>('sharepoint')
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
    sharePoint: params.get('sharepoint'),
    sharePointError: params.get('sharepoint_error'),
  }))
  const clients = useClients()
  const accounting = useAccounting(client?.client_id ?? '')
  const blocked = client !== undefined && blocksRun(readinessOf(accounting.data, accounting.isError))
  const [trail, setTrail] = useState<Step[]>([])
  const [date, setDate] = useState('')
  const sharePoint = useSharePointStatus()
  usePageTitle('Run')
  const startRun = useStartSharePointRun()
  const uploadRun = useUploadRun()
  const navigate = useNavigate()

  // SharePoint first when this server has it (decision 83); otherwise only this computer.
  const sharePointOn = sharePoint.data?.configured ?? false
  const sharePointReady = sharePointOn && sharePoint.data?.connected === true && !sharePoint.data.needs_reconnect
  const source: Source = sharePointOn ? picked : 'computer'
  const last = trail.at(-1)
  const current = last?.kind === 'folder' ? last : undefined
  // A whole library or site is too much to run: a folder inside it is chosen.
  const tooBig = current !== undefined && current.itemId === 'root'
  const listing = useSharePointFolder(current?.driveId, current?.itemId)
  const typical = useTypicalTime().data?.typical_ms ?? null
  // Inside SharePoint only this folder's own files are counted: sub-folders are counted by the run.
  const sharePointFiles = listing.data && listing.data.folders.length === 0 ? listing.data.invoice_files : undefined
  const files = source === 'sharepoint' ? sharePointFiles : choice?.invoices.length
  const estimate = files && typical ? files * typical : null
  const ready = source === 'sharepoint' ? sharePointReady && current !== undefined && !tooBig : choice !== undefined
  const chosenName = choice ? choice.folder || `${choice.invoices.length} chosen files` : ''
  const trailName = trail.map((step) => step.name).join(' / ')

  function adopt(chosen: Client) {
    const saved = lastTrail(chosen.client_id, isStep) ?? []
    setClient(chosen)
    setTrail(saved)
    if (source === 'sharepoint') setDate(dateOf(saved))
  }

  function pickClient(chosen: Client) {
    adopt(chosen)
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

  function pickSource(chosen: Source) {
    setPicked(chosen)
    setDate(chosen === 'sharepoint' ? dateOf(trail) : (choiceDate ?? ''))
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

  function goTo(next: Step[]) {
    setTrail(next)
    setDate(dateOf(next))
    startRun.reset()
    if (client) rememberTrail(client.client_id, next)
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
    if (!client || !current || tooBig) return
    startRun.mutate(
      { client_id: client.client_id, drive_id: current.driveId, item_id: current.itemId, folder_date: date || null },
      { onSuccess: (accepted) => void navigate(`/run/${accepted.run_id}`) },
    )
  }

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        title="Run"
        description="Pick a client, choose the documents (a folder on SharePoint, or one on your computer), confirm the date, and start processing. Sub-folders are included."
      />

      {returned.connected && (
        <Alert>
          <CircleCheck className="text-success" aria-hidden />
          <AlertTitle>Connected to {returned.system ?? 'the accounting system'}</AlertTitle>
          <AlertDescription>Organisation: {returned.connected}</AlertDescription>
        </Alert>
      )}
      {returned.sharePoint && (
        <Alert>
          <CircleCheck className="text-success" aria-hidden />
          <AlertTitle>Connected to SharePoint</AlertTitle>
          <AlertDescription>As {returned.sharePoint}. Read only: nothing in SharePoint is changed.</AlertDescription>
        </Alert>
      )}
      {returned.sharePointError && (
        <Alert variant="destructive">
          <AlertTitle>SharePoint not connected</AlertTitle>
          <AlertDescription>{returned.sharePointError}</AlertDescription>
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
                : 'Open the folder on SharePoint, or paste its link. Everything inside it, sub-folders included, is run. SharePoint is only read: nothing there is changed.'}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {sharePointOn && <SourcePicker value={source} onPick={pickSource} />}
            {blocked ? (
              <p className="text-sm text-muted-foreground">
                First get the client's accounting system ready (above): then choose the documents.
              </p>
            ) : source === 'computer' ? (
              <ComputerPicker choice={choice} onChoose={choose} />
            ) : sharePointReady ? (
              <SharePointPicker trail={trail} onTrail={goTo} />
            ) : (
              <SharePointConnection back="run" showConnected={false} />
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
              {source === 'sharepoint'
                ? dateHint(date, current?.date ?? null, current?.path ?? '')
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

      {client && !blocked && source === 'sharepoint' && sharePointReady && current && (
        <div className="space-y-3">
          {startRun.isError && <ErrorAlert title="The run did not start" error={startRun.error} />}
          <RunSummary client={client.name} folder={trailName} date={date} files={files} estimate={estimate} />
          {tooBig && (
            <p className="text-sm text-muted-foreground">
              Open a folder inside {current.name} first: the whole library is too much to run.
            </p>
          )}
          {!tooBig && listing.data && listing.data.invoice_files === 0 && listing.data.folders.length === 0 && (
            <p className="text-sm text-muted-foreground">No invoice files in this folder.</p>
          )}
          <Button
            size="lg"
            onClick={run}
            disabled={startRun.isPending || !ready || (sharePointFiles === 0)}
          >
            <Play aria-hidden />
            {startRun.isPending ? 'Starting…' : `Run ${client.name}: ${current.name}`}
          </Button>
          <p className="text-xs text-muted-foreground">
            The files are copied from SharePoint when the run starts; SharePoint itself is not changed.
          </p>
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
    ['sharepoint', 'On SharePoint'],
    ['computer', 'On my computer'],
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

/** The date the open SharePoint folder's name stands for; '' for none. */
function dateOf(trail: Step[]): string {
  const last = trail.at(-1)
  return last?.kind === 'folder' ? (last.date ?? '') : ''
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
