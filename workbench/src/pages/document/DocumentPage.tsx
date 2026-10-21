import { ArrowLeft } from 'lucide-react'
import { useNavigate, useParams } from 'react-router'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

/** One document under review. The viewer and form come in task 4.5. */
export function DocumentPage() {
  const { documentId = '' } = useParams()
  const navigate = useNavigate()
  return (
    <Card className="max-w-xl">
      <CardHeader>
        <CardTitle>Review document</CardTitle>
        <CardDescription>
          The page viewer and the form come next (task 4.5). Document {documentId}.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Button variant="outline" onClick={() => void navigate(-1)}>
          <ArrowLeft aria-hidden />
          Back to the queue
        </Button>
      </CardContent>
    </Card>
  )
}
