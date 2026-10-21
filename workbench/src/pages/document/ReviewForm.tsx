import { Check, CircleAlert, Plus, Save, Trash2, TriangleAlert } from 'lucide-react'
import { type ReactNode, useEffect, useState } from 'react'
import {
  Controller,
  type Control,
  type UseFormRegister,
  type UseFormSetValue,
  useFieldArray,
  useForm,
  useWatch,
} from 'react-hook-form'

import type { CodingOptions, DocumentDetail, Issue } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { TYPE_LABELS } from '@/lib/labels'
import { formatScaled, parseAmount, sumAmounts, withinTolerance } from '@/lib/money'
import { cn } from '@/lib/utils'

import {
  AMOUNT_TYPE_LABELS,
  emptyLine,
  FIELD_LABELS,
  type FormValues,
  type HeaderField,
  type IssueMap,
  mapIssues,
  TAX_TYPE_LABELS,
  toFormValues,
  worst,
} from './formModel'

const NONE = '__none' // Radix Select items cannot have '' as their value

interface FieldSpec {
  name: HeaderField
  kind?: 'text' | 'date' | 'amount' | 'code'
}

const SECTIONS: { title: string; fields: FieldSpec[] }[] = [
  {
    title: 'Supplier and document',
    fields: [
      { name: 'vendor_name' },
      { name: 'vendor_trn', kind: 'code' },
      { name: 'invoice_number', kind: 'code' },
      { name: 'invoice_date', kind: 'date' },
      { name: 'due_date', kind: 'date' },
      { name: 'po_number', kind: 'code' },
    ],
  },
  {
    title: 'Amounts',
    fields: [
      { name: 'currency', kind: 'code' },
      { name: 'subtotal', kind: 'amount' },
      { name: 'vat', kind: 'amount' },
      { name: 'total', kind: 'amount' },
    ],
  },
  {
    title: 'Customer (the restaurant)',
    fields: [{ name: 'recipient_name' }, { name: 'recipient_trn', kind: 'code' }],
  },
]

export interface ReviewSubmit {
  values: FormValues
  confirmed: string[]
}

/**
 * The invoice as the model read it, with every check's findings next to the field concerned
 * (task 4.5). Nothing is fixed silently (rule 8): printed values the checks read themselves
 * are offered as suggestions; the accountant decides.
 */
