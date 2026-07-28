import { useEffect, useState } from 'react'

import { ApiError, listReviews } from '../api'
import { maturityFor } from '../maturity'
import type { PillarSummary, ReviewSummary } from '../types'

interface Props {
  onOpen: (reviewId: string) => void
  onNewReview: () => void
}

export function HistoryView({ onOpen, onNewReview }: Props) {
  const [reviews, setReviews] = useState<ReviewSummary[] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    listReviews()
      .then((fetched) => {
        if (!cancelled) setReviews(fetched)
      })
      .catch((caught: unknown) => {
        if (cancelled) return
        setError(
          caught instanceof ApiError ? caught.message : 'Could not load past reviews.',
        )
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="mx-auto max-w-5xl px-6 py-12 lg:py-16">
      <header className="flex flex-wrap items-end justify-between gap-6 border-b border-hairline pb-8">
        <div>
          <h2 className="t-display">Reviews</h2>
          <p className="t-body mt-2 text-ink-muted">
            {reviews === null
              ? 'Loading…'
              : reviews.length === 0
                ? 'No reviews yet.'
                : `${reviews.length} past ${reviews.length === 1 ? 'review' : 'reviews'}.`}
          </p>
        </div>
        <button
          type="button"
          onClick={onNewReview}
          className="t-body bg-minfy-indigo px-5 py-2.5 font-semibold text-white transition-colors duration-150 hover:bg-minfy-blue"
        >
          New review
        </button>
      </header>

      {error && (
        <div
          role="alert"
          className="mt-8 flex gap-3 border-l-2 border-sev-high bg-surface-sunken px-4 py-3.5"
        >
          <svg
            viewBox="0 0 16 16"
            aria-hidden="true"
            className="mt-0.5 size-4 shrink-0 fill-sev-high"
          >
            <path d="M8 1.5 L14.5 13.5 L1.5 13.5 Z" />
          </svg>
          <div className="min-w-0">
            <p className="t-heading text-sev-high">Could not load past reviews</p>
            <p className="t-caption mt-1 break-words text-ink-muted">{error}</p>
          </div>
        </div>
      )}

      {reviews === null && !error && (
        <div className="mt-8 space-y-4" aria-live="polite">
          {[0, 1, 2].map((row) => (
            <div key={row} className="flex items-center gap-6 py-3">
              <span className="h-3 flex-1 animate-pulse bg-hairline" />
              <span className="h-3 w-24 animate-pulse bg-hairline" />
            </div>
          ))}
        </div>
      )}

      {reviews !== null && reviews.length === 0 && !error && (
        <div className="mt-10 bg-pastel-tan px-6 py-14 text-center">
          <h3 className="t-title">Nothing reviewed yet</h3>
          <p className="t-body mx-auto mt-2 max-w-sm text-ink-muted">
            Submit a solution document and an architecture diagram, and the review
            will appear here with its score and findings.
          </p>
          <button
            type="button"
            onClick={onNewReview}
            className="t-body mt-6 bg-minfy-indigo px-5 py-2.5 font-semibold text-white transition-colors duration-150 hover:bg-minfy-blue"
          >
            Start the first review
          </button>
        </div>
      )}

      {reviews !== null && reviews.length > 0 && (
        <ul className="divide-y divide-hairline border-b border-hairline">
          {reviews.map((review, index) => (
            <li
              key={review.review_id}
              className="animate-enter"
              style={{ animationDelay: `${Math.min(index, 8) * 30}ms` }}
            >
              <button
                type="button"
                onClick={() => onOpen(review.review_id)}
                className="group flex w-full items-center gap-6 py-5 text-left transition-colors hover:bg-surface-sunken"
              >
                <span className="min-w-0 flex-1">
                  <span className="t-heading block truncate group-hover:text-minfy-indigo">
                    {review.title || 'Untitled review'}
                  </span>
                  <span className="t-caption mt-1 block text-ink-muted">
                    {formatDate(review.created_at)}
                    <span aria-hidden="true"> · </span>
                    <span className="tnum">{review.open_findings}</span> open
                    {review.high_severity_open > 0 && (
                      <>
                        <span aria-hidden="true"> · </span>
                        <span className="tnum font-medium text-sev-high">
                          {review.high_severity_open} high
                        </span>
                      </>
                    )}
                    {review.has_delta && (
                      <>
                        <span aria-hidden="true"> · </span>
                        re-review
                      </>
                    )}
                  </span>
                </span>

                <Heatmap pillars={review.pillars} title={review.title} />

                <span className="shrink-0 text-right">
                  <span className="tnum t-title block">
                    {review.overall_score.toFixed(1)}
                  </span>
                  <span className="t-caption block text-ink-muted">
                    {maturityFor(review.overall_score)}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * A 13-cell thumbnail of the pillar scores, grouped by framework.
 *
 * Opacity carries the score rather than hue, so the row reads as one texture at
 * a glance and stays legible to colour-blind viewers; the full grid on the
 * results page is where exact numbers live.
 */
function Heatmap({ pillars, title }: { pillars: PillarSummary[]; title: string }) {
  if (pillars.length === 0) return null
  const waf = pillars.filter((p) => p.framework === 'aws_waf')
  const trust7 = pillars.filter((p) => p.framework !== 'aws_waf')

  return (
    <span
      className="hidden shrink-0 items-center gap-2 sm:flex"
      role="img"
      aria-label={`Pillar scores for ${title || 'this review'}: ${pillars
        .map((p) => `${p.pillar_name} ${p.checks_evaluated ? p.score.toFixed(0) : 'n/a'}`)
        .join(', ')}`}
    >
      {[waf, trust7].map((group, groupIndex) => (
        <span key={groupIndex} className="flex gap-0.5">
          {group.map((pillar) => (
            <span
              key={pillar.pillar_id}
              title={`${pillar.pillar_name}: ${
                pillar.checks_evaluated ? pillar.score.toFixed(0) : 'not evaluated'
              }`}
              className="size-3.5 rounded-[1px] bg-minfy-navy"
              style={{
                // Floor keeps a very low score visible as a cell rather than blank.
                opacity: pillar.checks_evaluated
                  ? 0.12 + (Math.min(100, Math.max(0, pillar.score)) / 100) * 0.88
                  : 0.06,
              }}
            />
          ))}
        </span>
      ))}
    </span>
  )
}

function formatDate(iso: string): string {
  const parsed = Date.parse(iso)
  if (Number.isNaN(parsed)) return iso || 'Unknown date'
  return new Date(parsed).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}
