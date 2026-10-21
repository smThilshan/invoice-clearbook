/**
 * The review form's values and their mapping to the backend's Invoice. Every value is text in
 * the form ('' = not on the document); amounts stay text all the way to the server (rule 1).
 */
import type {
  AmountType,
  DocumentType,
  Invoice,
  InvoiceInput,
  Issue,
  TaxType,
} from '@/api/client'

export interface LineValues {
  description: string
  qty: string
  unit_price: string
  amount: string
  account_code: string
  tax_type: TaxType | ''
}

export interface FormValues {
  document_type: DocumentType
  vendor_name: string
  vendor_trn: string
  invoice_number: string
  invoice_date: string
  due_date: string
  currency: string
  amount_type: AmountType | ''
  subtotal: string
  vat: string
  total: string
  recipient_name: string
  recipient_trn: string
  po_number: string
  original_invoice_ref: string
  lines: LineValues[]
}

export type HeaderField = Exclude<keyof FormValues, 'lines' | 'document_type' | 'amount_type'>

export const FIELD_LABELS: Record<string, string> = {
  document_type: 'Document type',
  vendor_name: 'Supplier',
  vendor_trn: 'Supplier TRN',
  invoice_number: 'Number',
  invoice_date: 'Date',
  due_date: 'Due date',
  currency: 'Currency',
  amount_type: 'Amounts are',
  subtotal: 'Subtotal',
  vat: 'VAT',
  total: 'Total',
  recipient_name: 'Customer',
  recipient_trn: 'Customer TRN',
  po_number: 'PO number',
  original_invoice_ref: 'Original invoice',
  lines: 'Lines',
}

export const AMOUNT_TYPE_LABELS: Record<AmountType, string> = {
  EXCLUSIVE: 'VAT added on top',
  INCLUSIVE: 'VAT included',
  NO_TAX: 'No VAT',
}

export const TAX_TYPE_LABELS: Record<TaxType, string> = {
  STANDARD_5: 'Standard 5%',
  ZERO: 'Zero rated',
  EXEMPT: 'Exempt',
  REVERSE_CHARGE: 'Reverse charge',
  NONE: 'No VAT',
}

export function emptyLine(): LineValues {
  return { description: '', qty: '', unit_price: '', amount: '', account_code: '', tax_type: '' }
}

const text = (value: string | null | undefined) => value ?? ''

export function toFormValues(invoice: Invoice | null | undefined): FormValues {
  return {
    document_type: invoice?.document_type ?? 'BILL',
    vendor_name: text(invoice?.vendor_name),
    vendor_trn: text(invoice?.vendor_trn),
    invoice_number: text(invoice?.invoice_number),
    invoice_date: text(invoice?.invoice_date),
    due_date: text(invoice?.due_date),
    currency: text(invoice?.currency),
    amount_type: invoice?.amount_type ?? '',
    subtotal: text(invoice?.subtotal),
    vat: text(invoice?.vat),
    total: text(invoice?.total),
    recipient_name: text(invoice?.recipient_name),
    recipient_trn: text(invoice?.recipient_trn),
    po_number: text(invoice?.po_number),
    original_invoice_ref: text(invoice?.original_invoice_ref),
    lines: (invoice?.lines ?? []).map((line) => ({
      description: text(line.description),
      qty: text(line.qty),
      unit_price: text(line.unit_price),
      amount: line.amount,
      account_code: text(line.account_code),
      tax_type: line.tax_type ?? '',
    })),
  }
}

/** '' -> null; amounts are sent as the exact text typed (thousands separators removed). */
export function toInvoiceInput(values: FormValues): InvoiceInput {
  const value = (field: string) => field.trim() || null
  const amount = (field: string) => value(field.replace(/,/g, ''))
  return {
    document_type: values.document_type,
    vendor_name: value(values.vendor_name),
    vendor_trn: value(values.vendor_trn),
    invoice_number: value(values.invoice_number),
    invoice_date: value(values.invoice_date),
    due_date: value(values.due_date),
    currency: value(values.currency)?.toUpperCase() ?? null,
    amount_type: values.amount_type || null,
    subtotal: amount(values.subtotal),
    vat: amount(values.vat),
    total: amount(values.total),
    recipient_name: value(values.recipient_name),
    recipient_trn: value(values.recipient_trn),
    po_number: value(values.po_number),
    original_invoice_ref: value(values.original_invoice_ref),
    lines: values.lines.map((line) => ({
      description: value(line.description),
      qty: amount(line.qty),
      unit_price: amount(line.unit_price),
      amount: amount(line.amount) ?? '',
      account_code: value(line.account_code),
      tax_type: line.tax_type || null,
    })),
  }
}

export interface IssueMap {
  /** Issues on one header field, by field name ("total"). */
  byField: Map<string, Issue[]>
  /** Issues on one line field, by "index.field" ("2.amount"); "2" = the whole line. */
  byLine: Map<string, Issue[]>
  /** The whole document, or all lines ("lines"). */
  general: Issue[]
}

/** Sorts issues to where they are shown: next to a field, on a line, or at the top. */
export function mapIssues(issues: Issue[]): IssueMap {
  const map: IssueMap = { byField: new Map(), byLine: new Map(), general: [] }
  const add = (target: Map<string, Issue[]>, key: string, issue: Issue) =>
    target.set(key, [...(target.get(key) ?? []), issue])
  for (const issue of issues) {
    const line = /^lines\[(\d+)\](?:\.(\w+))?$/.exec(issue.field ?? '')
    if (line) add(map.byLine, line[2] ? `${line[1]}.${line[2]}` : (line[1] ?? ''), issue)
    else if (issue.field && issue.field !== 'lines') add(map.byField, issue.field, issue)
    else map.general.push(issue)
  }
  return map
}

export function worst(issues: Issue[] | undefined): 'ERROR' | 'WARNING' | null {
  if (!issues?.length) return null
  return issues.some((issue) => issue.severity === 'ERROR') ? 'ERROR' : 'WARNING'
}
