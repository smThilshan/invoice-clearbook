import { ChevronLeft, ChevronRight, Maximize2, ZoomIn, ZoomOut } from 'lucide-react'
import { useState } from 'react'

import { pageImageUrl } from '@/api/client'
import { Button } from '@/components/ui/button'

const ZOOM_STEP = 25
const ZOOM_MIN = 50
const ZOOM_MAX = 300

/**
 * The document's pages as the pictures the Read step made (PDFs, scans and photos alike).
 * Zoom is a share of the panel width: 100% = fit width.
 */
export function PageViewer({ documentId, pageCount }: { documentId: string; pageCount: number }) {
  const [page, setPage] = useState(1)
  const [zoom, setZoom] = useState(100)

  if (pageCount < 1) {
    return <p className="text-sm text-muted-foreground">No page pictures for this document.</p>
  }

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
      <div className="min-h-0 flex-1 overflow-auto rounded-lg border bg-muted/40">
        <img
          src={pageImageUrl(documentId, page)}
          alt={`Page ${page} of the document`}
          style={{ width: `${zoom}%` }}
          className="max-w-none"
        />
      </div>
    </div>
  )
}
