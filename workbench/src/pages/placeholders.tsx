import { Link } from 'react-router'

import { PageContainer, PageHeader } from '@/components/PageHeader'
import { usePageTitle } from '@/lib/usePageTitle'

export function NotFoundPage() {
  usePageTitle('Page not found')
  return (
    <PageContainer className="space-y-4">
      <PageHeader title="Page not found" description="This address does not lead to a page of the Workbench." />
      <div className="flex gap-3 text-sm">
        <Link to="/run" className="font-medium underline-offset-4 hover:underline">
          Start a run
        </Link>
        <Link to="/queue" className="font-medium underline-offset-4 hover:underline">
          Review queue
        </Link>
      </div>
    </PageContainer>
  )
}
