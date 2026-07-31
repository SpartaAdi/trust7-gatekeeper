import { useEffect, useState } from 'react'

import { getReviewVersions } from '../api'
import type { ReviewResult, ReviewVersion } from '../types'

/**
 * "This is version N, here is what you said, here is where the previous one is."
 *
 * Without it a follow-up round is indistinguishable from the original: the same
 * layout, a different score, and no visible reason for the difference. The score
 * delta alone does not carry that — it says the number moved, not that it moved
 * because of something the reviewer typed.
 *
 * The chain is fetched rather than derived, because a result only knows its own
 * `version` and the id it was built on — enough to link one step back, not enough
 * to list the set. One request answers from any member of the chain.
 *
 * Nothing renders on an original review (`version` 1 or absent). A banner reading
 * "version 1 of 1" on every first review is noise, and noise is what teaches people
 * to stop reading banners.
 */
export function VersionBanner({
  result,
  onOpenVersion,
}: {
  result: ReviewResult
  onOpenVersion: (reviewId: string) => void
}) {
  const version = result.version ?? 1
  const [chain, setChain] = useState<ReviewVersion[] | null>(null)

  useEffect(() => {
    if (version <= 1) return
    let cancelled = false
    getReviewVersions(result.review_id)
      .then((fetched) => {
        if (!cancelled) setChain(fetched.versions)
      })
      // Deliberately silent. The chain is context for a review that has already
      // loaded and rendered; failing to fetch it must not put an error in front of
      // someone reading their findings. The banner degrades to what the result
      // itself knows, which is the version number and one link back.
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [result.review_id, version])

  if (version <= 1) return null

  const total = chain?.length ?? version
  const previous = result.based_on_review_id ?? ''

  return (
    /*
      A 4px rule rather than the 2px every caveat panel uses, and the only block on
      the page with one. Four left-ruled blocks stack here — this banner, the feedback
      box, the detection panel and the delta panel — and at identical weight they read
      as four equal asides. This one is not an aside: it says WHICH DOCUMENT the reader
      is looking at, and everything below is scoped to that. The extra 2px is the whole
      of the hierarchy fix; no new colour, no new fill.
    */
    <section
      data-testid="version-banner"
      data-version={version}
      className="mt-8 border-l-4 border-minfy-navy bg-surface-sunken px-5 py-4.5"
    >
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h2 className="t-heading">
          Follow-up review — version <span className="tnum">{version}</span>
          <span className="t-caption font-normal text-ink-muted">
            {' '}
            of <span className="tnum">{total}</span>
          </span>
        </h2>
        {previous !== '' && (
          <button
            type="button"
            onClick={() => onOpenVersion(previous)}
            className="t-caption font-medium text-minfy-indigo underline underline-offset-2 transition-colors hover:text-minfy-blue"
          >
            Open the previous version
          </button>
        )}
      </div>

      {/*
        The reviewer's own words, quoted back. This is what makes the new score
        legible: it is the input that produced it, and it is also the only place
        the round's feedback is visible after the fact.

        Rendered as a quote in monospace rather than as prose, for the same reason
        the evidence excerpts are: it is submitted text, not our writing about it,
        and the difference should be visible.
      */}
      {result.feedback && (
        <figure className="mt-3.5">
          <figcaption className="t-eyebrow text-ink-faint">
            Your feedback for this round
          </figcaption>
          <blockquote
            data-testid="version-feedback"
            className="t-caption mt-1.5 border-l-2 border-ink/25 pl-3.5 font-mono text-pretty text-ink-muted"
          >
            {result.feedback}
          </blockquote>
        </figure>
      )}

      {chain && chain.length > 1 && (
        /*
          `aria-label` rather than a visible caption: the chips are a list of links to
          sibling reviews, and without a name a screen reader reads them as a bare run
          of "v1 48.9" buttons with nothing saying what the list is.
        */
        <ol
          aria-label="All versions of this review"
          className="mt-4 flex flex-wrap gap-x-2 gap-y-2"
          data-testid="version-chain"
        >
          {chain.map((entry) => {
            const current = entry.review_id === result.review_id
            return (
              <li key={entry.review_id}>
                <button
                  type="button"
                  onClick={() => onOpenVersion(entry.review_id)}
                  disabled={current}
                  aria-current={current ? 'page' : undefined}
                  title={
                    entry.is_original
                      ? 'The original review'
                      : entry.feedback || 'Follow-up round'
                  }
                  /*
                    `border-ink-faint` rather than `border-hairline`. These are the only
                    controls on the page whose entire boundary was a hairline — #ccd2dc
                    on the sunken fill is 1.42:1, well under the 3:1 WCAG 1.4.11 asks
                    for the boundary of a user-interface component, so they read as
                    floating text rather than as buttons. ink-faint is 5.37:1.
                  */
                  className={`t-caption tnum border px-2.5 py-1.5 transition-colors duration-150 ${
                    current
                      ? 'cursor-default border-minfy-navy bg-minfy-navy font-semibold text-white'
                      : 'border-ink-faint text-ink hover:border-minfy-navy hover:bg-surface'
                  }`}
                >
                  v{entry.version}
                  {/* `text-*` rather than `opacity-70`: opacity fades the border and
                      the background with the text on a control whose boundary is the
                      thing that just got fixed. */}
                  <span
                    className={`font-normal ${current ? 'text-white/80' : 'text-ink-muted'}`}
                  >
                    {' '}
                    · {entry.overall_score.toFixed(1)}
                  </span>
                </button>
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
