import { useEffect } from 'react'

import { APP_NAME } from '@/lib/appName'

/** The browser tab's title for this page, e.g. "(3/7) Run · ROAST – Missan Invoice Scanner". */
export function usePageTitle(title: string | undefined): void {
  useEffect(() => {
    document.title = title ? `${title} – ${APP_NAME}` : APP_NAME
    return () => {
      document.title = APP_NAME
    }
  }, [title])
}
