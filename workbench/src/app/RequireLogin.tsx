import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'

import { useMe } from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { SystemStatus } from '@/components/SystemStatus'

/** Every page but the login page needs a logged-in user (decision 71); the login page brings
 * the reader back to where they were. */
export function RequireLogin({ children }: { children: ReactNode }) {
  const me = useMe()
  const { pathname, search } = useLocation()
  if (me.isPending) return null
  if (me.isError) {
    // Not a "log in" answer: something is down (the plain-words banner says what) or not set
    // up, e.g. JWT_SECRET (the reason below). Saying why beats a login loop.
    return (
      <>
        <SystemStatus />
        <div className="p-6">
          <ErrorAlert title="The Workbench cannot check who is logged in" error={me.error} />
        </div>
      </>
    )
  }
  if (!me.data) return <Navigate to="/login" replace state={{ from: pathname + search }} />
  return children
}
