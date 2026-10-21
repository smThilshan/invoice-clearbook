/** Words the accountants see for backend codes. One place, so every screen says the same. */
import type { DocumentStatus, DocumentType } from '@/api/client'

/** QUEUED: found by the folder scan, not taken in yet (run progress only). */
export type FileStatusCode = DocumentStatus | 'QUEUED'

export const STATUS_LABELS: Record<FileStatusCode, string> = {
  QUEUED: 'Waiting',
  CAPTURED: 'Taken in',
  READ: 'Read',
  EXTRACTED: 'Extracted',
  VERIFIED: 'Checked',
  IN_REVIEW: 'In review',
  APPROVED: 'Approved',
  POSTING: 'Posting',
  POSTED: 'Posted',
  SYNCED: 'Posted',
  REJECTED: 'Rejected',
  POST_ERROR: 'Posting failed',
  VOIDED: 'Voided',
  DUPLICATE: 'Duplicate file',
  NON_POSTABLE: 'Parked (not postable)',
  FAILED: 'Failed',
}

export const TYPE_LABELS: Record<DocumentType, string> = {
  BILL: 'Bill',
  CREDIT_NOTE: 'Credit note',
  DEBIT_NOTE: 'Debit note',
  RECEIPT: 'Receipt',
  SALES_INVOICE: 'Sales invoice',
  NON_POSTABLE: 'Not postable',
}

/** Still moving through read, extract and check. */
export const WORKING: ReadonlySet<string> = new Set([
  'QUEUED',
  'CAPTURED',
  'READ',
  'EXTRACTED',
  'VERIFIED',
])

export function statusLabel(status: string): string {
  return STATUS_LABELS[status as FileStatusCode] ?? status
}

/** 2026-10-21 -> 21 Oct 2026, shown the same everywhere (no locale guessing). */
export function formatDate(iso: string): string {
  const [year, month, day] = iso.split('-').map(Number)
  if (!year || !month || !day) return iso
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return `${day} ${months[month - 1] ?? ''} ${year}`
}
