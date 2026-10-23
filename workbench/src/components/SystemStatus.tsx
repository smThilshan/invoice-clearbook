import { useReadiness } from '@/api/queries'
import type { ModelStatus, Readiness } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'

type Check = 'ok' | 'unavailable'

const MODEL_LABELS: Record<ModelStatus, string> = {
  ok: 'Ready',
  unreachable: 'Not reachable',
  missing: 'Not installed',
  incompatible: 'Wrong model (needs Instruct)',
}

interface Item {
  name: string
  ok: boolean
  label: string
}

function items(readiness: Readiness): Item[] {
  const check = (name: string, value: Check): Item => ({
    name,
    ok: value === 'ok',
    label: value === 'ok' ? 'Ready' : 'Unavailable',
  })
  return [
    check('Database', readiness.database),
    check('Job queue', readiness.redis),
    { name: 'AI model', ok: readiness.model === 'ok', label: MODEL_LABELS[readiness.model] },
  ]
}

/** Is everything the Workbench needs running? (backed by GET /health/ready) `compact`: one
 * green dot when all is well (task 4.18); the full list still shows as soon as something is down. */
export function SystemStatus({ compact = false }: { compact?: boolean }) {
  const { data, isPending, isError } = useReadiness()

  if (isPending) {
    return <Skeleton className="h-6 w-64" aria-label="Checking system status" />
  }
  if (isError) {
    return (
      <Badge variant="destructive" role="status">
        API not reachable — is <code>make api</code> running?
      </Badge>
    )
  }
  const all = items(data)
  if (compact && all.every((item) => item.ok)) {
    return (
      <span title={all.map((i) => `${i.name}: ${i.label}`).join(' · ')}
        className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <span className="size-2 rounded-full bg-success" aria-hidden />
        All systems ready
      </span>
    )
  }
  return (
    <ul className="flex flex-wrap items-center gap-2" aria-label="System status">
      {all.map((item) => (
        <li key={item.name}>
          <Badge variant={item.ok ? 'secondary' : 'destructive'}>
            {item.name}: {item.label}
          </Badge>
        </li>
      ))}
    </ul>
  )
}
