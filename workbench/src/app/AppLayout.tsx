import { FileSearch, FolderInput } from 'lucide-react'
import type { ReactNode } from 'react'
import { NavLink, Outlet } from 'react-router'

import { SystemStatus } from '@/components/SystemStatus'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

const NAV = [
  { to: '/run', label: 'Run', icon: FolderInput },
  { to: '/queue', label: 'Review queue', icon: FileSearch },
]

function NavItem({ to, children }: { to: string; children: ReactNode }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          'flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium',
          isActive ? 'bg-secondary text-secondary-foreground' : 'text-muted-foreground hover:bg-muted',
        )
      }
    >
      {children}
    </NavLink>
  )
}

/** Header with system status, side navigation, and the current page. */
export function AppLayout() {
  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <header className="flex flex-wrap items-center justify-between gap-4 px-6 py-3">
        <div>
          <h1 className="text-lg font-semibold">Clearbook</h1>
          <p className="text-xs text-muted-foreground">Missan invoice workbench</p>
        </div>
        <SystemStatus />
      </header>
      <Separator />
      <div className="flex flex-1">
        <nav aria-label="Main" className="w-52 shrink-0 space-y-1 p-3">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavItem key={to} to={to}>
              <Icon className="size-4" aria-hidden />
              {label}
            </NavItem>
          ))}
        </nav>
        <Separator orientation="vertical" />
        <main className="flex-1 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
