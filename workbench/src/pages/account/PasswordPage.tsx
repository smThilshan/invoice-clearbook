import { CircleCheck, KeyRound, LogOut } from 'lucide-react'
import { type FormEvent, useState } from 'react'
import { useNavigate } from 'react-router'

import { useChangePassword, useLogout, useMe } from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { PageContainer, PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { usePageTitle } from '@/lib/usePageTitle'

/** The API's rule (decision 93), said before sending; the API checks it again. */
export const MIN_PASSWORD_LENGTH = 14

/** Decision 93: someone whose password an admin set sees only this until they choose their
 * own; no other page opens (the API refuses them too). */
export function ForcedPasswordChange() {
  usePageTitle('Choose your password')
  const me = useMe().data
  const logout = useLogout()
  const navigate = useNavigate()
  return (
    <div className="grid min-h-dvh place-items-center bg-muted/30 p-4">
      <Card className="w-full max-w-sm shadow-sm">
        <CardHeader>
          <CardTitle>Choose your own password</CardTitle>
          <CardDescription>
            {me ? `${me.name}, the` : 'The'} password you were given was for the first login only. Choose
            your own to continue.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <PasswordForm currentLabel="Password you were given" />
          <Button
            type="button"
            variant="ghost"
            className="w-full"
            disabled={logout.isPending}
            onClick={() => logout.mutate(undefined, { onSettled: () => void navigate('/login') })}
          >
            <LogOut aria-hidden />
            Log out
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}

/** Changing one's own password at any time, from the user menu. */
export function PasswordPage() {
  usePageTitle('Change password')
  return (
    <PageContainer>
      <PageHeader
        title="Change password"
        description="Your other sessions (another computer, your phone) are logged out; this one stays."
      />
      <div className="max-w-sm rounded-xl border bg-background p-4 shadow-sm">
        <PasswordForm currentLabel="Current password" />
      </div>
    </PageContainer>
  )
}

function PasswordForm({ currentLabel }: { currentLabel: string }) {
  const change = useChangePassword()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const problem =
    next.length > 0 && next.length < MIN_PASSWORD_LENGTH
      ? `At least ${MIN_PASSWORD_LENGTH} characters.`
      : again.length > 0 && again !== next
        ? 'The two new passwords differ.'
        : null

  function submit(event: FormEvent) {
    event.preventDefault()
    if (problem || next !== again) return
    change.mutate(
      { current_password: current, new_password: next },
      {
        onSuccess: () => {
          setCurrent('')
          setNext('')
          setAgain('')
        },
      },
    )
  }

  return (
    <form aria-label="Change password" className="space-y-4" onSubmit={submit}>
      <div className="space-y-1.5">
        <Label htmlFor="current-password">{currentLabel}</Label>
        <Input
          id="current-password"
          type="password"
          autoComplete="current-password"
          required
          autoFocus
          value={current}
          onChange={(event) => setCurrent(event.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="new-password">New password</Label>
        <Input
          id="new-password"
          type="password"
          autoComplete="new-password"
          required
          aria-describedby="new-password-hint"
          value={next}
          onChange={(event) => setNext(event.target.value)}
        />
        <p id="new-password-hint" className="text-xs text-muted-foreground">
          At least {MIN_PASSWORD_LENGTH} characters, used nowhere else. A password manager can make one.
        </p>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="new-password-again">New password again</Label>
        <Input
          id="new-password-again"
          type="password"
          autoComplete="new-password"
          required
          value={again}
          onChange={(event) => setAgain(event.target.value)}
        />
      </div>
      {problem && (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}
      {change.isError && <ErrorAlert title="Password not changed" error={change.error} />}
      {change.isSuccess && (
        <p role="status" className="flex items-center gap-2 text-sm">
          <CircleCheck className="size-4 text-success" aria-hidden />
          Password changed. Your other sessions were logged out.
        </p>
      )}
      <Button type="submit" className="w-full" disabled={change.isPending || Boolean(problem)}>
        <KeyRound aria-hidden />
        {change.isPending ? 'Changing…' : 'Change password'}
      </Button>
    </form>
  )
}
