import { Navigate, type RouteObject } from 'react-router'

import { AppLayout } from '@/app/AppLayout'
import { NotFoundPage } from '@/pages/placeholders'

/** Each page's code is loaded when the page is first opened (task 4.15): the Workbench starts
 * with the layout only, and the review screen's viewer is not loaded until it is needed. */
export const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <Navigate to="/run" replace /> },
      {
        path: 'run',
        lazy: async () => ({ Component: (await import('@/pages/run/RunPage')).RunPage }),
      },
      {
        path: 'run/:runId',
        lazy: async () => ({
          Component: (await import('@/pages/run/RunProgressPage')).RunProgressPage,
        }),
      },
      {
        path: 'queue',
        lazy: async () => ({ Component: (await import('@/pages/queue/QueuePage')).QueuePage }),
      },
      {
        path: 'clients',
        lazy: async () => ({ Component: (await import('@/pages/clients/ClientsPage')).ClientsPage }),
      },
      {
        path: 'documents/:documentId',
        lazy: async () => ({
          Component: (await import('@/pages/document/DocumentPage')).DocumentPage,
        }),
      },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]
