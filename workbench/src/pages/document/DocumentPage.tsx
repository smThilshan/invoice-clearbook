import { ArrowLeft, CircleCheck } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useBlocker, useNavigate, useParams } from 'react-router'

import { type DocumentDetail, type DocumentStatus } from '@/api/client'
import {
  useApproveDocument,
  useCodingOptions,
  useDocument,
  useLocate,
  useUpdateDocument,
} from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { QUEUE_LABELS, statusLabel, TYPE_LABELS } from '@/lib/labels'

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

  if (document.isPending) return <Skeleton className="h-96 w-full" />
  if (document.isError) {
    return (
      <div className="max-w-xl space-y-4">
        <ErrorAlert title="This document could not be loaded" error={document.error} />
        <BackButton onBack={() => void navigate(-1)} />
      </div>
    )
  }

  const doc = document.data
  const editable = EDITABLE.has(doc.status)

  function onSave({ values, confirmed }: ReviewSubmit) {
    save.mutate({ id: doc.document_id, body: { invoice: toInvoiceInput(values), confirmed } })
  }

  return (
    <div className="flex flex-col gap-4">
      <Header document={doc} onBack={() => void navigate(-1)} />
      {!editable && (
        <p className="text-sm text-muted-foreground">
          This document is {statusLabel(doc.status).toLowerCase()}: it can no longer be changed
          here.
        </p>
      )}
      {save.isError && <ErrorAlert title="Not saved" error={save.error} />}
      {approve.isError && <ErrorAlert title="Not approved" error={approve.error} />}
      {doc.status === 'APPROVED' && (
        <p role="status" className="flex items-center gap-2 text-sm font-medium text-emerald-700 dark:text-emerald-300">
          <CircleCheck className="size-4" aria-hidden />
          Approved: waiting to be posted.
        </p>
      )}
      {save.isSuccess && !save.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          Saved and checked again
          {doc.queue ? `: now ${QUEUE_LABELS[doc.queue]}` : ''}.
        </p>
      )}
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="lg:sticky lg:top-4 lg:h-[calc(100vh-8rem)]">
          <PageViewer
            documentId={doc.document_id}
            pageCount={doc.page_count ?? 0}
            highlight={highlightFor(target, located.data?.places, located.isFetching)}
          />
        </div>
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
                  onApprove={() => approve.mutate({ id: doc.document_id, body: null })}
                />
                <RejectDialog documentId={doc.document_id} />
              </>
            )
          }
        />
      </div>
    </div>
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

function Header({ document: doc, onBack }: { document: DocumentDetail; onBack: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <BackButton onBack={onBack} />
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
    </div>
  )
}

/** Approve takes the document as last saved and checked: never with unsaved edits or errors.
 * The server checks again before approving. */
function ApproveButton({
  blockedBy,
  pending,
  onApprove,
}: {
  blockedBy: string | null
  pending: boolean
  onApprove: () => void
}) {
  return (
    <>
      <Button
        type="button"
        className="bg-emerald-600 text-white hover:bg-emerald-700"
        disabled={blockedBy !== null || pending}
        onClick={onApprove}
      >
        <CircleCheck aria-hidden />
        {pending ? 'Approving…' : 'Approve'}
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
