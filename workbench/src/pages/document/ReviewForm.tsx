import { Check, CircleAlert, Plus, Save, Trash2, TriangleAlert } from 'lucide-react'
import { type ReactNode, useEffect, useState } from 'react'
import {
  Controller,
  type Control,
  type Path,
  type UseFormRegister,
  type UseFormSetValue,
  useFieldArray,
  useForm,
  useWatch,
} from 'react-hook-form'

import type { AmountType, CodingOptions, Correction, DocumentDetail, Issue, ValueKind } from '@/api/client'
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
import { formatAmount, formatDate, TYPE_LABELS } from '@/lib/labels'
import { formatScaled, parseAmount, sumAmounts, withinTolerance } from '@/lib/money'
import { useDebounced } from '@/lib/useDebounced'
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
const LOCATE_DELAY_MS = 300 // wait for a pause in typing before looking the value up

/** The field being looked at, for the page viewer to highlight (task 4.6). */
export interface FieldTarget {
  label: string
  kind: ValueKind
  value: string
}

interface ActiveField {
  name: Path<FormValues>
  label: string
  kind: ValueKind
}

function locateKind(spec: FieldSpec): ValueKind {
  if (spec.name.endsWith('_trn')) return 'trn'
  if (spec.kind === 'date') return 'date'
  if (spec.kind === 'amount') return 'amount'
  return 'text'
}

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
    title: 'Customer (the client)',
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
  onTarget,
  actions,
}: {
  document: DocumentDetail
  options: CodingOptions | undefined
  editable: boolean
  saving: boolean
  onSave: (submit: ReviewSubmit) => void
  onDirtyChange?: (dirty: boolean) => void
  onTarget?: (target: FieldTarget | null) => void
  actions?: ReactNode
}) {
  const { register, control, handleSubmit, setValue, formState } = useForm<FormValues>({
    defaultValues: toFormValues(document.invoice),
  })
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set())
  const issues = mapIssues(document.issues)
  const verified = new Set(document.verified_fields)
  const corrected = new Map(document.corrections.map((c) => [c.field, c]))
  const documentType = useWatch({ control, name: 'document_type' })

  const dirty = formState.isDirty || confirmed.size > 0
  useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange])

  const [active, setActive] = useState<ActiveField | null>(null)
  const watched: unknown = useWatch({ control, name: active?.name ?? 'vendor_name' })
  const activeValue = useDebounced(active ? String(watched ?? '') : '', LOCATE_DELAY_MS)
  useEffect(() => {
    onTarget?.(active ? { label: active.label, kind: active.kind, value: activeValue } : null)
  }, [active, activeValue, onTarget])

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
          <SelectField label={FIELD_LABELS.document_type ?? ''} issues={issues.byField.get('document_type')}
            correction={corrected.get('document_type')}>
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
          <SelectField
            label={FIELD_LABELS.amount_type ?? ''}
            issues={issues.byField.get('amount_type')}
            suggest={editable ? {
              label: (value) => AMOUNT_TYPE_LABELS[value as AmountType] ?? value,
              apply: (value) => setValue('amount_type', value as AmountType, { shouldDirty: true }),
            } : undefined}
          >
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
                  correction={corrected.get(spec.name)}
                  confirmed={confirmed.has(spec.name)}
                  onConfirm={(on) => toggleConfirmed(spec.name, on)}
                  onFocus={() => setActive(headerTarget(spec))}
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
                  correction={corrected.get('original_invoice_ref')}
                  confirmed={confirmed.has('original_invoice_ref')}
                  onConfirm={(on) => toggleConfirmed('original_invoice_ref', on)}
                  onFocus={() => setActive(headerTarget({ name: 'original_invoice_ref' }))}
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
          corrected={corrected}
          editable={editable}
          onFocusField={setActive}
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

function headerTarget(spec: FieldSpec): ActiveField {
  return { name: spec.name, label: FIELD_LABELS[spec.name] ?? spec.name, kind: locateKind(spec) }
}

