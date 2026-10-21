import { ChevronRight, Folder as FolderIcon, Play } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'

import type { Folder, Restaurant } from '@/api/client'
import { useFolders, useStartRun } from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { RestaurantSelect } from '@/components/RestaurantSelect'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { formatDate } from '@/lib/labels'

/** One step of the path from the restaurant's folder down to the selected one. */
interface Crumb {
  name: string
  path: string // '' = the restaurant's folder itself
  date: string | null // the date the folder name stands for
}

/** Pick a restaurant and a folder, confirm the date, start the run (task 4.3). */
export function RunPage() {
  const [restaurant, setRestaurant] = useState<Restaurant>()
  const [crumbs, setCrumbs] = useState<Crumb[]>([])
  const [date, setDate] = useState('')
  const startRun = useStartRun()
  const navigate = useNavigate()

  const current = crumbs.at(-1)
  const folderDate = current?.date ?? null

  function pickRestaurant(picked: Restaurant) {
    setRestaurant(picked)
    setCrumbs([{ name: picked.name, path: '', date: null }])
    setDate('')
    startRun.reset()
  }

  function goTo(trail: Crumb[]) {
    setCrumbs(trail)
    setDate(trail.at(-1)?.date ?? '')
    startRun.reset()
  }

  function run() {
    if (!restaurant || !current) return
    startRun.mutate(
      { client_id: restaurant.client_id, folder: current.path, folder_date: date || null },
      { onSuccess: (accepted) => void navigate(`/run/${accepted.run_id}`) },
    )
  }

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h2 className="text-xl font-semibold">Run</h2>
        <p className="text-sm text-muted-foreground">
          Pick a restaurant and a folder, confirm the date, and start processing. Sub-folders are
          included.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>1. Restaurant</CardTitle>
        </CardHeader>
        <CardContent>
          <RestaurantSelect value={restaurant?.client_id} onChange={pickRestaurant} />
        </CardContent>
      </Card>

      {restaurant && current && (
        <Card>
          <CardHeader>
            <CardTitle>2. Folder</CardTitle>
            <CardDescription>
              Open the folder to process. Everything inside it, sub-folders included, is run.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Breadcrumbs crumbs={crumbs} onGoTo={goTo} />
            <FolderList
              clientId={restaurant.client_id}
              path={current.path}
              onOpen={(folder) =>
                goTo([...crumbs, { name: folder.name, path: folder.path, date: folder.date }])
              }
            />
          </CardContent>
        </Card>
      )}

      {restaurant && current && (
        <Card>
          <CardHeader>
            <CardTitle>3. Date</CardTitle>
            <CardDescription>The date the documents in this folder belong to.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <Label htmlFor="folder-date">Folder date</Label>
            <Input
              id="folder-date"
              type="date"
              className="w-48"
              value={date}
              onChange={(event) => setDate(event.target.value)}
            />
            <p className="text-sm text-muted-foreground" data-testid="date-hint">
              {dateHint(date, folderDate)}
            </p>
          </CardContent>
        </Card>
      )}

      {restaurant && current && (
        <div className="space-y-3">
          {startRun.isError && <ErrorAlert title="The run did not start" error={startRun.error} />}
          <Button size="lg" onClick={run} disabled={startRun.isPending}>
            <Play aria-hidden />
            {startRun.isPending ? 'Starting…' : `Run ${crumbs.map((c) => c.name).join(' / ')}`}
          </Button>
        </div>
      )}
    </div>
  )
}

function Breadcrumbs({ crumbs, onGoTo }: { crumbs: Crumb[]; onGoTo: (trail: Crumb[]) => void }) {
  return (
    <nav aria-label="Folder path" className="flex flex-wrap items-center gap-1 text-sm">
      {crumbs.map((crumb, index) => {
        const last = index === crumbs.length - 1
        return (
          <span key={crumb.path} className="flex items-center gap-1">
            {index > 0 && <ChevronRight className="size-3.5 text-muted-foreground" aria-hidden />}
            {last ? (
              <span className="font-medium" aria-current="location">
                {crumb.name}
              </span>
            ) : (
              <button
                type="button"
                className="text-muted-foreground underline-offset-4 hover:underline"
                onClick={() => onGoTo(crumbs.slice(0, index + 1))}
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

function FolderList({
  clientId,
  path,
  onOpen,
}: {
  clientId: string
  path: string
  onOpen: (folder: Folder) => void
}) {
  const folders = useFolders(clientId, path)

  if (folders.isPending) return <Skeleton className="h-24 w-full" />
  if (folders.isError) {
    return <ErrorAlert title="This folder could not be opened" error={folders.error} />
  }
  if (folders.data.folders.length === 0) {
    return <p className="text-sm text-muted-foreground">No sub-folders here.</p>
  }
  return (
    <ul aria-label="Sub-folders" className="divide-y rounded-lg border">
      {folders.data.folders.map((folder) => (
        <li key={folder.path}>
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted"
            onClick={() => onOpen(folder)}
          >
            <FolderIcon className="size-4 text-muted-foreground" aria-hidden />
            <span className="flex-1">{folder.name}</span>
            {folder.date && (
              <span className="text-xs text-muted-foreground">{formatDate(folder.date)}</span>
            )}
          </button>
        </li>
      ))}
    </ul>
  )
}

function dateHint(date: string, folderDate: string | null): string {
  if (folderDate && date === folderDate) return 'Taken from the folder name.'
  if (date === '' && folderDate) return 'No date: the period check is skipped for this run.'
  if (date === '') {
    return (
      'The folder name has no date. Enter the date the documents belong to, or leave it ' +
      'empty: the period check is then skipped.'
    )
  }
  return 'Entered by you.'
}
