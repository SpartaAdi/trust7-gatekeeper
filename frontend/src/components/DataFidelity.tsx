import { CaveatPanel } from './CaveatPanel'
import { COVERAGE_REVIEW_THRESHOLD, type DataFidelity as Fidelity } from '../types'

/**
 * The three data-fidelity numbers, as three separate panels.
 *
 * THREE PANELS, NEVER ONE FIGURE. The temptation is a single "extraction accuracy"
 * badge, and it would be a lie: one of these is an exact ratio, one is an estimate
 * against a second fallible reader, and one is a count that says nothing about what
 * survived. Averaging them would hide exactly the differences a reviewer needs.
 * That is why each renders its own panel with its own wording, and why nothing here
 * computes across them.
 *
 * The panel is `CaveatPanel` — the same component `IngestWarnings` uses — so this
 * introduces no new panel style. Tone is the only variable, and only the STRUCTURAL
 * figure can set it: `caution` when that exact ratio falls under the review
 * threshold, `neutral` everywhere else. The OCR proxy is always neutral because it
 * is an estimate, and the grounding count is always neutral because a caught claim
 * is the filter working — see `review_recommended` in backend/schema.py.
 */
export function DataFidelity({
  fidelity,
  className = '',
}: {
  fidelity?: Fidelity
  className?: string
}) {
  const structural = fidelity?.structural ?? null
  const ocr = fidelity?.ocr_proxy ?? null
  const grounding = fidelity?.grounding ?? null

  // The grounding count is shown only when it caught something. A panel reading
  // "0 ungrounded claims caught" on every clean review is noise, and noise is what
  // teaches people to stop reading these panels.
  const showGrounding =
    grounding !== null && (grounding.removed > 0 || grounding.incomplete > 0)

  if (!structural && !ocr && !showGrounding) return null

  return (
    <section
      role="status"
      aria-label="Data fidelity"
      data-testid="data-fidelity"
      className={`animate-enter space-y-3 ${className}`}
    >
      {structural && (
        <CaveatPanel
          tone={structural.percent < COVERAGE_REVIEW_THRESHOLD ? 'caution' : 'neutral'}
          testId="fidelity-structural"
          title={`Diagram structure read: ${structural.percent}% of elements`}
          body={
            structural.percent < COVERAGE_REVIEW_THRESHOLD ? (
              <>
                Under {COVERAGE_REVIEW_THRESHOLD}% of the diagram's elements reached
                the review, so <strong>this review should be checked by hand</strong>{' '}
                against the source diagram.
                {structural.dropped.length > 0 && (
                  <> What did not come through: {structural.dropped.join('; ')}.</>
                )}
              </>
            ) : (
              <>
                An exact count against the draw.io file's own XML — this figure is
                measured, not estimated, and no model was involved in producing it.
              </>
            )
          }
          detail={
            `${structural.parsed_elements} of ${structural.total_elements} diagram ` +
            `elements parsed (components + connections + notes)`
          }
        />
      )}

      {ocr && !ocr.available && (
        /* Absent, not zero. A 0% here would read as "the vision model missed
           everything", which is a claim about the model rather than about tooling. */
        <CaveatPanel
          tone="neutral"
          testId="fidelity-ocr-unavailable"
          title="Diagram text coverage: not measured (estimate unavailable)"
          body={
            <>
              This figure is an <strong>estimate</strong> produced by re-reading the
              image with a separate OCR engine. It was not available for this review,
              so no coverage figure is reported — this is not a score of zero.
            </>
          }
          detail={ocr.unavailable_reason}
        />
      )}

      {ocr && ocr.available && (
        <CaveatPanel
          /*
            ALWAYS neutral, whatever the figure. This panel deliberately does not
            carry the caution tone at any percentage: the tone IS the automated
            recommendation, and an estimate that reads 83% on a perfectly-extracted
            diagram must not pull that lever. See review_recommended in schema.py.
          */
          tone="neutral"
          testId="fidelity-ocr"
          title={`Diagram text coverage: ~${ocr.percent}% (estimated)`}
          body={
            <>
              {/* The word "estimate" appears in the title AND here AND in the
                  detail line. Deliberately repeated: this is the number most likely
                  to be quoted out of context as though it were measured. */}
              An <strong>estimate, not a measurement</strong>. A separate OCR pass
              read the image and this is the share of words it found that also appear
              in the extracted design. There is no ground truth for what an image
              really contains, so a low figure means the two readers disagree — not
              which one is right, and OCR invents words as often as it misses them.
              {/*
                No "review recommended" clause at any figure, deliberately. A title,
                a legend or a region label is text in the image and not a component,
                so a diagram extracted perfectly still scores well under any useful
                threshold — an automated flag driven by this would fire on correct
                work. It is here to be weighed, not to gate.
              */}{' '}
              A low figure is worth a look, not a verdict; it does not on its own
              mean anything was missed.
              {ocr.sample_unmatched.length > 0 && (
                <>
                  {' '}
                  Words OCR read that the extraction does not contain, which may be
                  missed labels or may be OCR noise: {ocr.sample_unmatched.join(', ')}.
                </>
              )}
            </>
          }
          detail={
            `estimated proxy · ${ocr.matched_tokens} of ${ocr.ocr_tokens} ` +
            `OCR-read words found in the extracted design`
          }
        />
      )}

      {showGrounding && grounding && (
        <CaveatPanel
          tone="neutral"
          testId="fidelity-grounding"
          title={
            `${grounding.removed} ungrounded ${
              grounding.removed === 1 ? 'claim' : 'claims'
            } caught and removed`
          }
          body={
            <>
              {/* Says what was removed and stops. It deliberately does NOT say
                  anything about what remains: a claim whose quote was verifiable is
                  not thereby correct, and "3 of 5 removed" must never be shown as
                  "60% grounded". */}
              Recommendations whose supporting quote could not be found in the
              context you submitted were discarded before you saw them. This counts
              what was removed — it is not a confidence figure for the
              recommendations that remain.
              {grounding.removed_for.length > 0 && (
                <> Removed claims concerned: {grounding.removed_for.join(', ')}.</>
              )}
            </>
          }
          detail={
            `${grounding.removed} removed for an unverifiable quote, ` +
            `${grounding.incomplete} for a missing field, out of ` +
            `${grounding.checked} the model returned`
          }
        />
      )}
    </section>
  )
}
