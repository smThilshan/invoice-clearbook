import { useEffect } from 'react'

const APP = 'Clearbook'

/** The browser tab's title for this page, e.g. "(3/7) Run · ROAST – Clearbook". */
export function usePageTitle(title: string | undefined): void {
  useEffect(() => {
    document.title = title ? `${title} – ${APP}` : APP
    return () => {
      document.title = APP
    }
  }, [title])
}
