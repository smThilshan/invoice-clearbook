import { ChevronRight, FileText, Folder as FolderIcon, Globe, Library, Link2 } from 'lucide-react'
import { type FormEvent, useState } from 'react'

import type { SharePointFolder } from '@/api/client'
import {
  useSharePointFolder,
  useSharePointLibraries,
  useSharePointLink,
  useSharePointSites,
} from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { formatDate } from '@/lib/labels'
import { contentText, folderStep, type Step } from '@/lib/sharePoint'

/** Choose a folder on SharePoint (decision 83): paste its link, or open sites, libraries and
 * folders. Read live each time: what is shown is what is there now. Nothing here can change
 * SharePoint. */
export function SharePointPicker({ trail, onTrail }: { trail: Step[]; onTrail: (trail: Step[]) => void }) {
  const last = trail.at(-1)
  return (
    <div className="space-y-3">
      <PasteLink onOpen={(folder) => onTrail([folderStep(folder)])} />
      <Breadcrumbs trail={trail} onTrail={onTrail} />
      {last === undefined ? (
        <SiteList onOpen={(step) => onTrail([step])} />
      ) : last.kind === 'site' ? (
        <LibraryList siteId={last.siteId} onOpen={(step) => onTrail([...trail, step])} />
      ) : (
        <FolderContents step={last} onOpen={(step) => onTrail([...trail, step])} />
      )}
    </div>
  )
}

function PasteLink({ onOpen }: { onOpen: (folder: SharePointFolder) => void }) {
  const [url, setUrl] = useState('')
  const link = useSharePointLink()

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!url.trim()) return
    link.mutate(url.trim(), {
      onSuccess: (folder) => {
        setUrl('')
        onOpen(folder)
      },
    })
  }

  return (
    <form onSubmit={submit} aria-label="Open a SharePoint link" className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Input
          aria-label="SharePoint folder link"
          className="min-w-0 flex-1 basis-72"
          placeholder="Paste a SharePoint folder link"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
        />
        <Button type="submit" variant="outline" disabled={link.isPending || !url.trim()}>
          <Link2 aria-hidden />
          {link.isPending ? 'Opening…' : 'Open'}
        </Button>
      </div>
      {link.isError && <ErrorAlert title="This link could not be opened" error={link.error} />}
      <p className="text-xs text-muted-foreground">Or open the folder below.</p>
    </form>
  )
}

function Breadcrumbs({ trail, onTrail }: { trail: Step[]; onTrail: (trail: Step[]) => void }) {
  const crumbs = [{ name: 'All sites', key: 'sites' }, ...trail.map((step, i) => ({ name: step.name, key: `${i}` }))]
  return (
    <nav aria-label="Folder path" className="flex flex-wrap items-center gap-1 text-sm">
      {crumbs.map((crumb, index) => {
        const current = index === crumbs.length - 1
        return (
          <span key={crumb.key} className="flex items-center gap-1">
            {index > 0 && <ChevronRight className="size-3.5 text-muted-foreground" aria-hidden />}
            {current ? (
              <span className="font-medium" aria-current="location">
                {crumb.name}
              </span>
            ) : (
              <button
                type="button"
                className="text-muted-foreground underline-offset-4 hover:underline"
                onClick={() => onTrail(trail.slice(0, index))}
              >
                {crumb.name}
              </button>
            )}
          </span>
        )
      })}
    </nav>
  )
}

function SiteList({ onOpen }: { onOpen: (step: Step) => void }) {
  const sites = useSharePointSites(true)
  if (sites.isPending) return <Skeleton className="h-24 w-full" />
  if (sites.isError) return <ErrorAlert title="SharePoint sites could not be loaded" error={sites.error} />
  if (sites.data.length === 0) return <p className="text-sm text-muted-foreground">No SharePoint sites can be opened.</p>
  return (
    <Choices label="Sites">
      {sites.data.map((site) => (
        <Choice key={site.site_id} icon={Globe} name={site.name}
          onClick={() => onOpen({ kind: 'site', name: site.name, siteId: site.site_id })} />
      ))}
    </Choices>
  )
}

function LibraryList({ siteId, onOpen }: { siteId: string; onOpen: (step: Step) => void }) {
  const libraries = useSharePointLibraries(siteId)
  if (libraries.isPending) return <Skeleton className="h-16 w-full" />
  if (libraries.isError) return <ErrorAlert title="This site could not be opened" error={libraries.error} />
  if (libraries.data.length === 0) return <p className="text-sm text-muted-foreground">No document libraries here.</p>
  return (
    <Choices label="Libraries">
      {libraries.data.map((library) => (
        <Choice
          key={library.drive_id}
          icon={Library}
          name={library.name}
          onClick={() =>
            onOpen({ kind: 'folder', name: library.name, driveId: library.drive_id, itemId: 'root', path: '', date: null })
          }
        />
      ))}
    </Choices>
  )
}

function FolderContents({ step, onOpen }: { step: Extract<Step, { kind: 'folder' }>; onOpen: (step: Step) => void }) {
  const listing = useSharePointFolder(step.driveId, step.itemId)
  if (listing.isPending) return <Skeleton className="h-24 w-full" />
  if (listing.isError) return <ErrorAlert title="This folder could not be opened" error={listing.error} />
  return (
    <div className="space-y-2">
      <p className="flex items-center gap-1.5 text-sm text-muted-foreground" data-testid="file-count">
        <FileText className="size-4" aria-hidden />
        {contentText(listing.data)}
      </p>
      {listing.data.folders.length > 0 && (
        <Choices label="Sub-folders">
          {listing.data.folders.map((folder) => (
            <Choice
              key={folder.item_id}
              icon={FolderIcon}
              name={folder.name}
              detail={folder.items === null || folder.items === undefined ? '' : `${folder.items} item${folder.items === 1 ? '' : 's'}`}
              date={folder.date ?? null}
              onClick={() => onOpen(folderStep(folder))}
            />
          ))}
        </Choices>
      )}
    </div>
  )
}

function Choices({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <ul aria-label={label} className="max-h-80 divide-y overflow-y-auto rounded-lg border">
      {children}
    </ul>
  )
}

function Choice({
  icon: Icon,
  name,
  detail = '',
  date = null,
  onClick,
}: {
  icon: typeof FolderIcon
  name: string
  detail?: string
  date?: string | null
  onClick: () => void
}) {
  return (
    <li>
      <button type="button" className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted" onClick={onClick}>
        <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <span className="flex-1 truncate">{name}</span>
        {detail && <span className="text-xs tabular-nums text-muted-foreground">{detail}</span>}
        {date && <span className="w-24 text-right text-xs text-muted-foreground">{formatDate(date)}</span>}
      </button>
    </li>
  )
}
