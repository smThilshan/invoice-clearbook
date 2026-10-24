import { Building2, FileSearch, FolderInput, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { type ReactNode, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router'

import { SystemStatus } from '@/components/SystemStatus'
import { ThemeSwitch } from '@/components/ThemeSwitch'
import { cn } from '@/lib/utils'

const NAV = [
  { to: '/run', label: 'Run', icon: FolderInput, also: [] },
  // Reviewing a document is part of the review queue (task 4.15).
  { to: '/queue', label: 'Review queue', icon: FileSearch, also: ['/documents/'] },
  { to: '/clients', label: 'Clients', icon: Building2, also: [] },
]

const SIDEBAR_KEY = 'clearbook.sidebar' // "open" / "closed": the user's own choice

function savedSidebar(): 'open' | 'closed' | null {
  try {
    const value = localStorage.getItem(SIDEBAR_KEY)
    return value === 'open' || value === 'closed' ? value : null
  } catch {
    return null
  }
}

function NavItem({
  to,
  also,
  label,
  folded,
  children,
}: {
  to: string
  also: string[]
  label: string
  folded: boolean
  children: ReactNode
}) {
  const { pathname } = useLocation()
  const inside = also.some((prefix) => pathname.startsWith(prefix))
  return (
    <NavLink
      to={to}
      title={folded ? label : undefined}
      aria-label={label}
      className={({ isActive }) =>
        cn(
          'flex shrink-0 items-center gap-2 rounded-md px-3 py-2 text-sm font-medium sm:shrink',
          folded && 'sm:justify-center sm:px-2',
          isActive || inside ? 'bg-secondary text-secondary-foreground' : 'text-muted-foreground hover:bg-muted',
        )
      }
    >
      {children}
    </NavLink>
  )
}

/** Header with system status, side navigation, and the current page (tasks 4.1, 4.18).
 * The review screen needs its width for the page and the form: there the sidebar folds to
 * icons and the header is compact, unless the user chose otherwise (remembered per computer). */
export function AppLayout() {
  const { pathname } = useLocation()
  const reviewing = pathname.startsWith('/documents/')
  const [choice, setChoice] = useState(savedSidebar)
  const folded = choice ? choice === 'closed' : reviewing

  function toggle() {
    const next = folded ? 'open' : 'closed'
    setChoice(next)
    try {
      localStorage.setItem(SIDEBAR_KEY, next)
    } catch {
      // not remembered this time
    }
  }

  return (
    <div className="flex h-screen h-dvh flex-col overflow-hidden bg-muted/30 text-foreground">
      <header
        className={cn(
          'flex flex-wrap items-center justify-between gap-4 border-b bg-background px-6',
          reviewing ? 'py-2' : 'py-3',
        )}
      >
        <div className="flex items-center gap-3">
          <div
            className="grid size-8 place-items-center rounded-lg bg-primary text-sm font-bold text-primary-foreground"
            aria-hidden
          >
            C
          </div>
          <div className="flex items-baseline gap-3">
            <h1 className="text-lg font-semibold tracking-tight">Clearbook</h1>
            {!reviewing && <p className="text-xs text-muted-foreground">Missan invoice workbench</p>}
          </div>
        </div>
        <div className="flex items-center gap-3">
          <ThemeSwitch />
        </div>
      </header>
      <SystemStatus />
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden sm:flex-row">
        <nav
          aria-label="Main"
          className={cn(
            'flex w-full shrink-0 gap-1 overflow-x-auto border-b bg-background p-2 sm:flex-col sm:space-y-1 sm:overflow-y-auto sm:border-r sm:border-b-0 sm:transition-[width]',
            folded ? 'sm:w-14' : 'sm:w-52 sm:p-3',
          )}
        >
          {NAV.map(({ to, label, icon: Icon, also }) => (
            <NavItem key={to} to={to} also={also} label={label} folded={folded}>
              <Icon className="size-4 shrink-0" aria-hidden />
              <span className={cn(folded && 'sm:hidden')}>{label}</span>
            </NavItem>
          ))}
          <button
            type="button"
            onClick={toggle}
            aria-label={folded ? 'Expand sidebar' : 'Collapse sidebar'}
            title={folded ? 'Expand sidebar' : 'Collapse sidebar'}
            className={cn(
              'mt-4 hidden w-full items-center rounded-md py-2 text-muted-foreground hover:bg-muted sm:flex',
              folded ? 'justify-center px-2' : 'gap-2 px-3 text-xs',
            )}
          >
            {folded ? <PanelLeftOpen className="size-4" aria-hidden /> : <PanelLeftClose className="size-4" aria-hidden />}
            {!folded && 'Collapse'}
          </button>
        </nav>
        {/* min-w-0: a wide table scrolls inside its own box instead of widening the page */}
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto p-4 sm:px-10 sm:py-6 lg:px-12">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
