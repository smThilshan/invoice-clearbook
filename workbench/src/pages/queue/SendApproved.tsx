import { Send, Undo2 } from 'lucide-react'
import { type ReactNode, useState } from 'react'

import type { DocumentSummary, SendBody } from '@/api/client'
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

/** What one press of Send would send, asked before it goes. */
interface Asked {
  body: SendBody
  title: string
  details: ReactNode
}

/** Decision 98: the Approved list's way out, after a last look at how many and how much: the
 * ticked documents, or all of them (as many as were seen waiting, or nothing is sent). */
export function SendBar({
  clientId,
  waiting,
  chosen,
  system,
  onSent,
}: {
  clientId: string
  waiting: number // approved documents of this company waiting for Send, every page
  chosen: DocumentSummary[]
  system: string
  onSent: () => void
}) {
  const [asked, setAsked] = useState<Asked | null>(null)
  const send = useSendApproved()
  return (
    <div role="region" aria-label="Send" className="flex flex-wrap items-center gap-3 rounded-lg border bg-background px-3 py-2 text-sm shadow-sm">
      <span className="text-muted-foreground">
        {waiting === 0
          ? `Nothing waiting: approved documents wait here until you send them to ${system}.`
          : chosen.length === 0
            ? 'Send one from its row, tick several, or send all.'
            : `${plural(chosen.length, 'document')} ticked.`}
      </span>
      {chosen.length > 0 && (
        <Button size="sm" variant="outline" onClick={() => setAsked(askChosen(chosen, system))}>
          <Send aria-hidden />
          Send ticked ({chosen.length})
        </Button>
      )}
      {waiting > 0 && (
        <Button
          size="sm"
          onClick={() =>
            setAsked({
              body: { all_waiting: waiting },
              title: `Send all ${plural(waiting, 'approved document')} to ${system}?`,
              details: `Every approved document of this company, on every page. If someone approves another one meanwhile, nothing is sent and you are asked to check again.`,
            })
          }
        >
          <Send aria-hidden />
          Send all ({waiting})
        </Button>
      )}
      {send.data && (
        <span role="status" className="font-medium text-success">
          {plural(send.data.sent, 'document')} on the way to {system}.
          {send.data.not_waiting > 0 && ` ${send.data.not_waiting} had already gone or were undone.`}
        </span>
      )}
      <ConfirmSend
        asked={asked}
        system={system}
        sending={send.isPending}
        error={send.error}
        onCancel={() => {
          setAsked(null)
          send.reset()
        }}
        onSend={(body) =>
          send.mutate(
            { clientId, body },
            {
              onSuccess: () => {
                setAsked(null)
                onSent()
              },
            },
          )
        }
      />
    </div>
  )
}

/** Decision 98: one approved document, from its row: Send it, or take the approval back. */
export function SendOrUndo({ document, system }: { document: DocumentSummary; system: string }) {
  const [asked, setAsked] = useState<Asked | null>(null)
  const send = useSendApproved()
  const undo = useUndoApproval()
  return (
    // Clicks here are not the row's: the row opens the document.
    <span className="flex flex-wrap items-center gap-1.5" onClick={(event) => event.stopPropagation()}>
      <Button type="button" size="sm" className="h-7" onClick={() => setAsked(askChosen([document], system))}>
        <Send aria-hidden />
        Send
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-7"
        disabled={undo.isPending}
        onClick={() => undo.mutate({ id: document.document_id, body: null })}
      >
        <Undo2 aria-hidden />
        Undo approval
      </Button>
      {undo.isError && <span className="text-xs text-destructive">{undo.error.message}</span>}
      <ConfirmSend
        asked={asked}
        system={system}
        sending={send.isPending}
        error={send.error}
        onCancel={() => {
          setAsked(null)
          send.reset()
        }}
        onSend={(body) =>
          send.mutate({ clientId: document.client_id, body }, { onSuccess: () => setAsked(null) })
        }
      />
    </span>
  )
}

function ConfirmSend({
  asked,
  system,
  sending,
  error,
  onCancel,
  onSend,
}: {
  asked: Asked | null
  system: string
  sending: boolean
  error: Error | null
  onCancel: () => void
  onSend: (body: SendBody) => void
}) {
  return (
    <Dialog open={asked !== null} onOpenChange={(open) => !open && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{asked?.title}</DialogTitle>
          <DialogDescription>
            {asked?.details} After sending, a change is made in {system}, not here.
          </DialogDescription>
        </DialogHeader>
        {error && <ErrorAlert title="Not sent" error={error} />}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button type="button" disabled={sending || !asked} onClick={() => asked && onSend(asked.body)}>
            <Send aria-hidden />
            {sending ? 'Sending…' : 'Send'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** The question for these documents: how many, their total, and whether some are final at once. */
function askChosen(chosen: DocumentSummary[], system: string): Asked {
  const expenses = chosen.filter((d) => d.sending?.as_expense).length
  return {
    body: { document_ids: chosen.map((d) => d.document_id) },
    title: `Send ${plural(chosen.length, 'document')} to ${system}?`,
    details: (
      <>
        Total {totals(chosen)}.
        {expenses > 0 && (
          <span className="mt-1 block font-medium text-warning">
            {expenses === chosen.length ? (chosen.length === 1 ? 'It is an expense' : 'They are expenses') : `${plural(expenses, 'of them is', 'of them are')} expenses`}:
            final in {system} at once, with no draft.
          </span>
        )}
      </>
    ),
  }
}

/** The documents' totals per currency, counted in exact units of 1/10000 (never float). */
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
