/**
 * Score-to-maturity mapping.
 *
 * The band boundaries are a starting point, not a defined standard — adjust them
 * once real reviews show where designs actually cluster. They are here in one
 * place precisely so that's a one-line change.
 */

export type MaturityLabel =
  | 'Aware'
  | 'Managed'
  | 'Governed'
  | 'Certified'
  | 'Pioneering'

interface Band {
  min: number
  label: MaturityLabel
}

const BANDS: readonly Band[] = [
  { min: 90, label: 'Pioneering' },
  { min: 75, label: 'Certified' },
  { min: 60, label: 'Governed' },
  { min: 40, label: 'Managed' },
  { min: 0, label: 'Aware' },
]

export function maturityFor(score: number): MaturityLabel {
  return BANDS.find((band) => score >= band.min)?.label ?? 'Aware'
}

/** Bar fill colour. Deliberately one accent plus neutrals — no rainbow. */
export function scoreToneClass(score: number): string {
  if (score >= 75) return 'bg-verdict-pass'
  if (score >= 40) return 'bg-minfy-orange'
  return 'bg-sev-high'
}

export const SEVERITY_TEXT_CLASS: Record<string, string> = {
  high: 'text-sev-high',
  medium: 'text-sev-medium',
  low: 'text-sev-low',
}

export const SEVERITY_DOT_CLASS: Record<string, string> = {
  high: 'bg-sev-high',
  medium: 'bg-sev-medium',
  low: 'bg-sev-low',
}
