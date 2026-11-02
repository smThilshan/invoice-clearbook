import { MutationCache, QueryCache, QueryClient, type QueryClientConfig } from '@tanstack/react-query'

import { ApiError } from './client'
import { queryKeys } from './queries'

/**
 * The app's query cache. Any answer of 401 means the session ended (expired, logged out
 * elsewhere, user deactivated): "who is logged in" becomes nobody, and the login guard sends
 * the reader to the login page, back here after logging in (decision 71).
 */
export function makeQueryClient(config: QueryClientConfig = {}): QueryClient {
  const loggedOut = (error: unknown) => {
    if (error instanceof ApiError && error.status === 401) client.setQueryData(queryKeys.me, null)
  }
  const client: QueryClient = new QueryClient({
    ...config,
    queryCache: new QueryCache({ onError: loggedOut }),
    mutationCache: new MutationCache({ onError: loggedOut }),
  })
  return client
}
