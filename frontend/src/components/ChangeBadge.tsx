/**
 * A score movement between two reviews.
 *
 * Lifted out of `ResultsView` so the shared read-only view renders a delta the
 * same way rather than growing a second, subtly different one — the first draft
 * of `SharedView` used `text-sev-low` for an improvement, which is a severity
 * token pressed into service as a verdict colour and reads grey where every
 * other improvement in the app reads green.
 *
 * Colour is never the only carrier: the arrow shows direction visually and the
 * `sr-only` word says it outright, so the meaning survives a screen reader and
 * a monochrome print of the PDF alike.
 */
export function ChangeBadge({
  change,
  compact,
}: {
  change: number
  compact?: boolean
}) {
  const improved = change > 0
  const worsened = change < 0
  const tone = improved ? 'text-verdict-pass' : worsened ? 'text-sev-high' : 'text-ink-muted'
  const arrow = improved ? '▲' : worsened ? '▼' : '–'
  const wording = improved ? 'up' : worsened ? 'down' : 'unchanged'

  return (
    <span className={`tnum ${compact ? 't-caption' : 't-body'} font-semibold ${tone}`}>
      <span aria-hidden="true">{arrow} </span>
      <span className="sr-only">{wording} </span>
      {change === 0 ? '0.0' : Math.abs(change).toFixed(1)}
    </span>
  )
}
