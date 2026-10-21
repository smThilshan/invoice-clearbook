import { Link } from 'react-router'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

function Placeholder({ title, description }: { title: string; description: string }) {
  return (
    <Card className="max-w-xl">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="text-sm text-muted-foreground">Coming next in Phase 4.</CardContent>
    </Card>
  )
}

export function QueuePage() {
  return (
    <Placeholder
      title="Review queue"
      description="Documents to review, Exception first, then Check, then Ready (task 4.4)."
    />
  )
}

export function NotFoundPage() {
  return (
    <div className="space-y-2">
      <h2 className="text-lg font-semibold">Page not found</h2>
      <Link to="/run" className="text-sm underline">
        Back to Run
      </Link>
    </div>
  )
}
