import type { ChosenFile } from '@/api/client'

/** Files chosen on this computer for a run (decision 78): the invoices to upload, the files left
 * out, and the folder that was chosen. The server checks all of it again. */
export interface Choice {
  folder: string // the chosen folder's name; '' when files were chosen one by one
  invoices: ChosenFile[]
  skipped: string[] // not invoice files: not uploaded
  bytes: number // of the invoices
}

const INVOICE = /\.(pdf|png|jpe?g)$/i
// The server's limits for one upload (MAX_UPLOAD_FILES, MAX_UPLOAD_MB), said before uploading.
export const MAX_UPLOAD_FILES = 1000
export const MAX_UPLOAD_BYTES = 1024 * 1024 * 1024

export function chooseFiles(files: Iterable<File>): Choice {
  const invoices: ChosenFile[] = []
  const skipped: string[] = []
  let bytes = 0
  for (const file of files) {
    const path = file.webkitRelativePath || file.name
    // Hidden and temporary files (.DS_Store, ~$book.xlsx) are left out without a word.
    if (path.split('/').some((part) => part.startsWith('.') || part.startsWith('~$'))) continue
    if (INVOICE.test(path)) {
      invoices.push({ path, file })
      bytes += file.size
    } else {
      skipped.push(path)
    }
  }
  const tops = new Set(invoices.map((chosen) => (chosen.path.includes('/') ? chosen.path.split('/')[0] : '')))
  return { folder: tops.size === 1 ? ([...tops][0] ?? '') : '', invoices, skipped, bytes }
}

/** Why the choice cannot be uploaded in one go, or null. */
export function tooMuch(choice: Choice): string | null {
  if (choice.invoices.length > MAX_UPLOAD_FILES) {
    return `At most ${MAX_UPLOAD_FILES} files can be uploaded at a time: choose a smaller folder.`
  }
  if (choice.bytes > MAX_UPLOAD_BYTES) {
    return `At most ${formatBytes(MAX_UPLOAD_BYTES)} can be uploaded at a time: choose a smaller folder.`
  }
  return null
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}
