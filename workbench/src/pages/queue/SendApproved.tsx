import { Send, Undo2 } from 'lucide-react'
import { useState } from 'react'

import type { DocumentSummary } from '@/api/client'
import { useSendApproved, useUndoApproval } from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { formatAmount } from '@/lib/labels'

/** Decision 98: the chosen approved documents go to the accounting system, after a last look
 * at how many and how much. Exactly the ones chosen are sent, never "everything". */
export function SendBar({
  clientId,
  chosen,
  system,
  onSent,
}: {
  clientId: string
  chosen: DocumentSummary[]
  system: string
  onSent: () => void
}) {
  const [open, setOpen] = useState(false)
  const send = useSendApproved()
  const expenses = chosen.filter((d) => d.sending?.as_expense).length
  return (
    <div role="region" aria-label="Send" className="flex flex-wrap items-center gap-3 rounded-lg border bg-background px-3 py-2 text-sm shadow-sm">
      <span className="text-muted-foreground">
        {chosen.length === 0 ? 'Tick the documents to send.' : `${plural(chosen.length, 'document')} chosen.`}
      </span>
      <Button size="sm" disabled={chosen.length === 0} onClick={() => setOpen(true)}>
        <Send aria-hidden />
        Send to {system}
      </Button>
      {send.data && (
        <span role="status" className="font-medium text-success">
          {plural(send.data.sent, 'document')} on the way to {system}.
          {send.data.not_waiting > 0 && ` ${send.data.not_waiting} had already gone or were undone.`}
        </span>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Send {plural(chosen.length, 'document')} to {system}?
            </DialogTitle>
            <DialogDescription>
              Total {totals(chosen)}. After sending, a change is made in {system}, not here.
            </DialogDescription>
          </DialogHeader>
          {expenses > 0 && (
            <p className="text-sm font-medium text-warning">
              {expenses === chosen.length ? 'They are' : `${plural(expenses, 'of them is', 'of them are')}`} expenses:
              final in {system} at once, with no draft.
            </p>
          )}
          {send.isError && <ErrorAlert title="Not sent" error={send.error} />}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              disabled={send.isPending}
              onClick={() =>
                send.mutate(
                  { clientId, documentIds: chosen.map((d) => d.document_id) },
                  {
                    onSuccess: () => {
                      setOpen(false)
                      onSent()
                    },
                  },
                )
              }
            >
              <Send aria-hidden />
              {send.isPending ? 'Sending…' : 'Send'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

/** Decision 98: back to review while it still waits for Send. */
export function UndoApproval({ documentId }: { documentId: string }) {
  const undo = useUndoApproval()
  return (
    <span className="flex flex-col items-start gap-1">
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-7"
        disabled={undo.isPending}
        onClick={(event) => {
          event.stopPropagation() // not the row: it opens the document
          undo.mutate({ id: documentId, body: null })
        }}
      >
        <Undo2 aria-hidden />
        Undo approval
      </Button>
      {undo.isError && <span className="text-xs text-destructive">{undo.error.message}</span>}
    </span>
  )
}

/** The chosen documents' totals per currency, counted in exact units of 1/10000 (never float). */
function totals(documents: DocumentSummary[]): string {
  const sums = new Map<string, bigint>()
  for (const doc of documents) {
    if (doc.total === null) continue
    const currency = doc.currency ?? ''
    sums.set(currency, (sums.get(currency) ?? 0n) + toUnits(doc.total))
  }
  if (sums.size === 0) return 'not known'
  return [...sums].map(([currency, units]) => `${currency} ${formatAmount(fromUnits(units))}`.trim()).join(' + ')
}

const SCALE = 4

function toUnits(amount: string): bigint {
  const negative = amount.startsWith('-')
  const [whole = '0', fraction = ''] = amount.replace('-', '').split('.')
  const units = BigInt(whole) * 10n ** BigInt(SCALE) + BigInt(fraction.padEnd(SCALE, '0').slice(0, SCALE) || '0')
  return negative ? -units : units
}

function fromUnits(units: bigint): string {
  const negative = units < 0n
  const digits = (negative ? -units : units).toString().padStart(SCALE + 1, '0')
  const text = `${digits.slice(0, -SCALE)}.${digits.slice(-SCALE)}`.replace(/0{1,2}$/, '')
  return negative ? `-${text}` : text
}

function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`
}
