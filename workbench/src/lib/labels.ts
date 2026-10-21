/** Words the accountants see for backend codes. One place, so every screen says the same. */
import type { DocumentStatus, DocumentType, QueueCategory } from '@/api/client'

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

export const QUEUE_LABELS: Record<QueueCategory, string> = {
  EXCEPTION: 'Exception',
  CHECK: 'Check',
  READY: 'Ready',
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
  return `${day} ${MONTHS[month - 1] ?? ''} ${year}`
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** A server timestamp in this computer's time: 21 Oct 2026 14:50. */
export function formatDateTime(iso: string): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  const time = `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`
  return `${at.getDate()} ${MONTHS[at.getMonth()] ?? ''} ${at.getFullYear()} ${time}`
}

/**
 * "1050.5" -> "1,050.5". Works on the text the server sent: money is never turned into a
 * JavaScript number, so nothing is rounded (rule 1).
 */
export function formatAmount(amount: string): string {
  const match = /^(-?)(\d+)(\.\d+)?$/.exec(amount)
  if (!match) return amount
  const [, sign = '', whole = '', fraction = ''] = match
  return `${sign}${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}${fraction}`
}
