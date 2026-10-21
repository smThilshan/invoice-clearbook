import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'

/** A failed request, said plainly: what did not work, then the server's reason. */
export function ErrorAlert({ title, error }: { title: string; error: Error }) {
  return (
    <Alert variant="destructive">
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>{error.message}</AlertDescription>
    </Alert>
  )
}
