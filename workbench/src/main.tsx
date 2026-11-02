import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router'

import { makeQueryClient } from '@/api/queryClient'
import { routes } from '@/app/routes'
import { startTheme } from '@/lib/theme'

import './index.css'

const queryClient = makeQueryClient({
  defaultOptions: {
    queries: { staleTime: 10_000, refetchOnWindowFocus: true },
  },
})
const router = createBrowserRouter(routes)

// Light by default; dark or "match this computer" when chosen in the header.
startTheme()

const root = document.getElementById('root')
if (!root) throw new Error('index.html has no #root element')

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
