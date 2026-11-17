import { Eye, EyeOff } from 'lucide-react'
import { type ComponentProps, useState } from 'react'

import { Input } from '@/components/ui/input'

/** A password box with an eye to check what was typed. Always starts hidden; the shown state
 * lives only in this box, so nothing about the password is kept. */
export function PasswordInput(props: Omit<ComponentProps<typeof Input>, 'type'>) {
  const [shown, setShown] = useState(false)
  return (
    <div className="relative">
      <Input {...props} type={shown ? 'text' : 'password'} className="pr-9" />
      <button
        type="button" // never submits the form
        aria-label={shown ? 'Hide password' : 'Show password'}
        aria-controls={props.id}
        className="absolute inset-y-0 right-0 flex w-9 items-center justify-center rounded-r-lg text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
        onClick={() => setShown((now) => !now)}
      >
        {shown ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
      </button>
    </div>
  )
}
