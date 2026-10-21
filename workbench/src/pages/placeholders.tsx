import { Link } from 'react-router'

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
