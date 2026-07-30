import type { IngestWarning } from '../types'

/**
 * Reasons to distrust how completely the design was read.
 *
 * Rendered in two places, deliberately: on the progress screen while the review is
 * still running, where the reviewer can still stop it and upload a better copy, and
 * at the top of the results page, where it is the first thing read and qualifies
 * every number below it.
 *
 * Amber, not red, and `role="status"` rather than `role="alert"`. The review
 * succeeded — these say it may have been scored on less of the design than the
 * submitter thinks, which is a caveat on a result rather than a failure to produce
 * one. Styling it as an error would put it in the same visual class as "the pipeline
 * crashed", and a reviewer who learns to dismiss one will dismiss the other.
 *
 * `detail` is shown, not tucked behind a disclosure. It is the measurement the
 * warning rests on — "3 of 40 pages had text" — and a warning a reviewer cannot
 * check is a warning they have to take on trust.
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
        <div
          key={warning.code}
          data-testid={`warning-${warning.code}`}
          className="flex gap-3 border-l-2 border-sev-medium bg-surface-sunken px-4 py-3.5"
        >
          {/* A bang in a triangle, outlined rather than filled: it sits beside a
              usable result, so it should read as a caveat and not as a stop sign. */}
          <svg
            viewBox="0 0 16 16"
            aria-hidden="true"
            className="mt-0.5 size-4 shrink-0"
          >
            <path
              d="M8 2 L14.5 13.5 L1.5 13.5 Z"
              className="fill-none stroke-sev-medium"
              strokeWidth="1.3"
              strokeLinejoin="round"
            />
            <path
              d="M8 6.2 V9.4"
              className="stroke-sev-medium"
              strokeWidth="1.3"
              strokeLinecap="round"
            />
            <circle cx="8" cy="11.4" r="0.75" className="fill-sev-medium" />
          </svg>
          <div className="min-w-0">
            <p className="t-heading">Only part of this design could be read</p>
            <p className="t-caption mt-1 break-words text-ink-muted">
              {warning.message}
            </p>
            {warning.detail && (
              <p className="t-caption mt-1.5 break-words font-mono text-ink-faint">
                {warning.detail}
              </p>
            )}
          </div>
        </div>
      ))}
    </section>
  )
}
