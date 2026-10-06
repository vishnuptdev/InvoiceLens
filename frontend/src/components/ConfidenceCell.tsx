import { Progress } from '@/components/ui/progress'

export type ConfidenceTier = 'high' | 'med' | 'low'

export function confidenceTier(value: number): ConfidenceTier {
  if (value >= 0.8) return 'high'
  if (value >= 0.5) return 'med'
  return 'low'
}

const tierClass: Record<ConfidenceTier, string> = {
  high: 'conf-high',
  med: 'conf-med',
  low: 'conf-low',
}

interface ConfidenceCellProps {
  value: number | null
  /** 40ms stagger per row, applied only on results arrival */
  index?: number
  /** change this key to replay the stamp-in animation */
  stampKey?: string | number
}

export function ConfidenceCell({ value, index = 0, stampKey }: ConfidenceCellProps) {
  if (value == null) {
    return <span className="font-mono text-muted-foreground">&mdash;</span>
  }

  return (
    <span
      key={stampKey}
      className={`stamp-in inline-flex items-center gap-2 ${tierClass[confidenceTier(value)]}`}
      style={{ animationDelay: `${index * 40}ms` }}
    >
      <Progress value={value * 100} className="w-[90px]" aria-label="Field confidence" />
      <span className="font-mono text-xs tabular-nums">{value.toFixed(2)}</span>
    </span>
  )
}
