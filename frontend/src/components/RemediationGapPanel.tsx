import { CaveatPanel } from './CaveatPanel'
import { remediationTotallyMissing, type RemediationGap } from '../types'

/**
 * "Some of the roadmap has no guidance in it" — said once, at the top.
 *
 * This exists because of a real run. The remediate stage returned 0 of 25 open
 * findings, its retry returned 0 of 25 again, and the review was stored and served
 * looking exactly like a normal one. Server-side there were two log lines. On the
 * page there was nothing: every roadmap row read "No remediation text was generated
 * for this check.", and the only page-level status was the data-fidelity panel
 * saying the diagram had been read at 100% — which reads as reassurance.
 *
 * The per-row message was honest and still is. What was missing is that a reader
 * scrolling a roadmap has to notice the same sentence sixteen times and infer a
 * systemic failure from it, and a reader who does not scroll never learns at all.
 *
 * Two other consequences are named here rather than left to be discovered, because
 * both are silent and both mislead in the direction of looking complete:
 *
 * - "Copy fix-it prompt" falls back to the finding TITLE when there is no
 *   remediation, so it produces a prompt that looks finished and carries no
 *   guidance. That is the artefact most likely to leave this app and land in
 *   someone else's editor.
 * - The roadmap's phases come from `remediation_effort`, which is blank whenever
 *   the text is. Its documented fallback files a blank-effort high-severity finding
 *   as Immediate — correct when one entry is missing, misleading when they all are,
 *   because "Immediate" then reflects an absent estimate rather than a cheap fix.
 *
 * `caution`, not `neutral`: unlike the fidelity numbers this is not a measurement to
 * weigh, it is a part of the deliverable that did not get produced.
 */
export function RemediationGapPanel({
  gap,
  className = '',
}: {
  gap?: RemediationGap
  className?: string
}) {
  if (!gap || gap.without_guidance === 0) return null

  const total = remediationTotallyMissing(gap)
  const n = gap.without_guidance
  const noun = n === 1 ? 'action has' : 'actions have'

  return (
    <section
      /* `status`, not `alert`, and the same as the sibling caveat panels: the
         review succeeded and its findings are usable. Announcing this at alert
         level puts it in the same class as "the pipeline crashed", and a reader
         who learns to dismiss one dismisses both. */
      role="status"
      aria-label="Remediation guidance"
      data-testid="remediation-gap"
      data-total={total ? 'true' : 'false'}
      className={`animate-enter ${className}`}
    >
      <CaveatPanel
        tone="caution"
        testId="remediation-gap-panel"
        title={
          total
            ? 'No remediation guidance was generated for this review'
            : `${n} of ${gap.open_findings} ${noun} no remediation guidance`
        }
        body={
          total ? (
            <>
              The findings and the scores below are complete and unaffected — every
              check was evaluated. What is missing is the guidance on what to change:
              the model returned nothing on the first attempt and nothing on the
              automatic retry, so every action in the roadmap is empty. Re-running
              the review usually produces it.{' '}
              <strong className="font-medium text-ink">
                Nothing has been invented to fill the gap.
              </strong>{' '}
              Note that “Copy fix-it prompt” falls back to the finding titles here,
              and that the roadmap's phases are grouped from an effort estimate that
              was also not returned.
            </>
          ) : (
            <>
              Those findings are shown in the roadmap with their titles and evidence,
              and marked as having no guidance rather than being given text the model
              did not write. The scores are unaffected. Re-running the review usually
              fills the gaps.
            </>
          )
        }
        // The ids, so the gap is checkable rather than a number to take on trust —
        // the same reason the fidelity panels show their measurements.
        detail={
          gap.check_ids.length > 0
            ? gap.check_ids.slice(0, 12).join(', ') +
              (gap.check_ids.length > 12 ? `, +${gap.check_ids.length - 12} more` : '')
            : undefined
        }
      />
    </section>
  )
}
