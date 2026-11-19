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
import { formatScaled, parseAmount, sumAmounts, withCents, withinTolerance } from '@/lib/money'

export interface LineValues {
  description: string
  qty: string
  unit_price: string
  amount: string
  account_code: string
  tax_type: TaxType | ''
  item_code: string  // the printed barcode, read by the server (decision 82); not edited
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
  paid_through: string  // an expense's payment account (decision 89); kept even when not shown
  lines: LineValues[]
}

export type HeaderField = Exclude<keyof FormValues, 'lines' | 'document_type' | 'amount_type' | 'paid_through'>

export const FIELD_LABELS: Record<string, string> = {
  document_type: 'Document type',
  vendor_name: 'Supplier',
  vendor_trn: 'Supplier VAT registration no',
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
  paid_through: 'Paid through',
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
  return { description: '', qty: '', unit_price: '', amount: '', account_code: '', tax_type: '', item_code: '' }
}

const text = (value: string | null | undefined) => value ?? ''
// Shown with two decimals at least ("4.5" -> "4.50"); the value is the same (rule 1).
const money = (value: string | null | undefined) => withCents(text(value))

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
    subtotal: money(invoice?.subtotal),
    vat: money(invoice?.vat),
    total: money(invoice?.total),
    recipient_name: text(invoice?.recipient_name),
    recipient_trn: text(invoice?.recipient_trn),
    po_number: text(invoice?.po_number),
    original_invoice_ref: text(invoice?.original_invoice_ref),
    paid_through: text(invoice?.paid_through),
    lines: (invoice?.lines ?? []).map((line) => ({
      description: text(line.description),
      qty: text(line.qty),
      unit_price: money(line.unit_price),
      amount: money(line.amount),
      account_code: text(line.account_code),
      tax_type: line.tax_type ?? '',
      item_code: text(line.item_code),
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
    paid_through: value(values.paid_through),
    lines: values.lines.map((line) => ({
      description: value(line.description),
      qty: amount(line.qty),
      unit_price: amount(line.unit_price),
      amount: amount(line.amount) ?? '',
      account_code: value(line.account_code),
      tax_type: line.tax_type || null,
      item_code: line.item_code || null,
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

/** Decision 105: the lines' VAT codes once "Amounts are" changes from `before` to `next`. No VAT:
 * lines on Standard 5% go to No VAT. Back from No VAT to VAT added on top or included: lines on
 * No VAT go to Standard 5%. Zero rated, exempt and reverse charge lines are never touched, nor
 * any line when the company has no such code (`available`, from its synced VAT rates). */
export function linesForVatType(
  taxTypes: (TaxType | '')[],
  before: AmountType | '',
  next: AmountType | '',
  available: TaxType[],
): (TaxType | '')[] {
  const swap = (from: TaxType, to: TaxType) =>
    available.includes(to) ? taxTypes.map((tax) => (tax === from ? to : tax)) : taxTypes
  if (next === 'NO_TAX' && before !== 'NO_TAX') return swap('STANDARD_5', 'NONE')
  if (before === 'NO_TAX' && (next === 'EXCLUSIVE' || next === 'INCLUSIVE')) return swap('NONE', 'STANDARD_5')
  return taxTypes
}

export type VatLine = Pick<LineValues, 'qty' | 'unit_price' | 'amount' | 'tax_type'>

export interface VatState {
  vat: string
  total: string
  lines: VatLine[]
}

const CENT = parseAmount('0.01') ?? 1n

const plain = (value: bigint) => formatScaled(value).replace(/,/g, '')

/** `value` rounded to whole cents (positive values only). */
function toCents(value: bigint): bigint {
  return ((value + CENT / 2n) / CENT) * CENT
}

/**
 * `extra` shared over the lines at `indexes` by their size, in whole cents, the rounding cent on
 * the biggest line, so the lines move by exactly `extra`. A unit price follows its amount. Null
 * (nothing changed) when a line is not a plain positive amount.
 */
function spread(lines: VatLine[], indexes: number[], extra: bigint): VatLine[] | null {
  const amounts = indexes.map((index) => parseAmount(lines[index]?.amount ?? ''))
  if (amounts.some((amount) => amount === null || amount <= 0n)) return null
  const sizes = amounts as bigint[]
  const base = sizes.reduce((a, b) => a + b, 0n)
  const negative = extra < 0n
  const whole = negative ? -extra : extra
  const shares = sizes.map((size) => toCents((whole * size) / base))
  const biggest = sizes.indexOf(sizes.reduce((a, b) => (b > a ? b : a)))
  shares[biggest] = (shares[biggest] ?? 0n) + whole - shares.reduce((a, b) => a + b, 0n)
  const next = lines.map((line) => ({ ...line }))
  for (const [position, index] of indexes.entries()) {
    const line = next[index]
    const share = shares[position] ?? 0n
    const amount = (sizes[position] ?? 0n) + (negative ? -share : share)
    if (!line || amount <= 0n) return null
    line.amount = plain(amount)
    const qty = parseAmount(line.qty)
    if (qty !== null && qty > 0n && line.unit_price !== '') {
      const factor = parseAmount('1') ?? 1n
      line.unit_price = plain((2n * amount * factor + qty) / (2n * qty))
    }
  }
  return next
}

/**
 * Decision 106: everything "Amounts are" carries, worked out on the click (the accountant's
 * choice, so nothing changes by itself). The lines' VAT codes as in `linesForVatType`; then:
 * - to No VAT: the VAT box empties. If the printed VAT is still missing from the lines (lines +
 *   VAT = total), it is added into the lines that went to No VAT: unclaimed VAT is part of the
 *   cost, and the total never changes.
 * - back to VAT: the VAT box gets `vatBack` (the VAT before, or the one the AI read) when empty.
 *   Added on top, with lines that add up to the total (VAT inside them): that VAT comes out of
 *   the lines that went back to 5%.
 * Amounts only move when the numbers prove it; otherwise they stay and the checks say so.
 */
export function followVatType(
  state: VatState,
  before: AmountType | '',
  next: AmountType | '',
  available: TaxType[],
  vatBack: string,
): VatState {
  const taxes = linesForVatType(state.lines.map((line) => line.tax_type), before, next, available)
  const switched = taxes.flatMap((tax, index) => (tax !== state.lines[index]?.tax_type ? [index] : []))
  let lines = state.lines.map((line, index) => ({ ...line, tax_type: taxes[index] ?? line.tax_type }))
  let vat = state.vat
  const total = parseAmount(state.total)
  const sum = sumAmounts(lines.map((line) => line.amount))

  if (next === 'NO_TAX' && before !== 'NO_TAX') {
    const printed = parseAmount(vat)
    vat = ''
    if (printed !== null && printed > 0n && total !== null && sum !== null && switched.length) {
      const missing = total - sum
      if (missing > 0n && withinTolerance(missing, printed)) lines = spread(lines, switched, missing) ?? lines
    }
  } else if (before === 'NO_TAX' && (next === 'EXCLUSIVE' || next === 'INCLUSIVE')) {
    if (vat.trim() === '') vat = vatBack
    const restored = parseAmount(vat)
    if (next === 'EXCLUSIVE' && restored !== null && restored > 0n && total !== null && sum !== null
      && switched.length && withinTolerance(sum, total)) {
      lines = spread(lines, switched, total - restored - sum) ?? lines
    }
  }
  return { vat, total: state.total, lines }
}
