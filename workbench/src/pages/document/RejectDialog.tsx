import { Ban } from 'lucide-react'
import { useState } from 'react'

import type { RejectReason } from '@/api/client'
import { useRejectDocument } from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'

/** The backend's pick-list (services/review.py), in words; a fixed list so rejections can be
 * counted and learned from. */
const REASONS: { value: RejectReason; label: string }[] = [
  { value: 'DUPLICATE', label: 'Duplicate: already in, or already posted' },
  { value: 'ALREADY_RECORDED', label: 'Already entered in the accounting system by hand' },
  { value: 'WRONG_RESTAURANT', label: 'Belongs to another restaurant' },
  { value: 'UNREADABLE', label: 'Unreadable or incomplete' },
  { value: 'OTHER', label: 'Other (needs a note)' },
]
const NOTE_MAX = 500

export function RejectDialog({ documentId }: { documentId: string }) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState<RejectReason>()
  const [note, setNote] = useState('')
  const reject = useRejectDocument()
  const needsNote = reason === 'OTHER' && note.trim() === ''

  function submit() {
    if (!reason || needsNote) return
    reject.mutate(
      { id: documentId, body: { reason, note: note.trim() || null } },
      { onSuccess: () => setOpen(false) },
    )
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) reject.reset()
      }}
    >
      <DialogTrigger asChild>
        <Button type="button" variant="destructive">
          <Ban aria-hidden />
          Reject…
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reject this document</DialogTitle>
          <DialogDescription>
            It will not be posted. Its number is freed, so a corrected copy can come in later.
          </DialogDescription>
        </DialogHeader>
        <fieldset className="space-y-2">
          <legend className="mb-1 text-sm font-medium">Reason</legend>
          {REASONS.map((option) => (
            <label key={option.value} className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="reject-reason"
                value={option.value}
                checked={reason === option.value}
                onChange={() => setReason(option.value)}
              />
              {option.label}
            </label>
          ))}
        </fieldset>
        <div className="space-y-1.5">
          <Label htmlFor="reject-note">Note{reason === 'OTHER' ? '' : ' (optional)'}</Label>
          <Textarea
            id="reject-note"
            maxLength={NOTE_MAX}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        </div>
        {reject.isError && <ErrorAlert title="Not rejected" error={reject.error} />}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={!reason || needsNote || reject.isPending}
            onClick={submit}
          >
            {reject.isPending ? 'Rejecting…' : 'Reject document'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
