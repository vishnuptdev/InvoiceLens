import { useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'

const ACCEPT = '.pdf,.docx,.txt,.text,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff'

interface UploadPanelProps {
  file: File | null
  loading: boolean
  onFileChange: (file: File | null) => void
  onExtract: () => void
}

function formatSize_kb(size: number): string {
  return `${(size / 1024).toFixed(1)} KB`
}

export function UploadPanel({ file, loading, onFileChange, onExtract }: UploadPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">Upload invoice</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div
          role="button"
          tabIndex={0}
          aria-label="Choose invoice file"
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              inputRef.current?.click()
            }
          }}
          onDragOver={(e) => {
            e.preventDefault()
            setDragOver(true)
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragOver(false)
            const dropped = e.dataTransfer.files[0]
            if (dropped) onFileChange(dropped)
          }}
          className={cn(
            'flex cursor-pointer flex-col items-center justify-center gap-1 rounded-[10px] border-2 border-dashed border-border bg-card px-4 py-8 text-center',
            'hover:border-[color:var(--stamp-green)] hover:bg-[color:var(--dropzone-hover-bg)]',
            dragOver &&
              'border-[color:var(--stamp-green)] bg-[color:var(--dropzone-hover-bg)]',
          )}
        >
          <span className="text-sm font-medium">Drop a file here, or click to browse</span>
          <span className="text-sm text-muted-foreground">
            PDF, DOCX, plain text, or image (PNG, JPG, WebP, BMP, TIFF)
          </span>
        </div>

        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          aria-label="Invoice file"
          className="sr-only"
          onChange={(e) => {
            const selected = e.target.files?.[0] ?? null
            onFileChange(selected)
            e.target.value = ''
          }}
        />

        {file && (
          <div className="flex items-center justify-between gap-2 rounded-lg border border-border bg-muted/50 px-3 py-2">
            <span className="truncate text-sm">
              {file.name}
              <span className="ml-2 font-mono text-xs text-muted-foreground">
                {formatSize_kb(file.size)}
              </span>
            </span>
            <button
              type="button"
              aria-label={`Remove ${file.name}`}
              onClick={() => onFileChange(null)}
              className="shrink-0 rounded-md p-1 text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--ring)]"
            >
              &times;
            </button>
          </div>
        )}

        <Button
          className="w-full"
          disabled={!file || loading}
          onClick={onExtract}
        >
          {loading ? 'Extracting…' : 'Extract invoice'}
        </Button>
      </CardContent>
    </Card>
  )
}
