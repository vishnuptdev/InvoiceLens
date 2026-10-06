import {
  Alert,
  AlertDescription,
  AlertTitle,
} from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs'
import { ConfidenceCell, confidenceTier } from '@/components/ConfidenceCell'
import { CopyButton } from '@/components/CopyButton'
import {
  curlCommand,
  formatBytes,
  formatSeconds,
  isExtractionResult,
  type ApiResponse,
  type ExtractionResult,
} from '@/lib/api'

const tierVar: Record<ReturnType<typeof confidenceTier>, string> = {
  high: 'var(--stamp-green)',
  med: 'var(--stamp-amber)',
  low: 'var(--stamp-red)',
}

interface ResultPanelProps {
  response: ApiResponse | null
  /** name of the file that was (or is being) uploaded — used by the curl tab */
  fileName: string | null
  loading: boolean
  /** client-side or network failure, distinct from a 4xx API response */
  error: string | null
  /** bump on each arrival to replay the stamp-in animation */
  stampSeq: number
}

export function ResultPanel({ response, fileName, loading, error, stampSeq }: ResultPanelProps) {
  const ok = response !== null && response.status >= 200 && response.status < 300
  const result = ok && response && isExtractionResult(response.json) ? response.json : null
  const prettyJson = response
    ? response.json !== null
      ? JSON.stringify(response.json, null, 2)
      : response.body
    : ''

  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-xl font-bold">
          {result?.invoice.vendor_name || 'API response'}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {error && !loading && (
          <Alert variant="destructive" className="mb-4">
            <AlertTitle>Request failed</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <Tabs defaultValue="json">
          <TabsList className="w-full">
            <TabsTrigger value="json">JSON response</TabsTrigger>
            <TabsTrigger value="parsed">Parsed view</TabsTrigger>
            <TabsTrigger value="curl">Request (curl)</TabsTrigger>
          </TabsList>

          <TabsContent value="json" className="flex flex-col gap-3 pt-2">
            {loading && (
              <>
                <Skeleton className="h-5 w-64" />
                {Array.from({ length: 8 }, (_, i) => (
                  <Skeleton key={i} className="h-4 w-full" />
                ))}
                <p className="text-sm text-muted-foreground">
                  Waiting for API response (AI extraction can take up to a minute)
                </p>
              </>
            )}

            {!loading && !response && (
              <p className="text-sm text-muted-foreground">
                Run an extraction to see the exact JSON response.
              </p>
            )}

            {!loading && response && (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge
                    variant="outline"
                    className={
                      ok
                        ? 'border-current text-[color:var(--stamp-green)]'
                        : 'border-current text-[color:var(--stamp-red)]'
                    }
                  >
                    <span className="font-mono">{ok ? `${response.status} OK` : response.status}</span>
                  </Badge>
                  <span className="font-mono text-xs text-muted-foreground">POST /extract</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {formatSeconds(response.elapsedMs)}
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {formatBytes(new TextEncoder().encode(response.body).length)}
                  </span>
                  <span className="ml-auto">
                    <CopyButton text={prettyJson} />
                  </span>
                </div>

                <pre className="max-h-[480px] overflow-auto rounded-lg border border-border bg-muted/50 p-3 font-mono text-xs leading-relaxed">
                  {prettyJson}
                </pre>
              </>
            )}
          </TabsContent>

          <TabsContent value="parsed" className="pt-2">
            {result ? (
              <InvoiceCard result={result} stampSeq={stampSeq} />
            ) : (
              <p className="text-sm text-muted-foreground">
                {loading
                  ? 'Waiting for API response (AI extraction can take up to a minute)'
                  : response
                    ? 'The API returned an error, so there is nothing to parse.'
                    : 'Run an extraction to see the parsed invoice.'}
              </p>
            )}
          </TabsContent>

          <TabsContent value="curl" className="flex flex-col gap-3 pt-2">
            <div className="self-end">
              <CopyButton text={curlCommand(fileName ?? 'document.pdf')} label="Copy curl" />
            </div>
            <pre className="overflow-x-auto rounded-lg border border-border bg-muted/50 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap">
              {curlCommand(fileName ?? 'document.pdf')}
            </pre>
            <p className="text-sm text-muted-foreground">
              Same request works against any deployment.
            </p>
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  )
}

/** Badge text describing how the document text was obtained, or null when the
 * response carries no meaningful provenance (the "text" default, older cached
 * responses without the field). */
function textSourceLabel(result: ExtractionResult): string | null {
  switch (result.text_source) {
    case 'pdf-text':
      return 'pdf text'
    case 'docx':
      return 'docx'
    case 'txt':
      return 'txt'
    case 'ocr':
      return result.ocr_engine ? `ocr: ${result.ocr_engine}` : 'ocr'
    case 'vision':
      return 'vision (model read the page image)'
    case 'image':
      return 'image (no text found)'
    default:
      return result.text_source && result.text_source !== 'text' ? result.text_source : null
  }
}

/** Parsed invoice card: fields, confidence stamps, line items, warnings. */
function InvoiceCard({ result, stampSeq }: { result: ExtractionResult; stampSeq: number }) {
  const sourceLabel = textSourceLabel(result)
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">
          <span className="font-mono">{result.backend}</span>
        </Badge>
        {sourceLabel && (
          <Badge variant="outline">
            <span className="font-mono">{sourceLabel}</span>
          </Badge>
        )}
        <Badge variant="outline">
          {result.attempts} {result.attempts === 1 ? 'attempt' : 'attempts'}
        </Badge>
        <Badge
          variant="outline"
          className="border-current"
          style={{ color: tierVar[confidenceTier(result.overall_confidence)] }}
        >
          Confidence{' '}
          <span className="font-mono">{result.overall_confidence.toFixed(2)}</span>
        </Badge>
      </div>

      {result.warnings.length > 0 && (
        <Alert className="border-(--stamp-amber) text-(--stamp-amber)" role="status">
          <AlertTitle>Warnings</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">
              {result.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Field</TableHead>
            <TableHead>Value</TableHead>
            <TableHead>Confidence</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <FieldRow
            label="Invoice #"
            value={result.invoice.invoice_number}
            confidence={result.field_confidence.invoice_number}
            stampSeq={stampSeq}
            index={0}
          />
          <FieldRow
            label="Invoice date"
            value={result.invoice.invoice_date}
            confidence={result.field_confidence.invoice_date}
            stampSeq={stampSeq}
            index={1}
          />
          <FieldRow
            label="Due date"
            value={result.invoice.due_date}
            confidence={result.field_confidence.due_date}
            stampSeq={stampSeq}
            index={2}
          />
          <FieldRow
            label="Total"
            value={
              result.invoice.total_amount == null
                ? null
                : `${result.invoice.total_amount.toFixed(2)} ${result.invoice.currency ?? ''}`.trim()
            }
            mono
            confidence={result.field_confidence.total_amount}
            stampSeq={stampSeq}
            index={3}
          />
        </TableBody>
      </Table>

      {result.invoice.line_items.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold">Line items</h3>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Item</TableHead>
                <TableHead className="text-right">Qty</TableHead>
                <TableHead className="text-right">Unit price</TableHead>
                <TableHead className="text-right">Amount</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {result.invoice.line_items.map((item, i) => (
                <TableRow key={i}>
                  <TableCell>{item.description}</TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {item.quantity == null ? '—' : item.quantity.toFixed(2)}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {item.unit_price == null ? '—' : item.unit_price.toFixed(2)}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {item.amount == null ? '—' : item.amount.toFixed(2)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <Collapsible>
        <CollapsibleTrigger className="cursor-pointer text-sm font-medium text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-(--ring)">
          Raw JSON
        </CollapsibleTrigger>
        <CollapsibleContent>
          <pre className="mt-2 overflow-x-auto rounded-lg border border-border bg-muted/50 p-3 font-mono text-xs leading-relaxed">
            {JSON.stringify(result, null, 2)}
          </pre>
        </CollapsibleContent>
      </Collapsible>
    </div>
  )
}

function FieldRow({
  label,
  value,
  confidence,
  stampSeq,
  index,
  mono,
}: {
  label: string
  value: string | null
  confidence: number | undefined
  stampSeq: number
  index: number
  mono?: boolean
}) {
  return (
    <TableRow>
      <TableCell className="text-muted-foreground">{label}</TableCell>
      <TableCell className={mono ? 'font-mono tabular-nums' : undefined}>
        {value || '—'}
      </TableCell>
      <TableCell>
        <ConfidenceCell
          value={confidence ?? null}
          index={index}
          stampKey={`${stampSeq}-${index}`}
        />
      </TableCell>
    </TableRow>
  )
}
