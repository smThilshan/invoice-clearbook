import type { SharePointFolder, SharePointListing } from '@/api/client'

/** One step of the way from the list of sites to the open folder. A library is its root
 * folder ('root'); a pasted link starts the way at its own folder. */
export type Step =
  | { kind: 'site'; name: string; siteId: string }
  | { kind: 'folder'; name: string; driveId: string; itemId: string; path: string; date: string | null }

export function isStep(step: unknown): step is Step {
  if (typeof step !== 'object' || step === null) return false
  const s = step as Record<string, unknown>
  if (s.kind === 'site') return typeof s.name === 'string' && typeof s.siteId === 'string'
  return (
    s.kind === 'folder' &&
    typeof s.name === 'string' &&
    typeof s.driveId === 'string' &&
    typeof s.itemId === 'string' &&
    typeof s.path === 'string'
  )
}

export function folderStep(folder: SharePointFolder): Step {
  return {
    kind: 'folder',
    name: folder.name,
    driveId: folder.drive_id,
    itemId: folder.item_id,
    path: folder.path,
    date: folder.date ?? null,
  }
}

/** What is in the folder, said plainly: a run takes its invoices and everything in its sub-folders. */
export function contentText(listing: SharePointListing): string {
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`
  const subs = listing.folders.length
  const left = listing.other_files > 0 ? ` ${plural(listing.other_files, 'other file')} left out.` : ''
  if (subs === 0) {
    return listing.invoice_files === 0
      ? `No invoice files (PDF or photo) in this folder.${left}`
      : `${plural(listing.invoice_files, 'invoice file')} (PDF or photo) in this folder.${left}`
  }
  return `${plural(listing.invoice_files, 'invoice file')} here and ${plural(subs, 'sub-folder')}: a run takes everything inside.${left}`
}