export function ReviewForm({
  document,
  options,
  editable,
  saving,
  onSave,
  onDirtyChange,
  actions,
}: {
  document: DocumentDetail
  options: CodingOptions | undefined
  editable: boolean
  saving: boolean
  onSave: (submit: ReviewSubmit) => void
  onDirtyChange?: (dirty: boolean) => void
  actions?: ReactNode
}) {
  const { register, control, handleSubmit, setValue, formState } = useForm<FormValues>({
    defaultValues: toFormValues(document.invoice),
  })
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set())
  const issues = mapIssues(document.issues)
  const verified = new Set(document.verified_fields)
  const documentType = useWatch({ control, name: 'document_type' })

  const dirty = formState.isDirty || confirmed.size > 0
  useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange])

  function toggleConfirmed(field: string, on: boolean) {
    const next = new Set(confirmed)
    if (on) next.add(field)
    else next.delete(field)
    setConfirmed(next)
  }

  return (
    <form
      aria-label="Review"
      className="space-y-6"
      onSubmit={handleSubmit((values) => onSave({ values, confirmed: [...confirmed] }))}
    >
      {document.invoice === null && (
        <p className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950 dark:bg-amber-500/10 dark:text-amber-100">
          The extracted data could not be read. Enter the document by hand.
        </p>
      )}
      <fieldset disabled={!editable} className="space-y-6">
        <GeneralIssues issues={issues.general} />

        <section className="grid gap-4 sm:grid-cols-2">
          <SelectField label={FIELD_LABELS.document_type ?? ''} issues={issues.byField.get('document_type')}>
            <Controller
              control={control}
              name="document_type"
              render={({ field }) => (
                <Select value={field.value} onValueChange={field.onChange} disabled={!editable}>
                  <SelectTrigger aria-label="Document type" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(TYPE_LABELS).map(([value, label]) => (
                      <SelectItem key={value} value={value}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            />
          </SelectField>
          <SelectField label={FIELD_LABELS.amount_type ?? ''} issues={issues.byField.get('amount_type')}>
            <Controller
              control={control}
              name="amount_type"
              render={({ field }) => (
                <Select value={field.value || NONE} disabled={!editable}
                  onValueChange={(v) => field.onChange(v === NONE ? '' : v)}>
                  <SelectTrigger aria-label="Amounts are" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE}>Not stated</SelectItem>
                    {Object.entries(AMOUNT_TYPE_LABELS).map(([value, label]) => (
                      <SelectItem key={value} value={value}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            />
          </SelectField>
        </section>

        {SECTIONS.map((section) => (
          <section key={section.title} className="space-y-3">
            <h3 className="text-sm font-semibold text-muted-foreground">{section.title}</h3>
            <div className="grid gap-4 sm:grid-cols-2">
              {section.fields.map((spec) => (
                <HeaderInput
                  key={spec.name}
                  spec={spec}
                  register={register}
                  setValue={setValue}
                  issues={issues.byField.get(spec.name)}
                  verified={verified.has(spec.name)}
                  confirmed={confirmed.has(spec.name)}
                  onConfirm={(on) => toggleConfirmed(spec.name, on)}
                  editable={editable}
                />
              ))}
              {section.title === 'Supplier and document' && documentType === 'CREDIT_NOTE' && (
                <HeaderInput
                  spec={{ name: 'original_invoice_ref', kind: 'code' }}
                  register={register}
                  setValue={setValue}
                  issues={issues.byField.get('original_invoice_ref')}
                  verified={verified.has('original_invoice_ref')}
                  confirmed={confirmed.has('original_invoice_ref')}
                  onConfirm={(on) => toggleConfirmed('original_invoice_ref', on)}
                  editable={editable}
                />
              )}
            </div>
          </section>
        ))}

        <Totals control={control} />

        <Lines
          control={control}
          register={register}
          issues={issues}
          options={options}
          coding={document.coding}
          editable={editable}
        />
      </fieldset>

      {editable && (
        <div className="sticky bottom-0 flex flex-wrap items-center gap-2 border-t bg-background py-3">
          <Button type="submit" disabled={saving}>
            <Save aria-hidden />
            {saving ? 'Saving…' : 'Save & re-check'}
          </Button>
          {actions}
          {dirty && (
            <span className="text-sm text-muted-foreground">Unsaved changes</span>
          )}
        </div>
      )}
    </form>
  )
}

function HeaderInput({
  spec,
  register,
  setValue,
  issues,
  verified,
  confirmed,
  onConfirm,
  editable,
}: {
  spec: FieldSpec
  register: UseFormRegister<FormValues>
  setValue: UseFormSetValue<FormValues>
  issues: Issue[] | undefined
  verified: boolean
  confirmed: boolean
  onConfirm: (on: boolean) => void
  editable: boolean
}) {
  const id = `field-${spec.name}`
  const level = worst(issues)
  const label = FIELD_LABELS[spec.name] ?? spec.name
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor={id}>{label}</Label>
        {verified && !level && (
          <span className="flex items-center gap-1 text-xs text-emerald-700 dark:text-emerald-300">
            <Check className="size-3" aria-hidden /> Checked
          </span>
        )}
      </div>
      <Input
        id={id}
        type={spec.kind === 'date' ? 'date' : 'text'}
        inputMode={spec.kind === 'amount' ? 'decimal' : undefined}
        aria-invalid={level === 'ERROR' || undefined}
        className={cn(
          spec.kind === 'amount' && 'text-right tabular-nums',
          level === 'WARNING' && 'border-amber-400 bg-amber-50 dark:bg-amber-500/10',
        )}
        {...register(spec.name)}
      />
      {issues?.map((issue) => (
        <IssueNote key={issue.code} issue={issue}>
          {editable && issue.suggestion && (
            <Button
              type="button"
              variant="outline"
              size="xs"
              onClick={() => setValue(spec.name, issue.suggestion ?? '', { shouldDirty: true })}
            >
              Use printed value {issue.suggestion}
            </Button>
          )}
        </IssueNote>
      ))}
      {editable && level && (
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <Checkbox checked={confirmed} onCheckedChange={(on) => onConfirm(on === true)}
            aria-label={`${label} checked against the original`} />
          Checked against the original: it is right
        </label>
      )}
    </div>
  )
}

function SelectField({
  label,
  issues,
  children,
}: {
  label: string
  issues: Issue[] | undefined
  children: ReactNode
}) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
      {issues?.map((issue) => <IssueNote key={issue.code} issue={issue} />)}
    </div>
  )
}

function IssueNote({ issue, children }: { issue: Issue; children?: ReactNode }) {
  const error = issue.severity === 'ERROR'
  const Icon = error ? CircleAlert : TriangleAlert
  return (
    <div
      className={cn(
        'flex flex-wrap items-start gap-1.5 text-xs',
        error ? 'text-destructive' : 'text-amber-800 dark:text-amber-200',
      )}
    >
      <Icon className="mt-0.5 size-3.5 shrink-0" aria-label={error ? 'Error' : 'Warning'} />
      <span className="flex-1">{issue.message}</span>
      {children}
    </div>
  )
}

function GeneralIssues({ issues }: { issues: Issue[] }) {
  if (issues.length === 0) return null
  return (
    <section aria-label="Document issues" className="space-y-1.5 rounded-lg border p-3">
      {issues.map((issue) => (
        <IssueNote key={`${issue.code}-${issue.message}`} issue={issue} />
      ))}
    </section>
  )
}

/** Live arithmetic while typing, with the server's ±0.01 tolerance; the server re-checks. */
function Totals({ control }: { control: Control<FormValues> }) {
  const [lines, subtotal, vat, total, amountType] = useWatch({
    control,
    name: ['lines', 'subtotal', 'vat', 'total', 'amount_type'],
  })
  const rows: { label: string; ok: boolean | null; detail: string }[] = []

  const linesSum = lines.length ? sumAmounts(lines.map((line) => line.amount)) : null
  const [target, targetName] = amountType === 'INCLUSIVE' || !subtotal.trim()
    ? [total, 'total'] : [subtotal, 'subtotal']  // prettier-ignore
  const targetValue = parseAmount(target)
  if (linesSum !== null && targetValue !== null) {
    rows.push(compare(`Lines add up to the ${targetName}`, linesSum, targetValue))
  } else if (lines.length) {
    rows.push({ label: 'Lines total', ok: null, detail: 'some amounts are empty or not numbers' })
  }

  const [sub, tax, all] = [parseAmount(subtotal), parseAmount(vat), parseAmount(total)]
  if (sub !== null && tax !== null && all !== null) {
    rows.push(compare('Subtotal + VAT = total', sub + tax, all))
  }
  if (rows.length === 0) return null

  return (
    <section aria-label="Totals check" className="space-y-1 rounded-lg border p-3 text-sm">
      {rows.map((row) => (
        <div key={row.label} className="flex flex-wrap items-center gap-2">
          {row.ok === true ? (
            <Check className="size-4 text-emerald-600" aria-label="Adds up" />
          ) : (
            <CircleAlert className="size-4 text-destructive" aria-label="Does not add up" />
          )}
          <span>{row.label}</span>
          <span className="text-muted-foreground">{row.detail}</span>
        </div>
      ))}
    </section>
  )
}

function compare(label: string, actual: bigint, expected: bigint) {
  const ok = withinTolerance(actual, expected)
  const detail = ok
    ? formatScaled(actual)
    : `${formatScaled(actual)} vs ${formatScaled(expected)}, off by ${formatScaled(actual - expected)}`
  return { label, ok, detail }
}

const SOURCE_HINTS: Record<string, string> = {
  supplier_history: 'From supplier history',
  model: 'AI suggestion',
}

function Lines({
  control,
  register,
  issues,
  options,
  coding,
  editable,
}: {
  control: Control<FormValues>
  register: UseFormRegister<FormValues>
  issues: IssueMap
  options: CodingOptions | undefined
  coding: DocumentDetail['coding']
  editable: boolean
}) {
  const { fields, append, remove } = useFieldArray({ control, name: 'lines' })
  const lineValues = useWatch({ control, name: 'lines' })
  const accounts = options?.accounts ?? []
  const taxTypes = options?.tax_types ?? []

  return (
    <section className="space-y-3" aria-label="Lines">
      <h3 className="text-sm font-semibold text-muted-foreground">Lines</h3>
      {fields.length === 0 && <p className="text-sm text-muted-foreground">No lines.</p>}
      <ol className="space-y-3">
        {fields.map((field, index) => {
          const lineIssues = (name: string) => issues.byLine.get(`${index}.${name}`)
          const account = lineValues[index]?.account_code ?? ''
          const suggested = coding[index]
          const hint =
            suggested && suggested.account_code === account
              ? SOURCE_HINTS[String(suggested.account_source)]
              : undefined
          const unknownAccount = account && !accounts.some((a) => a.code === account)
          return (
            <li key={field.id} aria-label={`Line ${index + 1}`} className="space-y-2 rounded-lg border p-3">
              <div className="flex items-start gap-2">
                <span className="pt-2 text-xs text-muted-foreground">{index + 1}</span>
                <div className="grid flex-1 gap-2 sm:grid-cols-[1fr_5rem_6rem_7rem]">
                  <Input aria-label="Description" placeholder="Description" {...register(`lines.${index}.description`)} />
                  <Input aria-label="Quantity" placeholder="Qty" inputMode="decimal" className="text-right tabular-nums"
                    {...register(`lines.${index}.qty`)} />
                  <Input aria-label="Unit price" placeholder="Price" inputMode="decimal" className="text-right tabular-nums"
                    {...register(`lines.${index}.unit_price`)} />
                  <Input aria-label="Amount" placeholder="Amount" inputMode="decimal"
                    aria-invalid={worst(lineIssues('amount')) === 'ERROR' || undefined}
                    className="text-right tabular-nums" {...register(`lines.${index}.amount`)} />
                </div>
                {editable && (
                  <Button type="button" variant="ghost" size="icon-sm" aria-label={`Remove line ${index + 1}`}
                    onClick={() => remove(index)}>
                    <Trash2 />
                  </Button>
                )}
              </div>
              <div className="grid gap-2 pl-5 sm:grid-cols-2">
                <div className="space-y-1">
                  <Controller
                    control={control}
                    name={`lines.${index}.account_code`}
                    render={({ field: f }) => (
                      <Select value={f.value || NONE} disabled={!editable}
                        onValueChange={(v) => f.onChange(v === NONE ? '' : v)}>
                        <SelectTrigger aria-label={`Account for line ${index + 1}`} className="w-full"
                          aria-invalid={worst(lineIssues('account_code')) === 'ERROR' || undefined}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value={NONE}>Choose an account</SelectItem>
                          {unknownAccount && (
                            <SelectItem value={account}>{account} (not in the master data)</SelectItem>
                          )}
                          {accounts.map((a) => (
                            <SelectItem key={a.code} value={a.code}>
                              {a.code} {a.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  />
                  {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
                </div>
                <Controller
                  control={control}
                  name={`lines.${index}.tax_type`}
                  render={({ field: f }) => (
                    <Select value={f.value || NONE} disabled={!editable}
                      onValueChange={(v) => f.onChange(v === NONE ? '' : v)}>
                      <SelectTrigger aria-label={`VAT for line ${index + 1}`} className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NONE}>Choose VAT</SelectItem>
                        {taxTypes.map((t) => (
                          <SelectItem key={t.tax_type} value={t.tax_type}>
                            {TAX_TYPE_LABELS[t.tax_type]} ({t.code})
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                />
              </div>
              {[...(issues.byLine.get(String(index)) ?? []),
                ...['description', 'qty', 'unit_price', 'amount', 'account_code', 'tax_type']
                  .flatMap((name) => lineIssues(name) ?? [])].map((issue) => (
                <IssueNote key={`${issue.code}-${issue.field}`} issue={issue} />
              ))}
            </li>
          )
        })}
      </ol>
      {editable && (
        <Button type="button" variant="outline" size="sm" onClick={() => append(emptyLine())}>
          <Plus aria-hidden />
          Add line
        </Button>
      )}
    </section>
  )
}
