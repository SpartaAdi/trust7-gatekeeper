import type { Severity } from '../types'

/**
 * Severity as a distinct shape, not just a colour.
 *
 * Triangle / diamond / circle stay distinguishable in greyscale and to the
 * ~8% of men with red-green colour blindness, for whom the high and medium
 * reds are close to identical.
 */
const SHAPES: Record<Severity, { path: string; className: string; label: string }> = {
  high: {
    path: 'M8 1.5 L14.5 13.5 L1.5 13.5 Z',
    className: 'fill-sev-high',
    label: 'High severity',
  },
  medium: {
    path: 'M8 1.5 L14.5 8 L8 14.5 L1.5 8 Z',
    className: 'fill-sev-medium',
    label: 'Medium severity',
  },
  low: {
    path: 'M8 2.5 A5.5 5.5 0 1 1 8 13.5 A5.5 5.5 0 1 1 8 2.5 Z',
    className: 'fill-sev-low',
    label: 'Low severity',
  },
}

interface Props {
  severity: Severity
  /** Decorative when the severity is already stated in adjacent text. */
  decorative?: boolean
}

export function SeverityMark({ severity, decorative = false }: Props) {
  const shape = SHAPES[severity] ?? SHAPES.low
  return (
    <svg
      viewBox="0 0 16 16"
      className={`size-3.5 shrink-0 ${shape.className}`}
      role={decorative ? 'presentation' : 'img'}
      aria-hidden={decorative || undefined}
      aria-label={decorative ? undefined : shape.label}
      focusable="false"
    >
      <path d={shape.path} />
    </svg>
  )
}
