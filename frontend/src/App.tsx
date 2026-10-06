import { useState } from 'react'

import { DocsView } from '@/components/DocsView'
import { EndpointCard } from '@/components/EndpointCard'
import { ResultPanel } from '@/components/ResultPanel'
import { UploadPanel } from '@/components/UploadPanel'
import { Button } from '@/components/ui/button'
import { extractFile, type ApiResponse } from '@/lib/api'
import { cn } from '@/lib/utils'

const ALLOWED_EXTENSIONS = [
  '.pdf', '.docx', '.txt', '.text',
  '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff',
]

type View = 'demo' | 'docs'

function App() {
  const [view, setView] = useState<View>('demo')
  const [file, setFile] = useState<File | null>(null)
  const [loading, setLoading] = useState(false)
  const [response, setResponse] = useState<ApiResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [stampSeq, setStampSeq] = useState(0)

  const handleFileChange = (next: File | null) => {
    if (
      next &&
      !ALLOWED_EXTENSIONS.some((ext) => next.name.toLowerCase().endsWith(ext))
    ) {
      setError(
        'Unsupported file type. Please upload a PDF, DOCX, plain text, or image file (PNG, JPG, WebP, BMP, TIFF).'
      )
      return
    }
    setError(null)
    setFile(next)
  }

  const handleExtract = async () => {
    if (!file) return
    setLoading(true)
    setError(null)
    try {
      const res = await extractFile(file)
      setResponse(res)
      if (res.status === 200) setStampSeq((s) => s + 1)
    } catch (err) {
      setResponse(null)
      setError(err instanceof Error ? err.message : 'Request failed.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-dvh">
      <header className="border-b border-border bg-card/80">
        <div className="mx-auto flex max-w-[1100px] flex-wrap items-center gap-x-4 gap-y-2 px-6 py-3">
          <span className="font-heading text-lg font-bold text-foreground">
            InvoiceLens
          </span>

          <nav aria-label="View" className="flex items-center gap-1">
            {(['demo', 'docs'] as const).map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={view === v}
                onClick={() => setView(v)}
                className={cn(
                  'rounded-lg border px-2.5 py-1 text-sm font-medium capitalize transition-colors',
                  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-(--ring)',
                  view === v
                    ? 'border-primary bg-primary text-primary-foreground'
                    : 'border-transparent text-muted-foreground hover:text-foreground'
                )}
              >
                {v}
              </button>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="border-primary text-primary"
              render={<a href="/docs" target="_blank" rel="noreferrer" />}
            >
              API docs
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="border-primary text-primary"
              render={<a href="/openapi.json" target="_blank" rel="noreferrer" />}
            >
              OpenAPI schema
            </Button>
          </div>
        </div>
      </header>

      {view === 'demo' ? (
        <>
          <p className="mx-auto max-w-[1100px] px-6 pt-6 text-sm text-muted-foreground">
            Try the extraction API — upload a document and get the exact JSON response an
            integrator would receive.
          </p>
          <main className="mx-auto grid max-w-[1100px] grid-cols-1 gap-6 px-6 py-6 md:grid-cols-[380px_1fr]">
            <div className="flex flex-col gap-6">
              <UploadPanel
                file={file}
                loading={loading}
                onFileChange={handleFileChange}
                onExtract={handleExtract}
              />
              <EndpointCard />
            </div>
            <ResultPanel
              response={response}
              fileName={file?.name ?? null}
              loading={loading}
              error={error}
              stampSeq={stampSeq}
            />
          </main>
        </>
      ) : (
        <main>
          <DocsView />
        </main>
      )}
    </div>
  )
}

export default App
