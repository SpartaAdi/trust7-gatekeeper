import { CaveatPanel } from './CaveatPanel'
import type { CaveatTone } from './CaveatPanel'
import type { IngestWarning, WarningCode } from '../types'

/**
 * Presentation per code, for the codes that are not "part of this was unreadable".
 *
 * The title used to be one hardcoded string for every warning, which was true of all
 * of them until `vision_minor_gaps` existed. That code means the opposite — the
 * diagram WAS read, with high confidence, and one detail was unclear — so the old
 * title contradicted the message underneath it, which is the same contradiction the
 * backend split was fixing one layer down.
 *
 * `neutral` for the same reason: `CaveatPanel` defines `caution` as "something is
 * wrong enough that a human should look", and a named sub-label on an otherwise
 * legible diagram is not that. A caution banner spent on it is a caution banner
 * ignored on a real one.
 */
const PRESENTATION: Partial<Record<WarningCode, { title: string; tone: CaveatTone }>> = {
  vision_minor_gaps: {
    title: 'A few details in the diagram were unclear',
    tone: 'neutral',
  },
}

const DEFAULT_PRESENTATION = {
  title: 'Only part of this design could be read',
  tone: 'caution' as CaveatTone,
}

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
      {warnings.map((warning) => {
        const { title, tone } = PRESENTATION[warning.code] ?? DEFAULT_PRESENTATION
        return (
          <CaveatPanel
            key={warning.code}
            tone={tone}
            testId={`warning-${warning.code}`}
            title={title}
            body={warning.message}
            detail={warning.detail}
          />
        )
      })}
    </section>
  )
}
