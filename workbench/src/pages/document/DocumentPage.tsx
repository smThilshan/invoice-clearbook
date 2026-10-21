import { ArrowLeft } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useBlocker, useNavigate, useParams } from 'react-router'

import { type DocumentDetail, type DocumentStatus } from '@/api/client'
import { useCodingOptions, useDocument, useUpdateDocument } from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { QUEUE_LABELS, statusLabel, TYPE_LABELS } from '@/lib/labels'

import { toInvoiceInput } from './formModel'
import { PageViewer } from './PageViewer'
import { RejectDialog } from './RejectDialog'
import { ReviewForm, type ReviewSubmit } from './ReviewForm'

/** Documents the accountant may still change (the backend enforces the same). */
const EDITABLE: ReadonlySet<DocumentStatus> = new Set(['IN_REVIEW', 'NON_POSTABLE'])

/** One document: its pages on the left, the review form on the right (task 4.5). */
export function DocumentPage() {
  const { documentId = '' } = useParams()
  const document = useDocument(documentId)
  const options = useCodingOptions(document.data?.client_id)
  const save = useUpdateDocument()
  const navigate = useNavigate()
  const [dirty, setDirty] = useState(false)
  const onDirtyChange = useCallback((next: boolean) => setDirty(next), [])
  useUnsavedChangesGuard(dirty)

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
      {save.isSuccess && !save.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          Saved and checked again
          {doc.queue ? `: now ${QUEUE_LABELS[doc.queue]}` : ''}.
        </p>
      )}
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="lg:sticky lg:top-4 lg:h-[calc(100vh-8rem)]">
          <PageViewer documentId={doc.document_id} pageCount={doc.page_count ?? 0} />
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
          actions={doc.status === 'IN_REVIEW' && <RejectDialog documentId={doc.document_id} />}
        />
      </div>
    </div>
  )
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
