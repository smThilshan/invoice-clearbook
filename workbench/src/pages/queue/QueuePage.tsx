import { ChevronLeft, ChevronRight, ExternalLink, FolderOpen, LoaderCircle, PauseCircle, Search } from 'lucide-react'
import { type ReactNode, useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router'

import type { DocumentSummary, QueueCategory, QueueQuery, RunFolder, SendingList } from '@/api/client'
import { useAccounting, useClients, useQueue, useRunFolders } from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { PageContainer, PageHeader } from '@/components/PageHeader'
import { ClientSelect } from '@/components/ClientSelect'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Checkbox } from '@/components/ui/checkbox'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  formatAmount,
  formatDate,
  formatDateTime,
  QUEUE_LABELS,
  TYPE_LABELS,
} from '@/lib/labels'
import { lastClient, lastFolder, rememberFolder } from '@/lib/remember'
import { useDebounced } from '@/lib/useDebounced'
import type { ReviewContext } from '@/lib/reviewList'
import { usePageTitle } from '@/lib/usePageTitle'
import { cn } from '@/lib/utils'

import { SendBar, UndoApproval } from './SendApproved'

export const PAGE_SIZE = 50
const NONE: ReadonlySet<string> = new Set()

// The quick ones first; how long each category's oldest has waited shows on its card.
const CATEGORIES: (QueueCategory | undefined)[] = [undefined, 'READY', 'CHECK', 'EXCEPTION']
const LISTS = [
  { status: 'IN_REVIEW', label: 'In review' },
  { status: 'NON_POSTABLE', label: 'Parked (not postable)' },
  { status: 'REJECTED', label: 'Rejected' },
] as const
type ListStatus = (typeof LISTS)[number]['status']
// Approved documents on their way to the accounting system (decision 70), and those that came
// back to review not sent (shown only when there are some).
const SENDING_LISTS: { key: SendingList; label: string }[] = [
  { key: 'not_sent', label: 'Not sent' },
  { key: 'held', label: 'Approved, not sent' }, // waiting for Send (decision 98)
  { key: 'waiting', label: 'Waiting to send' },
  { key: 'failed', label: 'Could not send' },
  { key: 'posted', label: 'Posted' },
]

const CATEGORY_STYLES: Record<QueueCategory, string> = {
  EXCEPTION: 'bg-destructive/10 text-destructive',
  CHECK: 'bg-warning-soft text-warning',
  READY: 'bg-success-soft text-success',
}

/**
 * Documents to review for one client: not sent first, then Ready, Check, Exception (task 4.4).
 * Every filter lives in the address, so refresh, back and the link from a run keep them. With
 * no folder in the address, the one chosen last for the client (after a run: its folder).
 */
