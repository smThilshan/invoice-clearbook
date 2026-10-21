import { Navigate, type RouteObject } from 'react-router'

import { AppLayout } from '@/app/AppLayout'
import { NotFoundPage, QueuePage } from '@/pages/placeholders'
import { RunPage } from '@/pages/run/RunPage'
import { RunProgressPage } from '@/pages/run/RunProgressPage'

export const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <Navigate to="/run" replace /> },
      { path: 'run', element: <RunPage /> },
      { path: 'run/:runId', element: <RunProgressPage /> },
      { path: 'queue', element: <QueuePage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]
