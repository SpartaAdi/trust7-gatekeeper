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

export interface MaturityTier {
  label: MaturityLabel
  /** Inclusive lower bound — the score at which this tier starts. */
  min: number
  /**
   * Exclusive upper bound: the score at which the next tier starts, or `null` for
   * the top tier, which has none.
   */
  below: number | null
  /** Pre-formatted for display, e.g. "75–90". See `MATURITY_BOUND_NOTE`. */
  range: string
}

/**
 * The bands as a displayable scale, highest first.
 *
 * DERIVED from `BANDS` above rather than written out again: the boundaries live in
 * exactly one place, so a tooltip explaining the scale cannot drift from the
 * function that assigns it. No range here is invented.
 *
 * Ranges are written `min–below` with the upper end exclusive, because
 * `maturityFor` compares `score >= min` against real (non-integer) scores — so
 * writing "75–89" would be wrong for a score of 89.4, which is Certified.
 */
export const MATURITY_SCALE: readonly MaturityTier[] = BANDS.map((band, index) => {
  const above = BANDS[index - 1]
  const below = above?.min ?? null
  return {
    label: band.label,
    min: band.min,
    below,
    range: below === null ? `${band.min}–100` : `${band.min}–${below}`,
  }
})

/** Companion to `MaturityTier.range`, so the exclusive upper bound is stated. */
export const MATURITY_BOUND_NOTE =
  'Upper bound is exclusive — the next tier starts at that score.'

/** Bar fill colour. Deliberately one accent plus neutrals — no rainbow. */
export function scoreToneClass(score: number): string {
  if (score >= 75) return 'bg-verdict-pass'
  if (score >= 40) return 'bg-minfy-indigo'
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
