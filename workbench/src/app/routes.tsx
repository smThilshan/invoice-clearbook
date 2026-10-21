import { Navigate, type RouteObject } from 'react-router'

import { AppLayout } from '@/app/AppLayout'
import { NotFoundPage, QueuePage, RunPage } from '@/pages/placeholders'

export const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <Navigate to="/run" replace /> },
      { path: 'run', element: <RunPage /> },
      { path: 'queue', element: <QueuePage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]
