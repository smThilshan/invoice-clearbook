import { ChevronLeft, ChevronRight, LoaderCircle, Search } from 'lucide-react'
import { type ReactNode, useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router'

import type { DocumentSummary, QueueCategory, QueueQuery, RunOption } from '@/api/client'
import { useClients, useQueue, useRuns } from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { PageHeader } from '@/components/PageHeader'
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
import { lastClient } from '@/lib/remember'
import { useDebounced } from '@/lib/useDebounced'
import type { ReviewContext } from '@/lib/reviewList'
import { usePageTitle } from '@/lib/usePageTitle'
import { cn } from '@/lib/utils'

export const PAGE_SIZE = 50

const CATEGORIES: (QueueCategory | undefined)[] = [undefined, 'EXCEPTION', 'CHECK', 'READY']
const LISTS = [
  { status: 'IN_REVIEW', label: 'In review' },
  { status: 'NON_POSTABLE', label: 'Parked (not postable)' },
] as const
type ListStatus = (typeof LISTS)[number]['status']

const CATEGORY_STYLES: Record<QueueCategory, string> = {
  EXCEPTION: 'bg-destructive/10 text-destructive',
  CHECK: 'bg-warning-soft text-warning',
  READY: 'bg-success-soft text-success',
}

/**
 * Documents to review for one client: Exception first, then Check, then Ready (task 4.4).
 * Every filter lives in the address, so refresh, back and the link from a run keep them.
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
  const status: ListStatus = params.get('status') === 'NON_POSTABLE' ? 'NON_POSTABLE' : 'IN_REVIEW'
  const runId = params.get('run') ?? undefined
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
        search: search || undefined,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }
    : undefined
  const queue = useQueue(query)
  const counts = queue.data?.counts

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

  return (
    <div className="space-y-5">
      {flash && (
        <p role="status" aria-label="Done" className="text-sm font-medium text-success">
          {flash}
        </p>
      )}
      <PageHeader
        title="Review queue"
        description="Exception first, then Check, then Ready; oldest first inside each."
      />

      <div className="flex flex-wrap items-center gap-3">
        <ClientSelect
          value={clientId}
          onChange={(c) => update({ client: c.client_id, run: undefined })}
        />
        <Toggle label="Category">
          {CATEGORIES.map((c) => (
            <ToggleButton key={c ?? 'ALL'} pressed={category === c} onClick={() => update({ queue: c })}>
              {c ? QUEUE_LABELS[c] : 'All'}
              {counts && (
                <span className="ml-1.5 tabular-nums text-muted-foreground">
                  {c ? (counts[c] ?? 0) : Object.values(counts).reduce((a, b) => a + (b ?? 0), 0)}
                </span>
              )}
            </ToggleButton>
          ))}
        </Toggle>
        <Toggle label="List">
          {LISTS.map((list) => (
            <ToggleButton
              key={list.status}
              pressed={status === list.status}
              onClick={() =>
                update({ status: list.status === 'IN_REVIEW' ? undefined : list.status })
              }
            >
              {list.label}
            </ToggleButton>
          ))}
        </Toggle>
        {clientId && (
          <RunFilter clientId={clientId} runId={runId} onChange={(run) => update({ run })} />
        )}
        {clientId && <SearchBox value={search} onChange={(q) => update({ q: q || undefined })} />}
      </div>

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

      {!clientId ? (
        <p className="text-sm text-muted-foreground">Choose a client to see its documents.</p>
      ) : queue.isPending ? (
        <Skeleton className="h-48 w-full" />
      ) : queue.isError ? (
        <ErrorAlert title="The queue could not be loaded" error={queue.error} />
      ) : queue.data.total === 0 ? (
        <EmptyQueue
          filtered={Boolean(category || search || runId)}
          parked={status === 'NON_POSTABLE'}
          onClear={() => update({ queue: undefined, q: undefined, run: undefined })}
        />
      ) : (
        <>
          <DocumentTable documents={queue.data.documents} />
          <Pager
            page={page}
            total={queue.data.total}
            onPage={(next) => update({ page: next === 1 ? undefined : String(next) })}
          />
        </>
      )}
    </div>
  )
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
function EmptyQueue({ filtered, parked, onClear }: { filtered: boolean; parked: boolean; onClear: () => void }) {
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
      <p className="font-medium">{parked ? 'No parked documents.' : 'Nothing to review.'}</p>
      {!parked && (
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

const ALL_RUNS = 'all'

/** Which run's documents to show: all, or one of the latest runs (task 4.11). */
function RunFilter({
  clientId,
  runId,
  onChange,
}: {
  clientId: string
  runId: string | undefined
  onChange: (runId: string | undefined) => void
}) {
  const runs = useRuns(clientId)
  const options = runs.data ?? []
  // A run from a link that is no longer among the latest still shows as chosen.
  const known = runId === undefined || options.some((run) => run.run_id === runId)
  return (
    <Select
      value={runId ?? ALL_RUNS}
      onValueChange={(value) => onChange(value === ALL_RUNS ? undefined : value)}
    >
      <SelectTrigger aria-label="Run" className="w-72 max-w-full">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL_RUNS}>All runs</SelectItem>
        {!known && runId && <SelectItem value={runId}>One run (from a link)</SelectItem>}
        {options.map((run) => (
          <SelectItem key={run.run_id} value={run.run_id}>
            {runLabel(run)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function runLabel(run: RunOption): string {
  const folder = run.folder || 'All folders'
  const count = `${run.documents} document${run.documents === 1 ? '' : 's'}`
  return `${folder} · ${count} · ${formatDateTime(run.created_at)}`
}

/** Fits the page: fixed column widths, long text cut with … (full text on hover); the type,
 * folder and file sit under the supplier. Narrow screens drop the number column. */
function DocumentTable({ documents }: { documents: DocumentSummary[] }) {
  const navigate = useNavigate()
  const location = useLocation()
  // Review opens with this list, for Back, previous / next and Approve & next (task 4.15).
  const state: ReviewContext = {
    from: location.pathname + location.search,
    ids: documents.map((d) => d.document_id),
  }
  return (
    <div className="rounded-lg border">
      <Table aria-label="Documents" className="table-fixed">
        <TableHeader>
          <TableRow>
            <TableHead className="w-28">Category</TableHead>
            <TableHead>Supplier</TableHead>
            <TableHead className="hidden w-40 lg:table-cell">Number</TableHead>
            <TableHead className="w-28">Date</TableHead>
            <TableHead className="w-32 text-right">Total</TableHead>
            <TableHead className="w-36">Issues</TableHead>
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
                className="cursor-pointer"
                onClick={() => void navigate(`/documents/${doc.document_id}`, { state })}
              >
                <TableCell>
                  {doc.queue && (
                    <Badge className={CATEGORY_STYLES[doc.queue]}>{QUEUE_LABELS[doc.queue]}</Badge>
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
                  <IssueCounts errors={doc.errors} warnings={doc.warnings} />
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
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
