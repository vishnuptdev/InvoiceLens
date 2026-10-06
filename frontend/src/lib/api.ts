export interface LineItem {
  description: string
  quantity: number | null
  unit_price: number | null
  amount: number | null
}

export interface Invoice {
  vendor_name: string | null
  invoice_number: string | null
  invoice_date: string | null
  due_date: string | null
  total_amount: number | null
  currency: string | null
  line_items: LineItem[]
}

export interface ExtractionResult {
  document_id: string
  filename: string
  invoice: Invoice
  field_confidence: Record<string, number>
  overall_confidence: number
  backend: string
  attempts: number
  warnings: string[]
  /** How the text was read: "pdf-text" | "docx" | "txt" | "ocr" | "image" |
   * "vision". Optional only because older cached responses predate the field. */
  text_source?: string
  /** Local OCR engine name when text_source is "ocr", otherwise null. */
  ocr_engine?: string | null
}

/** Every API call resolves to this — including 4xx — so the playground can
 * always show status, timing and the exact response body. */
export interface ApiResponse {
  status: number
  elapsedMs: number
  /** raw text of the response body */
  body: string
  /** parsed body when it is JSON, otherwise null */
  json: unknown
}

export function isExtractionResult(json: unknown): json is ExtractionResult {
  if (typeof json !== 'object' || json === null) return false
  const o = json as Record<string, unknown>
  return typeof o.invoice === 'object' && o.invoice !== null
}

export async function extractFile(file: File): Promise<ApiResponse> {
  const form = new FormData()
  form.append('file', file)

  const started = performance.now()
  // No timeout: the AI backend can legitimately take up to a minute.
  const res = await fetch('/extract', { method: 'POST', body: form })
  const body = await res.text()

  let json: unknown = null
  try {
    json = JSON.parse(body)
  } catch {
    // non-JSON body: keep the raw text
  }

  return { status: res.status, elapsedMs: Math.round(performance.now() - started), body, json }
}

export function formatBytes(size: number): string {
  return size < 1024 ? `${size} B` : `${(size / 1024).toFixed(1)} KB`
}

export function formatSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`
}

export function curlCommand(fileName: string): string {
  return `curl -X POST ${window.location.origin}/extract -F "file=@${fileName}"`
}
