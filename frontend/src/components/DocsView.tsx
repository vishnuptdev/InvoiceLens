import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { CopyButton } from '@/components/CopyButton'

// Illustrative clean AI-backend run: fields match sample_docs/sample_invoice.pdf,
// scores/backend are what the Anthropic path reports for the same document.
const QUICK_START_JSON = `{
  "document_id": "inline",
  "filename": "invoice.pdf",
  "invoice": {
    "vendor_name": "Acme Robotics Inc.",
    "invoice_number": "INV-10234",
    "invoice_date": "03/14/2026",
    "due_date": "04/13/2026",
    "total_amount": 1005.5,
    "currency": "USD",
    "line_items": [
      {
        "description": "Widget assembly (Model X)",
        "quantity": 10,
        "unit_price": 45.0,
        "amount": 450.0
      },
      {
        "description": "Custom bracket fabrication",
        "quantity": 4,
        "unit_price": 120.0,
        "amount": 480.0
      },
      {
        "description": "Rush shipping fee",
        "quantity": 1,
        "unit_price": 75.5,
        "amount": 75.5
      }
    ]
  },
  "field_confidence": {
    "vendor_name": 1.0,
    "invoice_number": 1.0,
    "invoice_date": 1.0,
    "due_date": 1.0,
    "total_amount": 1.0,
    "currency": 0.91,
    "line_items": 1.0
  },
  "overall_confidence": 0.987,
  "backend": "Atria-Dawn-Preview",
  "attempts": 1,
  "warnings": [],
  "text_source": "pdf-text",
  "ocr_engine": null
}`

const ERROR_JSON = `{
  "detail": "schema validation failed after 3 attempts: 2 validation errors for Invoice",
  "warnings": [
    "attempt 1: validation failed (2 error(s)), repairing and retrying",
    "attempt 2: validation failed (1 error(s)), repairing and retrying"
  ]
}`

function CodeSample({ code }: { code: string }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="self-end">
        <CopyButton text={code} />
      </div>
      <pre className="overflow-x-auto rounded-lg border border-border bg-muted/50 p-3 font-mono text-xs leading-relaxed">
        {code}
      </pre>
    </div>
  )
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3" aria-labelledby={id}>
      <h2 id={id} className="font-heading text-xl font-bold">
        {title}
      </h2>
      {children}
    </section>
  )
}

function Path({ children }: { children: string }) {
  return <code className="font-mono text-[0.85em]">{children}</code>
}

