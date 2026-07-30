import { CaveatPanel } from './CaveatPanel'
import type { IngestWarning } from '../types'

/**
 * Reasons to distrust how completely the design was read.
 *
 * Rendered in two places, deliberately: on the progress screen while the review is
 * still running, where the reviewer can still stop it and upload a better copy, and
 * at the top of the results page, where it is the first thing read and qualifies
 * every number below it.
 *
 * The panel itself is `CaveatPanel` at `caution` tone — the same component
 * `DataFidelity` uses, so the two cannot drift apart visually.
 */
export function IngestWarnings({
  warnings,
  className = '',
}: {
  warnings: IngestWarning[]
  className?: string
}) {
  if (warnings.length === 0) return null

  return (
    <section
      role="status"
      aria-label="Extraction warnings"
      data-testid="ingest-warnings"
      className={`animate-enter space-y-3 ${className}`}
    >
      {warnings.map((warning) => (
        <CaveatPanel
          key={warning.code}
          tone="caution"
          testId={`warning-${warning.code}`}
          title="Only part of this design could be read"
          body={warning.message}
          detail={warning.detail}
        />
      ))}
    </section>
  )
}
