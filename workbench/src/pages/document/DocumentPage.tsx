import { ArrowLeft, ChevronLeft, ChevronRight, CircleCheck, Undo2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useBlocker, useLocation, useNavigate, useParams } from 'react-router'

import { type DocumentDetail, type DocumentStatus } from '@/api/client'
import {
  useApproveDocument,
  useCodingOptions,
  useDocument,
  useLocate,
  useUpdateDocument,
} from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { PageContainer } from '@/components/PageHeader'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { QUEUE_LABELS, statusLabel, TYPE_LABELS } from '@/lib/labels'
import { neighbours, type ReviewContext, reviewContext } from '@/lib/reviewList'
import { formatDuration, workMs } from '@/lib/timings'
import { usePageTitle } from '@/lib/usePageTitle'

import { toInvoiceInput } from './formModel'
import { type Highlight, PageViewer } from './PageViewer'
import { RejectDialog } from './RejectDialog'
import { type FieldTarget, ReviewForm, type ReviewSubmit } from './ReviewForm'

/** Documents the accountant may still change (the backend enforces the same). */
const EDITABLE: ReadonlySet<DocumentStatus> = new Set(['IN_REVIEW', 'NON_POSTABLE'])

/** One document: its pages on the left, the review form on the right (task 4.5). */
export function DocumentPage() {
  const { documentId = '' } = useParams()
  const document = useDocument(documentId)
  const options = useCodingOptions(document.data?.client_id)
  const save = useUpdateDocument()
  const approve = useApproveDocument()
  const navigate = useNavigate()
  const [dirty, setDirty] = useState(false)
  const onDirtyChange = useCallback((next: boolean) => setDirty(next), [])
  useUnsavedChangesGuard(dirty)
  const [target, setTarget] = useState<FieldTarget | null>(null)
  const onTarget = useCallback((next: FieldTarget | null) => setTarget(next), [])
  const located = useLocate(documentId, target)
  const context = reviewContext(useLocation().state)
  const { previous, next, position, total } = neighbours(context, documentId)
  usePageTitle(document.data ? `Review · ${document.data.file}` : 'Review')

  // Back returns to the list this review was opened from; a direct link goes to the client's
  // queue instead of leaving the Workbench (task 4.15).
  const listAddress = (clientId?: string) =>
    context?.from ?? (clientId ? `/queue?client=${encodeURIComponent(clientId)}` : '/queue')
  const open = (id: string, flash?: string) =>
    void navigate(`/documents/${id}`, { state: { ...(context as ReviewContext), flash } })

  if (document.isPending) {
    return (
      <PageContainer>
        <Skeleton className="h-96 w-full" />
      </PageContainer>
    )
  }
  if (document.isError) {
    return (
      <PageContainer className="space-y-4">
        <ErrorAlert title="This document could not be loaded" error={document.error} />
        <BackButton onBack={() => void navigate(listAddress())} />
      </PageContainer>
    )
  }

  const doc = document.data
  const editable = EDITABLE.has(doc.status)

  function approveAndContinue() {
    approve.mutate(
      { id: doc.document_id, body: null },
      {
        onSuccess: () => {
          const flash = `Approved: ${doc.file}`
          if (next) open(next, flash)
          else void navigate(listAddress(doc.client_id), { state: { flash } })
        },
      },
    )
  }

  function onSave({ values, confirmed }: ReviewSubmit) {
    save.mutate({ id: doc.document_id, body: { invoice: toInvoiceInput(values), confirmed } })
  }

  return (
    <PageContainer className="flex flex-col gap-4">
      <Header
        document={doc}
        onBack={() => void navigate(listAddress(doc.client_id))}
        onPrevious={previous ? () => open(previous) : undefined}
        onNext={next ? () => open(next) : undefined}
        position={position && total ? `${position} of ${total}` : null}
      />
      {context?.flash && (
        <p role="status" aria-label="Done" className="flex items-center gap-2 text-sm font-medium text-success">
          <CircleCheck className="size-4" aria-hidden />
          {context.flash}
        </p>
      )}
      {!editable && (
        <p className="text-sm text-muted-foreground">
          This document is {statusLabel(doc.status).toLowerCase()}: it can no longer be changed
          here.
        </p>
      )}
      {save.isError && <ErrorAlert title="Not saved" error={save.error} />}
      {approve.isError && <ErrorAlert title="Not approved" error={approve.error} />}
      {doc.status === 'IN_REVIEW' && doc.failure_reason && (
        // Sent back by posting (task 5.1): the reason says what to change before approving again.
        <Alert variant="destructive">
          <Undo2 aria-hidden />
          <AlertTitle>Sent back: not posted</AlertTitle>
          <AlertDescription>{doc.failure_reason}</AlertDescription>
        </Alert>
      )}
      {doc.status === 'APPROVED' && (
        <p role="status" className="flex items-center gap-2 text-sm font-medium text-success">
          <CircleCheck className="size-4" aria-hidden />
          Approved: waiting to be posted.
        </p>
      )}
      {doc.status === 'POSTED' && (
        <p role="status" className="flex items-center gap-2 text-sm font-medium text-success">
          <CircleCheck className="size-4" aria-hidden />
          Posted to the accounting system, waiting for approval there.
        </p>
      )}
      {save.isSuccess && !save.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          Saved and checked again
          {doc.queue ? `: now ${QUEUE_LABELS[doc.queue]}` : ''}.
        </p>
      )}
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(420px,0.9fr)]">
        <section
          aria-label="Source document"
          className="flex min-h-[50vh] flex-col rounded-xl border bg-background p-3 shadow-sm xl:sticky xl:top-4 xl:h-[calc(100vh-8rem)]"
        >
          <h3 className="mb-3 px-1 text-sm font-semibold">Source document</h3>
          <PageViewer
            documentId={doc.document_id}
            pageCount={doc.page_count ?? 0}
            highlight={highlightFor(target, located.data?.places, located.isFetching)}
          />
        </section>
        {/* A new server version (after saving) starts a fresh form with its values. */}
        <ReviewForm
          key={doc.updated_at}
          document={doc}
          options={options.data}
          editable={editable}
          saving={save.isPending}
          onSave={onSave}
          onDirtyChange={onDirtyChange}
          onTarget={onTarget}
          actions={
            doc.status === 'IN_REVIEW' && (
              <>
                <ApproveButton
                  blockedBy={
                    dirty ? 'Save your changes first' : doc.errors > 0 ? 'Fix the errors first' : null
                  }
                  pending={approve.isPending}
                  label={next ? 'Approve & next' : 'Approve'}
                  onApprove={approveAndContinue}
                />
                <RejectDialog documentId={doc.document_id} />
              </>
            )
          }
        />
      </div>
    </PageContainer>
  )
}