export function QueuePage() {
  const [params, setParams] = useSearchParams()
  const clients = useClients()
  const flash = reviewFlash(useLocation().state)

  const remembered = lastClient()
  const clientId =
    params.get('client') ??
    (clients.data?.some((c) => c.client_id === remembered) ? remembered : undefined)
  const category = asCategory(params.get('queue'))
  const status: ListStatus = LISTS.find((l) => l.status === params.get('status'))?.status ?? 'IN_REVIEW'
  const sending = SENDING_LISTS.find((l) => l.key === params.get('sending'))?.key
  const runId = params.get('run') ?? undefined
  // '' in the address: all folders, chosen; none there: the one chosen last ('' = all too).
  const folderParam = params.get('folder')
  const folder = (runId ? '' : (folderParam ?? (clientId ? lastFolder(clientId) : undefined))) || undefined
  const search = params.get('q') ?? ''
  const page = Math.max(1, Number(params.get('page')) || 1)

  const clientName = clients.data?.find((c) => c.client_id === clientId)?.name
  usePageTitle(clientName ? `Review queue · ${clientName}` : 'Review queue')

  const query: QueueQuery | undefined = clientId
    ? {
        client_id: clientId,
        queue: category,
        status,
        run_id: runId,
        folder,
        search: search || undefined,
        sending,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }
    : undefined
  const queue = useQueue(query)
  // Decision 98: the documents ticked to Send, kept with the list they were ticked in, so a
  // new list or page starts with none.
  const listKey = JSON.stringify(query ?? null)
  const [ticked, setTicked] = useState<{ list: string; ids: Set<string> }>()
  const chosen = ticked?.list === listKey ? ticked.ids : NONE
  const setChosen = (ids: Set<string>) => setTicked({ list: listKey, ids })
  const counts = queue.data?.counts
  const sendingCounts = queue.data?.sending_counts
  const system = useAccounting(clientId ?? '', false).data?.provider_name ?? 'the accounting system'

  /** Change filters; any change other than the page goes back to page 1. */
  function update(changes: Record<string, string | undefined>) {
    const next = new URLSearchParams(params)
    if (clientId) next.set('client', clientId)
    for (const [key, value] of Object.entries(changes)) {
      if (value === undefined) next.delete(key)
      else next.set(key, value)
    }
    if (!('page' in changes)) next.delete('page')
    setParams(next)
  }

  /** One folder, or '' for all; remembered for the client. A run from a link is let go. */
  function chooseFolder(next: string) {
    if (clientId) rememberFolder(clientId, next)
    update({ folder: next, run: undefined })
  }

  return (
    <PageContainer className="space-y-6">
      {flash && (
        <p role="status" aria-label="Done" className="text-sm font-medium text-success">
          {flash}
        </p>
      )}
      <PageHeader
        title="Review queue"
        description="Not sent first, then Ready, Check, Exception; oldest first inside each."
      />

      {clientId && counts && !sending && (
        <section aria-label="Queue overview" className="grid gap-3 sm:grid-cols-3">
          {(['READY', 'CHECK', 'EXCEPTION'] as const).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => update({ queue: category === key ? undefined : key })}
              aria-pressed={category === key}
              className={cn(
                'rounded-xl border bg-background p-4 text-left shadow-sm transition-colors hover:bg-muted/50',
                category === key && 'border-primary ring-1 ring-primary/20',
              )}
            >
              <span className="text-sm text-muted-foreground">{QUEUE_LABELS[key]}</span>
              <span className="mt-1 block text-2xl font-semibold tabular-nums">{counts[key] ?? 0}</span>
              <span className="mt-1 block text-xs text-muted-foreground">
                documents to review
                {queue.data?.oldest[key] && ` · oldest ${waitingFor(queue.data.oldest[key])}`}
              </span>
            </button>
          ))}
        </section>
      )}

      <section aria-label="Queue filters" className="space-y-3 rounded-xl border bg-background p-4 shadow-sm">
        <div className="flex flex-wrap items-center gap-3">
          <ClientSelect
            value={clientId}
            onChange={(c) => update({ client: c.client_id, run: undefined, folder: undefined })}
          />
          {!sending && (
            <Toggle label="Category">
              {CATEGORIES.map((c) => (
                <ToggleButton
                  key={c ?? 'ALL'}
                  pressed={category === c}
                  onClick={() => update({ queue: c })}
                >
                  {c ? QUEUE_LABELS[c] : 'All'}
                  {counts && (
                    <span className="ml-1.5 tabular-nums text-muted-foreground">
                      {c ? (counts[c] ?? 0) : Object.values(counts).reduce((a, b) => a + (b ?? 0), 0)}
                    </span>
                  )}
                </ToggleButton>
              ))}
            </Toggle>
          )}
          <Toggle label="List">
            {LISTS.map((list) => (
              <ToggleButton
                key={list.status}
                pressed={!sending && status === list.status}
                onClick={() =>
                  update({
                    status: list.status === 'IN_REVIEW' ? undefined : list.status,
                    sending: undefined,
                  })
                }
              >
                {list.label}
              </ToggleButton>
            ))}
          </Toggle>
          <Toggle label="Sent">
            {SENDING_LISTS.map((list) => {
              const count = sendingCounts?.[list.key] ?? 0
              const onlyWhenSome = list.key === 'not_sent' || list.key === 'held'
              if (onlyWhenSome && count === 0 && sending !== list.key) return null
              const alarming = (list.key === 'failed' || list.key === 'not_sent') && count > 0
              const waiting = list.key === 'held' && count > 0
              return (
                <ToggleButton
                  key={list.key}
                  pressed={sending === list.key}
                  onClick={() => update({ sending: list.key, status: undefined, queue: undefined })}
                >
                  {list.label}
                  {sendingCounts && (
                    <span
                      className={cn(
                        'ml-1.5 tabular-nums',
                        alarming
                          ? 'font-semibold text-destructive'
                          : waiting
                            ? 'font-semibold text-warning'
                            : 'text-muted-foreground',
                      )}
                    >
                      {count}
                    </span>
                  )}
                </ToggleButton>
              )
            })}
          </Toggle>
          {clientId && (
            <FolderFilter clientId={clientId} folder={folder} runId={runId} onChange={chooseFolder} />
          )}
          {clientId && <SearchBox value={search} onChange={(q) => update({ q: q || undefined })} />}
        </div>
      </section>

      {clientId && (folder || runId) && (
        <p role="note" aria-label="Folder shown" className="flex flex-wrap items-center gap-2 text-sm">
          <FolderOpen className="size-4 text-muted-foreground" aria-hidden />
          {folder ? (
            <span>
              Showing only <span className="font-medium">{folder}</span>, sub-folders included.
            </span>
          ) : (
            <span>Showing only the documents of one run.</span>
          )}
          <Button variant="outline" size="sm" onClick={() => chooseFolder('')}>
            Show all folders
          </Button>
        </p>
      )}

      {clientId && queue.data && queue.data.processing > 0 && (
        <p role="note" aria-label="Still processing" className="flex flex-wrap items-center gap-2 rounded-lg border border-info/40 bg-info-soft px-3 py-2 text-sm">
          <LoaderCircle className="size-4 animate-spin text-info" aria-hidden />
          {queue.data.processing} document{queue.data.processing === 1 ? ' is' : 's are'} still
          being processed: {queue.data.processing === 1 ? 'it appears' : 'they appear'} here when ready.
          {runId && (
            <Link to={`/run/${runId}`} className="font-medium underline-offset-4 hover:underline">
              See progress
            </Link>
          )}
        </p>
      )}

      {clientId && queue.data && queue.data.stopped > 0 && (
        <p role="note" aria-label="Run stopped" className="flex flex-wrap items-center gap-2 rounded-lg border border-warning/40 bg-warning-soft px-3 py-2 text-sm">
          <PauseCircle className="size-4 text-warning" aria-hidden />
          {queue.data.stopped} document{queue.data.stopped === 1 ? ' was' : 's were'} not finished
          because the run was stopped.
          {queue.data.stopped_run_id && (
            <Link to={`/run/${queue.data.stopped_run_id}`} className="font-medium underline-offset-4 hover:underline">
              Open the run to resume
            </Link>
          )}
        </p>
      )}

      {!clientId ? (
        <p className="text-sm text-muted-foreground">Choose an organization or business to see its documents.</p>
      ) : queue.isPending ? (
        <Skeleton className="h-48 w-full" />
      ) : queue.isError ? (
        <ErrorAlert title="The queue could not be loaded" error={queue.error} />
      ) : queue.data.total === 0 ? (
        sending ? (
          <p className="rounded-lg border border-dashed p-6 text-center text-sm font-medium">
            {sending === 'held'
              ? `Nothing waiting for Send: every approved document went to ${system}.`
              : sending === 'failed'
              ? 'Nothing failed to send.'
              : sending === 'not_sent'
                ? `Nothing came back: every approved bill went to ${system}.`
                : sending === 'posted'
                  ? `Nothing posted to ${system} yet.`
                  : 'Nothing waiting to be sent.'}
          </p>
        ) : (
          <EmptyQueue
            filtered={Boolean(category || search || runId || folder)}
            list={status}
            onClear={() => {
              if (clientId) rememberFolder(clientId, '')
              update({ queue: undefined, q: undefined, run: undefined, folder: '' })
            }}
          />
        )
      ) : (
        <>
          {sending === 'held' && clientId && (
            <SendBar
              clientId={clientId}
              chosen={queue.data.documents.filter((d) => chosen.has(d.document_id))}
              system={system}
              onSent={() => setChosen(new Set())}
            />
          )}
          <DocumentTable
            documents={queue.data.documents}
            sendingList={Boolean(sending) && sending !== 'not_sent'} // back in review: its issues
            system={system}
            selection={sending === 'held' ? { chosen, onChange: setChosen } : undefined}
          />
          <Pager
            page={page}
            total={queue.data.total}
            onPage={(next) => update({ page: next === 1 ? undefined : String(next) })}
          />
        </>
      )}
    </PageContainer>
  )
}