export function DocsView() {
  const origin = window.location.origin
  const curlExample = `curl -X POST ${origin}/extract -F "file=@invoice.pdf"`

  return (
    <div className="mx-auto flex max-w-[760px] flex-col gap-10 px-6 py-8 text-[15px] leading-relaxed">
      <header className="flex flex-col gap-2">
        <h1 className="font-heading text-3xl font-bold">InvoiceLens documentation</h1>
        <p className="text-muted-foreground">
          Everything you need to integrate the invoice-extraction API. No external files
          required — this page is the reference.
        </p>
      </header>

      <Section id="what" title="What this is">
        <p>
          <code className="font-mono">InvoiceLens</code> is an HTTP API that turns messy
          invoice documents — PDF, DOCX, plain text, or an image of one (PNG, JPG, WebP,
          BMP, TIFF) — into structured, schema-validated
          JSON, with a confidence score for every field it reports. You send a file, you get
          back one <Path>invoice</Path> object plus metadata about how much of it the service
          actually trusts.
        </p>
        <p>
          Two extraction backends run behind the same contract. When AI credentials are
          configured (an <Path>ANTHROPIC_API_KEY</Path> or <Path>ANTHROPIC_AUTH_TOKEN</Path>{' '}
          in the server environment) the service calls an Anthropic-compatible LLM, which
          handles odd layouts and unlabeled totals well. With no credentials — or when the AI
          path is disabled — a deterministic offline heuristic (regex and rule-based) answers
          the same request in milliseconds, with no network access. Your integration code does
          not change between the two.
        </p>
        <p>
          Whatever the backend returns, a validation layer checks it against the invoice
          schema and repairs dirty values before answering: <Path>"$1,234.56"</Path> becomes{' '}
          <Path>1234.56</Path>, a missing currency falls back to <Path>"USD"</Path>. If the
          first pass is invalid, the layer repairs and retries, up to three attempts; only if
          all three fail does the request turn into a 422.
        </p>
      </Section>

      <Section id="use-cases" title="Use cases">
        <p>Reach for this API whenever a workflow needs document → structured data:</p>
        <ul className="list-disc pl-5">
          <li>Accounts-payable automation: match, approve and post invoices without a human reading them.</li>
          <li>Killing manual invoice data entry in back-office tooling.</li>
          <li>Expense pipeline ingestion: normalise supplier paperwork into your warehouse.</li>
          <li>Vendor spend analytics: query totals, dates and line items across thousands of PDFs.</li>
        </ul>
      </Section>

      <Section id="quick-start" title="Quick start">
        <p>
          One call does the whole job. Upload a file as multipart form data and read the JSON
          response — no document IDs, no polling, no session state.
        </p>
        <CodeSample code={curlExample} />
        <p>A successful call returns <Path>200</Path> and this shape:</p>
        <CodeSample code={QUICK_START_JSON} />
      </Section>

      <Section id="endpoints" title="Endpoints">
        <div className="overflow-x-auto rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Method</TableHead>
                <TableHead>Path</TableHead>
                <TableHead>Purpose</TableHead>
                <TableHead>Request</TableHead>
                <TableHead>Success</TableHead>
                <TableHead>Errors</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <EndpointRow
                method="POST"
                path="/extract"
                purpose="One-shot parse + extract, stateless"
                request='multipart field "file"'
                ok="200 ExtractionResult"
                err="422 {detail, warnings}"
              />
              <EndpointRow
                method="POST"
                path="/documents"
                purpose="Upload a document, store its text"
                request='multipart field "file"'
                ok="201 DocumentMetadata"
                err="422 {detail}"
              />
              <EndpointRow
                method="POST"
                path="/documents/{document_id}/extract"
                purpose="Extract a stored document"
                request='path param "document_id"'
                ok="200 ExtractionResult"
                err="404, 422"
              />
              <EndpointRow
                method="GET"
                path="/documents/{document_id}/extraction"
                purpose="Fetch a stored extraction result"
                request='path param "document_id"'
                ok="200 ExtractionResult"
                err="404"
              />
              <EndpointRow
                method="GET"
                path="/documents/{document_id}"
                purpose="Fetch document metadata"
                request='path param "document_id"'
                ok="200 DocumentMetadata"
                err="404"
              />
              <EndpointRow
                method="GET"
                path="/health"
                purpose="Liveness + what the process can read (OCR engine, vision availability)"
                request="none"
                ok="200 {status, ocr_engine, vision_for_images, …}"
                err="—"
              />
            </TableBody>
          </Table>
        </div>
        <p>
          The <Path>/documents</Path> flow keeps parsed text in an in-memory store, so it only
          works on a long-running server: an upload and its later extract call must land in the
          same process. On serverless hosts (Vercel, Lambda) use <Path>POST /extract</Path> —
          one request, nothing stored.
        </p>
      </Section>

      <Section id="schema" title="Response schema">
        <p>Fields on <Path>ExtractionResult</Path>:</p>
        <div className="overflow-x-auto rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Field</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Notes</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <SchemaRow field="document_id" type="string" note={`"inline" for POST /extract; the stored ID for the /documents flow`} />
              <SchemaRow field="filename" type="string" note="name as uploaded" />
              <SchemaRow field="invoice" type="object" note="see below" />
              <SchemaRow field="invoice.vendor_name" type="string | null" note="company name, best guess from the document" />
              <SchemaRow field="invoice.invoice_number" type="string | null" note="as printed, not normalised" />
              <SchemaRow field="invoice.invoice_date" type="string | null" note="raw string, e.g. 03/14/2026" />
              <SchemaRow field="invoice.due_date" type="string | null" note="raw string; null when the document has none" />
              <SchemaRow field="invoice.total_amount" type="number | null" note="float, currency symbols and thousands separators stripped" />
              <SchemaRow field="invoice.currency" type="string" note={`the only non-null field; defaults to "USD"`} />
              <SchemaRow field="invoice.line_items" type="array" note="empty array when none were found" />
              <SchemaRow field="invoice.line_items[].description" type="string" note="item text" />
              <SchemaRow field="invoice.line_items[].quantity" type="number | null" note="" />
              <SchemaRow field="invoice.line_items[].unit_price" type="number | null" note="" />
              <SchemaRow field="invoice.line_items[].amount" type="number | null" note="" />
              <SchemaRow field="field_confidence" type="object" note={`0–1 per field name, e.g. "invoice_number": 0.9`} />
              <SchemaRow field="overall_confidence" type="number" note="mean of field_confidence, rounded to 3 decimals" />
              <SchemaRow field="backend" type="string" note="which extractor produced this result" />
              <SchemaRow field="attempts" type="integer" note="validation passes needed; 1 means the first candidate was already valid" />
              <SchemaRow field="warnings" type="string[]" note="repair notes; empty on a clean run" />
              <SchemaRow
                field="text_source"
                type="string"
                note={`how the content was read: "pdf-text", "docx", "txt", "ocr" (local OCR), "vision" (the model read the page images), or "image" (nothing readable was found)`}
              />
              <SchemaRow
                field="ocr_engine"
                type="string | null"
                note={`the local OCR engine name (e.g. "rapidocr", "tesseract") when text_source is "ocr"; null otherwise`}
              />
            </TableBody>
          </Table>
        </div>
      </Section>

      <Section id="errors" title="Errors">
        <p>
          Errors are JSON with a human-readable <Path>detail</Path>; extraction failures also
          carry the <Path>warnings</Path> collected during repair attempts.
        </p>
        <ul className="list-disc pl-5">
          <li>
            <Path>422</Path> unsupported file type — extension is not one of{' '}
            <Path>.pdf .docx .txt .text .png .jpg .jpeg .webp .bmp .tif .tiff</Path>.
          </li>
          <li><Path>422</Path> uploaded file is empty.</li>
          <li>
            <Path>422</Path> no extractable text found in document — only possible for an
            image or scanned PDF when no local OCR engine is installed and no AI credentials
            are set, so neither route can read it; the <Path>detail</Path> explains both
            fixes.
          </li>
          <li>
            <Path>422</Path> schema validation exhausted after three attempts:
            <CodeSample code={ERROR_JSON} />
          </li>
          <li>
            <Path>404</Path> unknown <Path>document_id</Path>, or a document that has not been
            extracted yet — stateful <Path>/documents</Path> flow only.
          </li>
        </ul>
      </Section>

      <Section id="limits" title="Supported formats and limits">
        <ul className="list-disc pl-5">
          <li>
            Extensions accepted: <Path>.pdf</Path> <Path>.docx</Path> <Path>.txt</Path>{' '}
            <Path>.text</Path> <Path>.png</Path> <Path>.jpg</Path> <Path>.jpeg</Path>{' '}
            <Path>.webp</Path> <Path>.bmp</Path> <Path>.tif</Path> <Path>.tiff</Path>.
          </li>
          <li>
            PDFs are read from their text layer. A PDF with no text layer (a scan) and an
            image upload are read by a local OCR engine (RapidOCR or Tesseract, see{' '}
            <Path>OCR_ENGINE</Path>) with no network access; when none is installed — or its
            output is too poor to trust — the page images go to the model's vision input
            instead, when AI credentials are set. Only if neither route is available do you
            get a 422 "no extractable text".
          </li>
          <li>
            Text that came from OCR is trusted slightly less: per-field confidence is scaled
            by <Path>0.9</Path> and a warning says so (see <Path>text_source</Path> and{' '}
            <Path>ocr_engine</Path> on the response).
          </li>
          <li>Dates are returned exactly as the document prints them. Nothing is normalised to ISO 8601; parse them yourself if you need ordering.</li>
          <li>One schema today: invoices. Other document types need a schema and an extractor added server-side.</li>
          <li>AI responses can take up to a minute. Set a generous client timeout, or run the heuristic backend.</li>
        </ul>
      </Section>

      <Section id="confidence" title="Confidence semantics">
        <p>Confidence is per field, on a 0–1 scale, and it means different things per backend:</p>
        <ul className="list-disc pl-5">
          <li>
            Heuristic: <Path>0.9</Path> matched a labeled pattern ("Invoice #:"),{' '}
            <Path>0.6</Path> matched a loose fallback pattern, <Path>0.0</Path> nothing found.
          </li>
          <li>
            AI: the model self-reports each value, clamped into <Path>[0, 1]</Path>; fields the
            model forgot to score default to <Path>0.5</Path>.
          </li>
        </ul>
        <p>
          Practical guidance: treat anything below <Path>0.5</Path> as needs-review and route it
          to a human, and don't compare scores across backends — <Path>0.6</Path> from the
          heuristic means "guessed from a loose pattern", which is not the same statement a
          model's <Path>0.6</Path> makes.
        </p>
        <p>
          The <Path>backend</Path> field on every response says which extractor produced it:{' '}
          <Path>"heuristic-v1"</Path> (instant, offline), or the AI model name (for example{' '}
          <Path>"Atria-Dawn-Preview"</Path>). Which one answers is controlled server-side by the{' '}
          <Path>EXTRACTION_MODE</Path> environment variable — <Path>auto</Path> (heuristic fast
          path for clean documents, escalate to AI when confidence is low), <Path>ai</Path> (force
          AI), or <Path>fast</Path> (never call AI).
        </p>
        <p>
          Images and scanned PDFs have their own server-side controls. <Path>IMAGE_MODE</Path>{' '}
          decides how pixel input is read: <Path>auto</Path> (default — local OCR first, the
          model's vision input only to escalate when OCR is missing or unreliable),{' '}
          <Path>vision</Path> (always send the pixels to the model when AI credentials are
          set), or <Path>ocr</Path> (never call the model on images). <Path>OCR_ENGINE</Path>{' '}
          picks the local engine: <Path>auto</Path> (default — RapidOCR, then Tesseract),{' '}
          <Path>rapid</Path>, <Path>tesseract</Path>, or <Path>off</Path> (disable local OCR;
          image input then needs the vision path). Every response reports what actually
          happened in <Path>text_source</Path> and <Path>ocr_engine</Path>.
        </p>
      </Section>

      <footer className="flex flex-wrap items-center gap-3 border-t border-border pt-6">
        <span className="text-sm text-muted-foreground">
          Interactive API docs (Swagger UI, generated from the running server):
        </span>
        <Button variant="outline" size="sm" className="border-primary text-primary" render={<a href="/docs" target="_blank" rel="noreferrer" />}>
          /docs
        </Button>
        <Button variant="outline" size="sm" className="border-primary text-primary" render={<a href="/openapi.json" target="_blank" rel="noreferrer" />}>
          /openapi.json
        </Button>
      </footer>
    </div>
  )
}

function EndpointRow({
  method,
  path,
  purpose,
  request,
  ok,
  err,
}: {
  method: string
  path: string
  purpose: string
  request: string
  ok: string
  err: string
}) {
  return (
    <TableRow>
      <TableCell className="whitespace-nowrap font-mono text-xs font-semibold text-[color:var(--stamp-green)]">
        {method}
      </TableCell>
      <TableCell className="whitespace-nowrap font-mono text-xs">{path}</TableCell>
      <TableCell className="text-xs">{purpose}</TableCell>
      <TableCell className="text-xs">
        <code className="font-mono">{request}</code>
      </TableCell>
      <TableCell className="whitespace-nowrap font-mono text-xs">{ok}</TableCell>
      <TableCell className="whitespace-nowrap font-mono text-xs">{err}</TableCell>
    </TableRow>
  )
}

function SchemaRow({ field, type, note }: { field: string; type: string; note: string }) {
  return (
    <TableRow>
      <TableCell className="whitespace-nowrap font-mono text-xs">{field}</TableCell>
      <TableCell className="whitespace-nowrap font-mono text-xs text-muted-foreground">{type}</TableCell>
      <TableCell className="text-xs">{note}</TableCell>
    </TableRow>
  )
}
