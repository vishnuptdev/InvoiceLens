import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

const ENDPOINTS: Array<[string, string, string]> = [
  ['POST', '/extract', 'one-shot parse + extract (stateless)'],
  ['POST', '/documents', 'upload, returns document_id'],
  ['POST', '/documents/{id}/extract', 'extract stored doc'],
  ['GET', '/documents/{id}/extraction', 'fetch result'],
  ['GET', '/health', 'liveness'],
]

/** Small cheat-sheet of the API surface, under the upload panel. */
export function EndpointCard() {
  return (
    <Card>
      <CardHeader className="grid-cols-[1fr_auto] items-center">
        <CardTitle className="text-lg">Endpoints</CardTitle>
        <Button variant="outline" size="sm" render={<a href="/docs" target="_blank" rel="noreferrer" />}>
          API docs
        </Button>
      </CardHeader>
      <CardContent>
        <ul className="flex flex-col gap-2 text-xs">
          {ENDPOINTS.map(([method, path, purpose]) => (
            <li key={`${method} ${path}`} className="flex flex-col gap-0.5">
              <span className="font-mono">
                <span className="font-semibold text-[color:var(--stamp-green)]">{method}</span>{' '}
                {path}
              </span>
              <span className="text-muted-foreground">{purpose}</span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}
