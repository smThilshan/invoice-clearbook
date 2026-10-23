import { Monitor, Moon, Sun } from 'lucide-react'
import { useState } from 'react'

import { chooseTheme, savedTheme, type Theme } from '@/lib/theme'
import { cn } from '@/lib/utils'

const OPTIONS: { theme: Theme; label: string; icon: typeof Sun }[] = [
  { theme: 'light', label: 'Light', icon: Sun },
  { theme: 'dark', label: 'Dark', icon: Moon },
  { theme: 'system', label: 'Match this computer', icon: Monitor },
]

/** Light / dark / match the computer, in the header. */
export function ThemeSwitch() {
  const [theme, setTheme] = useState(savedTheme)
  return (
    <div role="group" aria-label="Theme" className="flex rounded-md border p-0.5">
      {OPTIONS.map(({ theme: value, label, icon: Icon }) => (
        <button
          key={value}
          type="button"
          aria-label={label}
          title={label}
          aria-pressed={theme === value}
          onClick={() => {
            chooseTheme(value)
            setTheme(value)
          }}
          className={cn(
            'rounded px-1.5 py-1',
            theme === value ? 'bg-secondary text-secondary-foreground' : 'text-muted-foreground hover:bg-muted',
          )}
        >
          <Icon className="size-3.5" aria-hidden />
        </button>
      ))}
    </div>
  )
}
