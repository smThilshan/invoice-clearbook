import type { Restaurant } from '@/api/client'
import { useRestaurants } from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { rememberRestaurant } from '@/lib/remember'

/** The restaurant dropdown used on every screen; remembers the last pick on this computer. */
export function RestaurantSelect({
  value,
  onChange,
}: {
  value: string | undefined
  onChange: (restaurant: Restaurant) => void
}) {
  const restaurants = useRestaurants()

  if (restaurants.isPending) return <Skeleton className="h-8 w-72" />
  if (restaurants.isError) {
    return <ErrorAlert title="Restaurants could not be loaded" error={restaurants.error} />
  }
  if (restaurants.data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No restaurants yet: add one with <code>make add-client</code>.
      </p>
    )
  }

  function pick(clientId: string) {
    const picked = restaurants.data?.find((r) => r.client_id === clientId)
    if (!picked) return
    rememberRestaurant(picked.client_id)
    onChange(picked)
  }

  return (
    <Select value={value ?? ''} onValueChange={pick}>
      <SelectTrigger className="w-72" aria-label="Restaurant">
        <SelectValue placeholder="Choose a restaurant" />
      </SelectTrigger>
      <SelectContent>
        {restaurants.data.map((r) => (
          <SelectItem key={r.client_id} value={r.client_id}>
            {r.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