function HeaderInput({
  spec,
  register,
  setValue,
  issues,
  verified,
  correction,
  confirmed,
  onConfirm,
  onFocus,
  editable,
}: {
  spec: FieldSpec
  register: UseFormRegister<FormValues>
  setValue: UseFormSetValue<FormValues>
  issues: Issue[] | undefined
  verified: boolean
  correction: Correction | undefined
  confirmed: boolean
  onConfirm: (on: boolean) => void
  onFocus: () => void
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
        onFocus={onFocus}
        {...register(spec.name)}
      />
      {correction && <AiRead value={shown(correction.model_value, spec.kind)} />}
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

/** A suggested value for a dropdown (e.g. the VAT type the amounts show, decision 34). */
interface Suggest {
  label: (value: string) => string
  apply: (value: string) => void
}

function SelectField({
  label,
  issues,
  correction,
  suggest,
  children,
}: {
  label: string
  issues: Issue[] | undefined
  correction?: Correction
  suggest?: Suggest
  children: ReactNode
}) {
  const before = correction?.model_value
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
      {correction && (
        <AiRead value={typeof before === 'string' ? (TYPE_LABELS[before as keyof typeof TYPE_LABELS] ?? before) : 'nothing'} />
      )}
      {issues?.map((issue) => (
        <IssueNote key={issue.code} issue={issue}>
          {suggest && issue.suggestion && (
            <Button type="button" variant="outline" size="xs"
              onClick={() => suggest.apply(issue.suggestion ?? '')}>
              Use: {suggest.label(issue.suggestion)}
            </Button>
          )}
        </IssueNote>
      ))}
    </div>
  )
}

/** What the model read (or the coding step suggested) for a field the accountant changed. */
function AiRead({ value, prefix = 'AI read' }: { value: string; prefix?: string }) {
  return (
    <p className="text-xs text-muted-foreground">
      {prefix}: {value}
    </p>
  )
}

function shown(value: unknown, kind?: FieldSpec['kind']): string {
  if (value === null || value === undefined || value === '') return 'nothing'
  const text = String(value)
  if (kind === 'amount') return formatAmount(text)
  if (kind === 'date') return formatDate(text)
  return text
}

const LINE_FIELD_LABELS: Record<string, string> = {
  description: 'Description',
  qty: 'Quantity',
  unit_price: 'Unit price',
  amount: 'Amount',
  account_code: 'Account',
  tax_type: 'VAT',
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
  corrected,
  editable,
  onFocusField,
}: {
  control: Control<FormValues>
  register: UseFormRegister<FormValues>
  issues: IssueMap
  options: CodingOptions | undefined
  coding: DocumentDetail['coding']
  corrected: Map<string, Correction>
  editable: boolean
  onFocusField: (field: ActiveField) => void
}) {
  const { fields, append, remove } = useFieldArray({ control, name: 'lines' })
  const lineValues = useWatch({ control, name: 'lines' })
  const accounts = options?.accounts ?? []
  const taxTypes = options?.tax_types ?? []

  return (
    <section className="space-y-3" aria-label="Lines">
      <h3 className="text-sm font-semibold text-muted-foreground">Lines</h3>
      {fields.length === 0 && <p className="text-sm text-muted-foreground">No lines.</p>}
      {fields.length > 0 && (
        // Column headings, lined up with the boxes below by the same layout: invisible copies
        // of the line number and the remove button take their space. Stacked boxes on narrow
        // screens have no columns, so the headings show from the sm breakpoint only.
        <div aria-hidden className="hidden items-end gap-2 px-3 text-xs font-medium text-muted-foreground sm:flex">
          <span className="invisible">{fields.length}</span>
          <div className="grid flex-1 gap-2 sm:grid-cols-[1fr_5rem_6rem_7rem]">
            <span>Description</span>
            <span className="text-right">Qty</span>
            <span className="text-right">Unit price</span>
            <span className="text-right">Amount</span>
          </div>
          {editable && (
            <Button type="button" variant="ghost" size="icon-sm" tabIndex={-1} className="invisible">
              <Trash2 />
            </Button>
          )}
        </div>
      )}
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
          const focus = (name: 'description' | 'qty' | 'unit_price' | 'amount', label: string) => () =>
            onFocusField({
              name: `lines.${index}.${name}`,
              label: `Line ${index + 1} ${label}`,
              kind: name === 'description' ? 'text' : 'amount',
            })
          return (
            <li key={field.id} aria-label={`Line ${index + 1}`} className="space-y-2 rounded-lg border p-3">
              <div className="flex items-start gap-2">
                <span className="pt-2 text-xs text-muted-foreground">{index + 1}</span>
                <div className="grid flex-1 gap-2 sm:grid-cols-[1fr_5rem_6rem_7rem]">
                  <Input aria-label="Description" placeholder="Description"
                    onFocus={focus('description', 'description')} {...register(`lines.${index}.description`)} />
                  <Input aria-label="Quantity" placeholder="Qty" inputMode="decimal" className="text-right tabular-nums"
                    onFocus={focus('qty', 'quantity')} {...register(`lines.${index}.qty`)} />
                  <Input aria-label="Unit price" placeholder="Price" inputMode="decimal" className="text-right tabular-nums"
                    onFocus={focus('unit_price', 'unit price')} {...register(`lines.${index}.unit_price`)} />
                  <Input aria-label="Amount" placeholder="Amount" inputMode="decimal"
                    aria-invalid={worst(lineIssues('amount')) === 'ERROR' || undefined}
                    onFocus={focus('amount', 'amount')}
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
              {Object.entries(LINE_FIELD_LABELS).map(([name, label]) => {
                const correction = corrected.get(`lines[${index}].${name}`)
                if (!correction) return null
                const code = name === 'account_code' || name === 'tax_type'
                const value = shown(correction.model_value, name === 'description' ? undefined : 'amount')
                return (
                  <AiRead key={name} prefix={`${label}: ${code ? 'suggested' : 'AI read'}`}
                    value={code ? shown(correction.model_value) : value} />
                )
              })}
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
