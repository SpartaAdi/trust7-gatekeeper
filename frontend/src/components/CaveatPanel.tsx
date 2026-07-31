import type { ReactNode } from 'react'

/**
 * The one panel style for "here is something about this result you should know".
 *
 * Extracted from `IngestWarnings` so `DataFidelity` reuses it verbatim rather than
 * growing a second, nearly-identical panel that drifts. Both are the same kind of
 * statement — a caveat sitting beside a usable review — so they should look the
 * same, and the only way to guarantee that is for them to be the same component.
 *
 * `tone` is the one axis of difference:
 *
 * - `caution` (amber) — something is wrong enough that a human should look. Used by
 *   every ingest warning, and by a coverage figure under the review threshold.
 * - `neutral` (navy) — a measurement worth reporting that is not a problem. Used by
 *   a healthy coverage figure and by the grounding count, which describes the filter
 *   working rather than anything failing.
 *
 * Always `role="status"`, never `role="alert"`, on both tones. The review succeeded;
 * these qualify it. Announcing a caveat as an alert puts it in the same class as
 * "the pipeline crashed", and a reviewer who learns to dismiss one dismisses both.
 */
export type CaveatTone = 'caution' | 'neutral'

export function CaveatPanel({
  tone,
  title,
  body,
  detail,
  badge,
  footer,
  testId,
}: {
  tone: CaveatTone
  title: string
  body: ReactNode
  /** The measurements behind the claim. Shown, not hidden — see below. */
  detail?: string
  /**
   * A short status tag on the title line. Optional and presentational.
   *
   * Added for the AI-detection panel, whose SIX verdicts collapsed into this
   * component's two tones — `present`, `likely`, `absent`, `denied` and `not_run` all
   * rendered as the same navy block, distinguishable only by reading a full sentence.
   * The tag names the verdict in one or two words so the state is scannable, without
   * inventing a third tone or a second panel style. Callers that have one state pass
   * nothing and are unchanged.
   */
  badge?: ReactNode
  /**
   * Content that belongs to the panel rather than after it — a disclosure and what it
   * reveals. Rendered inside the block, so it inherits the fill and the left rule.
   *
   * Also added for the AI-detection panel: its evidence disclosure used to sit on the
   * page background BELOW the block, which read as unrelated content rather than as
   * the panel's own supporting detail.
   */
  footer?: ReactNode
  testId?: string
}) {
  const caution = tone === 'caution'
  const stroke = caution ? 'stroke-sev-medium' : 'stroke-minfy-navy'
  const fill = caution ? 'fill-sev-medium' : 'fill-minfy-navy'

  return (
    <div
      data-testid={testId}
      data-tone={tone}
      className={`flex gap-3 border-l-2 bg-surface-sunken px-4 py-3.5 ${
        caution ? 'border-sev-medium' : 'border-minfy-navy'
      }`}
    >
      {caution ? (
        /* A bang in a triangle, outlined rather than filled: it sits beside a
           usable result, so it should read as a caveat and not as a stop sign. */
        <svg viewBox="0 0 16 16" aria-hidden="true" className="mt-0.5 size-4 shrink-0">
          <path
            d="M8 2 L14.5 13.5 L1.5 13.5 Z"
            className={`fill-none ${stroke}`}
            strokeWidth="1.3"
            strokeLinejoin="round"
          />
          <path d="M8 6.2 V9.4" className={stroke} strokeWidth="1.3" strokeLinecap="round" />
          <circle cx="8" cy="11.4" r="0.75" className={fill} />
        </svg>
      ) : (
        /* An outlined "i": a figure being reported, not a problem being raised. */
        <svg viewBox="0 0 16 16" aria-hidden="true" className="mt-0.5 size-4 shrink-0">
          <circle cx="8" cy="8" r="6.25" className={`fill-none ${stroke}`} strokeWidth="1.3" />
          <circle cx="8" cy="5.2" r="0.75" className={fill} />
          <path d="M8 7.4 V11.2" className={stroke} strokeWidth="1.3" strokeLinecap="round" />
        </svg>
      )}
      <div className="min-w-0 flex-1">
        {/* Tag first in the DOM but baseline-aligned beside the title, so a screen
            reader hears the state before the sentence that elaborates it. */}
        <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          {badge}
          <p className="t-heading">{title}</p>
        </div>
        <p className="t-caption mt-1.5 break-words text-ink-muted">{body}</p>
        {detail && (
          /* Shown, not tucked behind a disclosure. It is the measurement the claim
             rests on, and a number a reviewer cannot check is one they have to take
             on trust. */
          <p className="t-caption mt-2 break-words font-mono text-ink-faint">{detail}</p>
        )}
        {footer}
      </div>
    </div>
  )
}
