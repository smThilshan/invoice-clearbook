import { CheckIcon, ChevronDownIcon } from 'lucide-react'
import { useState } from 'react'

import type { CodingOptions } from '@/api/client'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from '@/components/ui/command'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

type Account = CodingOptions['accounts'][number]

/** The client's accounts to choose from, searched by name or code as you type, with the
 * account suggested for this supplier on top. A long chart of accounts stays one search away. */
export function AccountPicker({
  label,
  placeholder,
  value,
  onChange,
  accounts,
  suggested,
  clearLabel,
  disabled,
  invalid,
}: {
  label: string // the box's name for screen readers and tests
  placeholder: string
  value: string // '' when none is chosen
  onChange: (code: string) => void
  accounts: Account[]
  suggested?: string // the code the checks suggested for this line
  clearLabel?: string // offered to choose no account, when that is allowed
  disabled?: boolean
  invalid?: boolean
}) {
  const [open, setOpen] = useState(false)
  const chosen = accounts.find((a) => a.code === value)
  const unknown = value !== '' && !chosen // kept, never hidden: the master data no longer has it
  const top = accounts.find((a) => a.code === suggested)

  function choose(code: string) {
    onChange(code)
    setOpen(false)
  }

  const item = (account: Account, key: string) => (
    <CommandItem key={key} value={key} keywords={[account.label, account.code]} onSelect={() => choose(account.code)}>
      <span className="flex-1 truncate">{account.label}</span>
      {account.code === value && <CheckIcon aria-hidden />}
    </CommandItem>
  )

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild disabled={disabled}>
        <button
          type="button"
          role="combobox"
          aria-label={label}
          aria-expanded={open}
          aria-invalid={invalid || undefined}
          className="flex h-8 w-full items-center justify-between gap-1.5 rounded-lg border border-input bg-transparent py-2 pr-2 pl-2.5 text-left text-sm whitespace-nowrap transition-colors outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 dark:bg-input/30 dark:hover:bg-input/50"
        >
          <span className={`truncate ${value === '' ? 'text-muted-foreground' : ''}`}>
            {chosen?.label ?? (unknown ? `${value} (not in the master data)` : placeholder)}
          </span>
          <ChevronDownIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-(--radix-popover-trigger-width) min-w-64 p-0">
        <Command label="Search accounts" filter={contains}>
          <CommandInput placeholder="Search account or code…" />
          <CommandList>
            <CommandEmpty>No account matches.</CommandEmpty>
            {top && (
              <>
                <CommandGroup heading="Suggested">{item(top, `suggested ${top.code}`)}</CommandGroup>
                <CommandSeparator />
              </>
            )}
            <CommandGroup heading="All accounts">
              {clearLabel && (
                <CommandItem value="none" keywords={[clearLabel]} onSelect={() => choose('')}>
                  <span className="text-muted-foreground">{clearLabel}</span>
                </CommandItem>
              )}
              {unknown && (
                <CommandItem value={`unknown ${value}`} onSelect={() => choose(value)}>
                  <span className="flex-1 truncate">{value} (not in the master data)</span>
                  <CheckIcon aria-hidden />
                </CommandItem>
              )}
              {accounts.map((a) => item(a, a.code))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

/** Every typed word appears in the account's name or code, in any order: "sup" finds Office
 * Supplies, never a guess from scattered letters. 1 shows it, 0 hides it. */
function contains(_value: string, search: string, keywords?: string[]): number {
  const text = (keywords ?? []).join(' ').toLowerCase()
  return search.toLowerCase().split(/\s+/).every((word) => text.includes(word)) ? 1 : 0
}