/** What the viewer shows for the field being looked at; nothing for an empty field. */
function highlightFor(
  target: FieldTarget | null,
  places: Highlight['places'] | undefined,
  fetching: boolean,
): Highlight | null {
  if (!target || target.value.trim() === '') return null
  if (fetching || places === undefined) return { label: target.label, state: 'searching', places: [] }
  return { label: target.label, state: places.length ? 'found' : 'not-found', places }
}

function Header({
  document: doc,
  onBack,
  onPrevious,
  onNext,
  position,
}: {
  document: DocumentDetail
  onBack: () => void
  onPrevious?: () => void
  onNext?: () => void
  position: string | null
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <BackButton onBack={onBack} />
      {position && (
        <nav aria-label="Documents in this list" className="flex items-center gap-1">
          <Button variant="ghost" size="icon-sm" aria-label="Previous document" disabled={!onPrevious} onClick={onPrevious}>
            <ChevronLeft />
          </Button>
          <span className="text-sm tabular-nums text-muted-foreground">{position}</span>
          <Button variant="ghost" size="icon-sm" aria-label="Next document" disabled={!onNext} onClick={onNext}>
            <ChevronRight />
          </Button>
        </nav>
      )}
      <h2 className="text-lg font-semibold">{doc.file}</h2>
      <Badge variant="outline">{statusLabel(doc.status)}</Badge>
      {doc.queue && <Badge variant="secondary">{QUEUE_LABELS[doc.queue]}</Badge>}
      {doc.document_type && (
        <span className="text-sm text-muted-foreground">{TYPE_LABELS[doc.document_type]}</span>
      )}
      <span className="text-sm text-muted-foreground">
        {doc.errors} error{doc.errors === 1 ? '' : 's'} · {doc.warnings} warning
        {doc.warnings === 1 ? '' : 's'}
      </span>
      {doc.timings?.extract_ms != null && (
        <span className="text-sm text-muted-foreground">
          {doc.timings.total_ms !== null && `Processed in ${formatDuration(workMs(doc.timings))} · `}
          model {formatDuration(doc.timings.extract_ms)}
        </span>
      )}
    </div>
  )
}

/** Approve takes the document as last saved and checked: never with unsaved edits or errors.
 * The server checks again before approving. */
function ApproveButton({
  blockedBy,
  pending,
  label,
  onApprove,
}: {
  blockedBy: string | null
  pending: boolean
  label: string
  onApprove: () => void
}) {
  return (
    <>
      <Button
        type="button"
        className="bg-success text-background hover:bg-success/90"
        disabled={blockedBy !== null || pending}
        onClick={onApprove}
      >
        <CircleCheck aria-hidden />
        {pending ? 'Approving…' : label}
      </Button>
      {blockedBy && <span className="text-sm text-muted-foreground">{blockedBy}</span>}
    </>
  )
}

function BackButton({ onBack }: { onBack: () => void }) {
  return (
    <Button variant="outline" size="sm" onClick={onBack}>
      <ArrowLeft aria-hidden />
      Back
    </Button>
  )
}

const LEAVE_WARNING = 'You have unsaved changes. Leave without saving?'

/** Ask before leaving the page (in the app or closing the tab) with unsaved edits. */
function useUnsavedChangesGuard(dirty: boolean) {
  const blocker = useBlocker(dirty)
  useEffect(() => {
    if (blocker.state !== 'blocked') return
    if (window.confirm(LEAVE_WARNING)) blocker.proceed()
    else blocker.reset()
  }, [blocker])
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
}
