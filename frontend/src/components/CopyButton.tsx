import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'

interface CopyButtonProps {
  /** text placed on the clipboard */
  text: string
  label?: string
}

/** Copy-to-clipboard button that flips to "Copied" for 1.5s. */
export function CopyButton({ text, label = 'Copy' }: CopyButtonProps) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // clipboard unavailable (insecure context / denied): nothing to show
    }
  }

  return (
    <Button variant="outline" size="sm" onClick={copy}>
      {copied ? 'Copied' : label}
      <span className="sr-only" aria-live="polite">
        {copied ? 'Copied to clipboard' : ''}
      </span>
    </Button>
  )
}