/** How long ago a document was taken in, in days: "today", "1 day", "12 days". */
function waitingFor(iso: string): string {
  const days = Math.floor((Date.now() - Date.parse(iso)) / 86_400_000)
  return days < 1 ? 'today' : days === 1 ? '1 day' : `${days} days`
}

/** Search by supplier, invoice number or file name; the address follows after a short pause. */
function SearchBox({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [text, setText] = useState(value)
  const settled = useDebounced(text, 300)
  useEffect(() => {
    if (settled !== value) onChange(settled)
  }, [settled]) // eslint-disable-line react-hooks/exhaustive-deps -- only when the typing settles
  return (
    <div className="relative w-64 max-w-full">
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
      <Input
        type="search"
        aria-label="Search"
        placeholder="Supplier, number or file"
        className="pl-8"
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
    </div>
  )
}

/** Nothing in the list: say why, and what to do next. */
function EmptyQueue({ filtered, list, onClear }: { filtered: boolean; list: ListStatus; onClear: () => void }) {
  if (filtered) {
    return (
      <div className="space-y-2 rounded-lg border border-dashed p-6 text-center text-sm">
        <p className="font-medium">No documents match these filters.</p>
        <Button variant="outline" size="sm" onClick={onClear}>
          Clear filters
        </Button>
      </div>
    )
  }
  return (
    <div className="space-y-2 rounded-lg border border-dashed p-6 text-center text-sm">
      <p className="font-medium">
        {list === 'NON_POSTABLE' ? 'No parked documents.' : list === 'REJECTED' ? 'No rejected documents.' : 'Nothing to review.'}
      </p>
      {list === 'IN_REVIEW' && (
        <p className="text-muted-foreground">
          New documents appear here after a run.{' '}
          <Link to="/run" className="font-medium text-foreground underline-offset-4 hover:underline">
            Start a run
          </Link>
        </p>
      )}
    </div>
  )
}

const ALL_FOLDERS = '*' // never a folder name: SharePoint and Windows do not allow it
const ONE_RUN = 'run' // a run from a link (the address names a run, not a folder)

/** Which folder's documents to show: all, or one a run picked, sub-folders included. */
function FolderFilter({
  clientId,
  folder,
  runId,
  onChange,
}: {
  clientId: string
  folder: string | undefined
  runId: string | undefined
  onChange: (folder: string) => void
}) {
  const options = useRunFolders(clientId).data ?? []
  // A folder from a link or remembered, with nothing left in it, still shows as chosen.
  const known = folder === undefined || options.some((f) => f.folder === folder)
  return (
    <Select
      value={runId ? ONE_RUN : (folder ?? ALL_FOLDERS)}
      onValueChange={(value) => {
        if (value !== ONE_RUN) onChange(value === ALL_FOLDERS ? '' : value)
      }}
    >
      <SelectTrigger aria-label="Folder" className="w-72 max-w-full">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL_FOLDERS}>All folders</SelectItem>
        {runId && <SelectItem value={ONE_RUN}>One run (from a link)</SelectItem>}
        {!known && folder && <SelectItem value={folder}>{folder}</SelectItem>}
        {options.map((option) => (
          <SelectItem key={option.folder} value={option.folder}>
            {folderLabel(option)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function folderLabel(option: RunFolder): string {
  return `${option.folder} · ${option.in_review} in review · run ${formatDateTime(option.last_run)}`
}

/** Fits the page: fixed column widths, long text cut with … (full text on hover); the type,
 * folder and file sit under the supplier. Narrow screens drop the number column. */
function DocumentTable({
  documents,
  sendingList,
  system,
  selection,
}: {
  documents: DocumentSummary[]
  sendingList: boolean // a list of approved documents: where each is instead of its issues
  system: string
  selection?: { chosen: ReadonlySet<string>; onChange: (chosen: Set<string>) => void } // to Send (98)
}) {
  const navigate = useNavigate()
  const location = useLocation()
  // Review opens with this list, for Back, previous / next and Approve & next (task 4.15).
  const state: ReviewContext = {
    from: location.pathname + location.search,
    ids: documents.map((d) => d.document_id),
  }
  return (
    <div className="overflow-hidden rounded-xl border bg-background shadow-sm">
      <Table aria-label="Documents" className="table-fixed">
        <TableHeader>
          <TableRow className="bg-muted/50 hover:bg-muted/50">
            {selection && (
              <TableHead className="w-10">
                <Checkbox
                  aria-label="Choose every document on this page"
                  checked={documents.length > 0 && documents.every((d) => selection.chosen.has(d.document_id))}
                  onCheckedChange={(on) =>
                    selection.onChange(new Set(on === true ? documents.map((d) => d.document_id) : []))
                  }
                />
              </TableHead>
            )}
            <TableHead className="w-28">Category</TableHead>
            <TableHead>Supplier</TableHead>
            <TableHead className="hidden w-40 lg:table-cell">Number</TableHead>
            <TableHead className="w-28">Date</TableHead>
            <TableHead className="w-32 text-right">Total</TableHead>
            <TableHead className={sendingList ? 'w-56' : 'w-36'}>
              {sendingList ? 'Sending' : 'Issues'}
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {documents.map((doc) => {
            const where = [doc.folder, doc.file].filter(Boolean).join(' / ')
            const details = [doc.document_type ? TYPE_LABELS[doc.document_type] : null, where]
              .filter(Boolean)
              .join(' · ')
            return (
              <TableRow
                key={doc.document_id}
                className={cn('cursor-pointer', doc.not_sent && 'bg-destructive/5 hover:bg-destructive/10')}
                onClick={() => void navigate(`/documents/${doc.document_id}`, { state })}
              >
                {selection && (
                  <TableCell onClick={(event) => event.stopPropagation()}>
                    <Checkbox
                      aria-label={`Choose ${doc.vendor_name ?? doc.file}`}
                      checked={selection.chosen.has(doc.document_id)}
                      onCheckedChange={(on) => {
                        const next = new Set(selection.chosen)
                        if (on === true) next.add(doc.document_id)
                        else next.delete(doc.document_id)
                        selection.onChange(next)
                      }}
                    />
                  </TableCell>
                )}
                {/* Came back not sent: a red bar and badge instead of its category, so it stands out. */}
                <TableCell className={cn('border-l-4', doc.not_sent ? 'border-l-destructive' : 'border-l-transparent')}>
                  {doc.not_sent ? (
                    <Badge className="bg-destructive text-white">Not sent</Badge>
                  ) : (
                    doc.queue && <Badge className={CATEGORY_STYLES[doc.queue]}>{QUEUE_LABELS[doc.queue]}</Badge>
                  )}
                </TableCell>
                <TableCell>
                  {/* The link makes each row reachable by keyboard; the row click is a shortcut. */}
                  <Link
                    to={`/documents/${doc.document_id}`}
                    state={state}
                    title={doc.vendor_name ?? undefined}
                    className="block truncate font-medium underline-offset-4 hover:underline"
                    onClick={(event) => event.stopPropagation()}
                  >
                    {doc.vendor_name ?? <span className="text-muted-foreground">Supplier not read</span>}
                  </Link>
                  <span className="block truncate text-xs text-muted-foreground" title={details}>
                    {details}
                  </span>
                  {doc.not_sent && (
                    <span className="mt-0.5 line-clamp-2 text-xs font-medium text-destructive" title={doc.not_sent}>
                      {doc.not_sent}
                    </span>
                  )}
                </TableCell>
                <TableCell className="hidden lg:table-cell">
                  <span className="block truncate tabular-nums" title={doc.invoice_number ?? undefined}>
                    {doc.invoice_number ?? ''}
                  </span>
                </TableCell>
                <TableCell className="tabular-nums">
                  {doc.invoice_date ? formatDate(doc.invoice_date) : ''}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {doc.total !== null ? `${doc.currency ?? ''} ${formatAmount(doc.total)}`.trim() : ''}
                </TableCell>
                <TableCell>
                  {sendingList ? (
                    <SendingCell document={doc} system={system} />
                  ) : (
                    <IssueCounts errors={doc.errors} warnings={doc.warnings} />
                  )}
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

const SENDING_LABELS: Record<string, string> = {
  held: 'Waiting for Send',
  waiting: 'Waiting',
  sending: 'Sending',
  retrying: 'Trying again later',
  paused: 'Paused',
  failed: 'Could not send',
  posted: 'Posted',
}

/** Where an approved document is (decision 70): the state, why, and the posted one's link. */
function SendingCell({ document, system }: { document: DocumentSummary; system: string }) {
  const sending = document.sending
  if (!sending) return null
  const tone =
    sending.state === 'failed' || sending.state === 'paused'
      ? 'text-destructive'
      : sending.state === 'posted'
        ? 'text-success'
        : 'text-warning'
  return (
    <span className="flex flex-col text-xs leading-5">
      <span className={cn('font-medium', tone)}>
        {SENDING_LABELS[sending.state]}
        {sending.state === 'retrying' && sending.next_try_at ? ` at ${formatDateTime(sending.next_try_at)}` : ''}
      </span>
      {sending.state === 'held' && <UndoApproval documentId={document.document_id} />}
      {sending.reason && sending.state !== 'posted' && (
        <span className="truncate text-muted-foreground" title={sending.reason}>
          {sending.reason}
        </span>
      )}
      {document.external_url && (
        <a
          href={document.external_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 font-medium underline-offset-4 hover:underline"
          onClick={(event) => event.stopPropagation()}
        >
          Open in {system}
          <ExternalLink className="size-3" aria-hidden />
        </a>
      )}
    </span>
  )
}

/** Errors in red, warnings in amber, one per line so the column stays narrow. */
function IssueCounts({ errors, warnings }: { errors: number; warnings: number }) {
  if (!errors && !warnings) return <span className="text-muted-foreground">None</span>
  return (
    <span className="flex flex-col text-xs leading-5">
      {errors > 0 && (
        <span className="font-medium text-destructive">
          {errors} error{errors === 1 ? '' : 's'}
        </span>
      )}
      {warnings > 0 && (
        <span className="text-warning">
          {warnings} warning{warnings === 1 ? '' : 's'}
        </span>
      )}
    </span>
  )
}

function Pager({
  page,
  total,
  onPage,
}: {
  page: number
  total: number
  onPage: (page: number) => void
}) {
  const first = (page - 1) * PAGE_SIZE + 1
  const last = Math.min(page * PAGE_SIZE, total)
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground" aria-live="polite">
        {first > total ? `Page ${page} is past the end` : `${first}–${last} of ${total}`}
      </span>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          <ChevronLeft aria-hidden />
          Previous
        </Button>
        <Button variant="outline" size="sm" disabled={last >= total} onClick={() => onPage(page + 1)}>
          Next
          <ChevronRight aria-hidden />
        </Button>
      </div>
    </div>
  )
}

function Toggle({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div role="group" aria-label={label} className="flex rounded-lg border p-0.5">
      {children}
    </div>
  )
}

function ToggleButton({
  pressed,
  onClick,
  children,
}: {
  pressed: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={cn(
        'rounded-md px-2.5 py-1 text-sm',
        pressed ? 'bg-secondary font-medium text-secondary-foreground' : 'text-muted-foreground hover:bg-muted',
      )}
    >
      {children}
    </button>
  )
}

/** A message left by the review screen, e.g. after approving the last document of a list. */
function reviewFlash(state: unknown): string | undefined {
  const flash = (state as { flash?: unknown } | null)?.flash
  return typeof flash === 'string' ? flash : undefined
}

function asCategory(value: string | null): QueueCategory | undefined {
  return value === 'EXCEPTION' || value === 'CHECK' || value === 'READY' ? value : undefined
}
