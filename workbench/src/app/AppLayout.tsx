import { Building2, FileSearch, FolderInput, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { type ReactNode, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router'

import { SystemStatus } from '@/components/SystemStatus'
import { ThemeSwitch } from '@/components/ThemeSwitch'
import { Separator } from '@/components/ui/separator'
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
          'flex items-center gap-2 rounded-md py-2 text-sm font-medium',
          folded ? 'justify-center px-2' : 'px-3',
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
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <header className={cn('flex flex-wrap items-center justify-between gap-4 px-6', reviewing ? 'py-2' : 'py-3')}>
        <div className="flex items-baseline gap-3">
          <h1 className="text-lg font-semibold tracking-tight">Clearbook</h1>
          {!reviewing && <p className="text-xs text-muted-foreground">Missan invoice workbench</p>}
        </div>
        <div className="flex items-center gap-3">
          <SystemStatus compact={reviewing} />
          <ThemeSwitch />
        </div>
      </header>
      <Separator />
      <div className="flex flex-1">
        <nav aria-label="Main" className={cn('shrink-0 space-y-1 p-2 transition-[width]', folded ? 'w-14' : 'w-52 p-3')}>
          {NAV.map(({ to, label, icon: Icon, also }) => (
            <NavItem key={to} to={to} also={also} label={label} folded={folded}>
              <Icon className="size-4 shrink-0" aria-hidden />
              {!folded && label}
            </NavItem>
          ))}
          <button
            type="button"
            onClick={toggle}
            aria-label={folded ? 'Expand sidebar' : 'Collapse sidebar'}
            title={folded ? 'Expand sidebar' : 'Collapse sidebar'}
            className={cn('mt-4 flex w-full items-center rounded-md py-2 text-muted-foreground hover:bg-muted', folded ? 'justify-center px-2' : 'gap-2 px-3 text-xs')}
          >
            {folded ? <PanelLeftOpen className="size-4" aria-hidden /> : <PanelLeftClose className="size-4" aria-hidden />}
            {!folded && 'Collapse'}
          </button>
        </nav>
        <Separator orientation="vertical" />
        {/* min-w-0: a wide table scrolls inside its own box instead of widening the page */}
        <main className={cn('min-w-0 flex-1', reviewing ? 'p-4' : 'p-6')}>
          <Outlet />
        </main>
      </div>
    </div>
  )
}
