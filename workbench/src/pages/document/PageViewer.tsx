import { ChevronLeft, ChevronRight, Maximize2, ZoomIn, ZoomOut } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { pageImageUrl, type Place } from '@/api/client'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const ZOOM_STEP = 25
const ZOOM_MIN = 50
const ZOOM_MAX = 300
const BOX_PADDING = 0.004 // a little air around the printed words (fraction of the page)

/** What the form is looking at: a field and where its value is printed (task 4.6). */
export interface Highlight {
  label: string
  state: 'searching' | 'found' | 'not-found'
  places: Place[]
}

/**
 * The document's pages as the pictures the Read step made (PDFs, scans and photos alike).
 * Zoom is a share of the panel width: 100% = fit width. Highlight boxes are fractions of the
 * page, so they stay on the words at any zoom.
 */
export function PageViewer({
  documentId,
  pageCount,
  highlight,
}: {
  documentId: string
  pageCount: number
  highlight?: Highlight | null
}) {
  const [page, setPage] = useState(1)
  const [zoom, setZoom] = useState(100)
  const firstBox = useRef<HTMLDivElement>(null)
  const places = highlight?.places ?? []
  const first = places[0]

  // A new highlight shows the page of its first place and scrolls it into view.
  const firstKey = first ? `${first.page}:${first.box.join(',')}` : ''
  useEffect(() => {
    if (!first) return
    setPage(first.page)
    requestAnimationFrame(() =>
      firstBox.current?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' }),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run only when the place changes
  }, [firstKey])

  if (pageCount < 1) {
    return <p className="text-sm text-muted-foreground">No page pictures for this document.</p>
  }

  const onPage = places.filter((place) => place.page === page)

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          <Button variant="outline" size="icon-sm" aria-label="Previous page" disabled={page <= 1}
            onClick={() => setPage(page - 1)}>
            <ChevronLeft />
          </Button>
          <span className="min-w-24 text-center text-sm" aria-live="polite">
            Page {page} of {pageCount}
          </span>
          <Button variant="outline" size="icon-sm" aria-label="Next page" disabled={page >= pageCount}
            onClick={() => setPage(page + 1)}>
            <ChevronRight />
          </Button>
        </div>
        <div className="flex items-center gap-1">
          <Button variant="outline" size="icon-sm" aria-label="Zoom out" disabled={zoom <= ZOOM_MIN}
            onClick={() => setZoom(zoom - ZOOM_STEP)}>
            <ZoomOut />
          </Button>
          <span className="w-12 text-center text-sm tabular-nums">{zoom}%</span>
          <Button variant="outline" size="icon-sm" aria-label="Zoom in" disabled={zoom >= ZOOM_MAX}
            onClick={() => setZoom(zoom + ZOOM_STEP)}>
            <ZoomIn />
          </Button>
          <Button variant="outline" size="icon-sm" aria-label="Fit width" onClick={() => setZoom(100)}>
            <Maximize2 />
          </Button>
        </div>
      </div>
      {highlight && <HighlightStatus highlight={highlight} page={page} onPage={setPage} />}
      <div className="min-h-0 flex-1 overflow-auto rounded-lg border bg-muted/40">
        <div className="relative" style={{ width: `${zoom}%` }}>
          <img
            src={pageImageUrl(documentId, page)}
            alt={`Page ${page} of the document`}
            className="block w-full"
          />
          {onPage.map((place, index) => (
            <div
              key={place.box.join(',')}
              ref={place === first ? firstBox : undefined}
              data-testid="highlight"
              data-exact={place.exact}
              aria-hidden
              className={cn(
                'pointer-events-none absolute rounded-sm border-2 border-warning/40 bg-warning',
                !place.exact && 'border-dashed bg-warning',
                index === 0 && 'ring-2 ring-warning/50',
              )}
              style={boxStyle(place.box)}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

function HighlightStatus({
  highlight,
  page,
  onPage,
}: {
  highlight: Highlight
  page: number
  onPage: (page: number) => void
}) {
  const pages = [...new Set(highlight.places.map((place) => place.page))]
  const elsewhere = pages.filter((p) => p !== page)
  const approximate = highlight.places.some((place) => !place.exact)
  return (
    <p role="status" className="flex flex-wrap items-center gap-x-2 text-sm">
      <span className="font-medium">{highlight.label}:</span>{' '}
      {highlight.state === 'searching' && <span className="text-muted-foreground">looking…</span>}
      {highlight.state === 'not-found' && (
        <span className="text-muted-foreground">
          not found in the page text (it may be in a logo, stamp or handwriting)
        </span>
      )}
      {highlight.state === 'found' && (
        <>
          <span className="text-muted-foreground">
            {highlight.places.length === 1 ? '1 place' : `${highlight.places.length} places`}
            {approximate && ' (dashed: most of the words, not all)'}
          </span>
          {elsewhere.map((p) => (
            <Button key={p} type="button" variant="link" size="xs" onClick={() => onPage(p)}>
              also on page {p}
            </Button>
          ))}
        </>
      )}
    </p>
  )
}

function boxStyle([x0, y0, x1, y1]: [number, number, number, number]) {
  const left = Math.max(0, x0 - BOX_PADDING)
  const top = Math.max(0, y0 - BOX_PADDING)
  return {
    left: percent(left),
    top: percent(top),
    width: percent(Math.min(1, x1 + BOX_PADDING) - left),
    height: percent(Math.min(1, y1 + BOX_PADDING) - top),
  }
}

/** 0.596 -> "59.6%" (rounded: no 59.599999999999994%). */
function percent(fraction: number): string {
  return `${Math.round(fraction * 100_000) / 1000}%`
}
