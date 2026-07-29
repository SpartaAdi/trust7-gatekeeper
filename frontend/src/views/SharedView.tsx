import { useEffect, useState } from 'react'

import { ApiError, getSharedReview } from '../api'
import { ChangeBadge } from '../components/ChangeBadge'
import { MinfyMark } from '../components/MinfyMark'
import { maturityFor } from '../maturity'
import type { SharedReview } from '../types'

/**
 * One completed review, read-only, opened from a share link.
 *
 * Rendered instead of the whole app — including the demo gate — because the
 * person opening it has no token and is not meant to need one. That also means
 * this view offers no navigation into the rest of the app: there is nothing here
 * to click through to, by design.
 *
 * It shows the scoreboard and its movement. Findings, evidence and remediation
 * are not in the payload at all (see `SharedReview` in backend/api/routes.py), so
 * there is nothing here that could accidentally render them.
 */
export function SharedView({ reviewId, token }: { reviewId: string; token: string }) {
  const [review, setReview] = useState<SharedReview | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    getSharedReview(reviewId, token)
      .then((result) => live && setReview(result))
      .catch((cause: unknown) => {
        if (!live) return
        setError(
          cause instanceof ApiError && cause.status === 404
            ? 'This link is no longer valid. Shared reviews are stored on a free-tier disk that is wiped when the server restarts, so a link stops working once that happens.'
            : cause instanceof Error
              ? cause.message
              : 'Could not load this shared review.',
        )
      })
    return () => {
      live = false
    }
  }, [reviewId, token])

  return (
    <div className="flex min-h-screen flex-col bg-surface text-ink">
      <header className="bg-minfy-navy text-white">
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-6 py-4">
          <MinfyMark size={26} tone="onDark" className="shrink-0" />
          <div className="min-w-0">
            <p className="t-heading">Trust7 Gatekeeper</p>
            <p className="t-caption text-white/70">
              Shared review — read only
            </p>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-3xl grow px-6 py-12">
        {/* Same alert shape as every other view: left rule, warning glyph,
            heading, then the detail. An error that looks different from the
            app's other errors reads as a different kind of problem. */}
        {error && (
          <div
            role="alert"
            className="flex gap-3 border-l-2 border-sev-high bg-surface-sunken px-4 py-3.5"
          >
            <svg
              viewBox="0 0 16 16"
              aria-hidden="true"
              className="mt-0.5 size-4 shrink-0 fill-sev-high"
            >
              <path d="M8 1.5 L14.5 13.5 L1.5 13.5 Z" />
            </svg>
            <div className="min-w-0">
              <p className="t-heading text-sev-high">Link unavailable</p>
              <p className="t-caption mt-1 max-w-prose break-words text-ink-muted">
                {error}
              </p>
            </div>
          </div>
        )}

        {/* Skeleton rather than a spinner or a line of text, matching the
            history and results views — the page is about to be dense, and a
            shape that resembles it settles the layout before it arrives. */}
        {!error && !review && (
          <div className="space-y-4" aria-live="polite">
            <span className="sr-only">Loading the shared review…</span>
            {[0, 1, 2, 3].map((row) => (
              <div
                key={row}
                className="h-3 animate-pulse bg-hairline"
                style={{ width: `${80 - row * 12}%` }}
              />
            ))}
          </div>
        )}

        {review && <SharedScoreboard review={review} />}
      </main>
    </div>
  )
}

function SharedScoreboard({ review }: { review: SharedReview }) {
  const band = maturityFor(review.overall_score)

  return (
    <div className="animate-enter">
      <h1 className="t-display">{review.title || 'Untitled review'}</h1>
      <p className="t-caption mt-2 text-ink-muted">
        Reviewed against {review.frameworks.join(' and ')} ·{' '}
        {review.component_count} component{review.component_count === 1 ? '' : 's'}
      </p>

      <div className="mt-8 flex flex-wrap items-baseline gap-x-6 gap-y-2 border-t border-hairline pt-6">
        {/* Plain ink and an explicit denominator, exactly as the results view
            renders it — a bare "61.5" invites being read as 61.5 out of 5, and
            colouring the number would imply a band the label already states. */}
        <p className="tnum text-5xl font-semibold leading-none tracking-tight">
          {review.overall_score.toFixed(1)}
          <span className="t-title align-baseline font-normal text-ink-muted">
            /100
          </span>
        </p>
        <p className="t-eyebrow text-ink-muted">Overall · {band}</p>
        {review.delta && <ChangeBadge change={review.delta.change} />}
      </div>

      <dl className="mt-6 flex flex-wrap gap-x-10 gap-y-3">
        <div>
          <dt className="t-caption text-ink-muted">Open findings</dt>
          <dd className="t-heading">{review.open_findings}</dd>
        </div>
        <div>
          <dt className="t-caption text-ink-muted">High severity</dt>
          <dd className="t-heading">{review.high_severity_open}</dd>
        </div>
      </dl>

      <h2 className="t-heading mt-10 border-t border-hairline pt-6">Pillar scores</h2>
      <table className="mt-4 w-full">
        <thead>
          <tr className="border-b border-hairline text-left">
            <th className="t-caption pb-2 font-normal text-ink-muted">Pillar</th>
            <th className="t-caption pb-2 text-right font-normal text-ink-muted">
              Checks passed
            </th>
            <th className="t-caption pb-2 text-right font-normal text-ink-muted">
              Score
            </th>
          </tr>
        </thead>
        <tbody>
          {review.pillars.map((pillar) => (
            <tr key={`${pillar.framework}-${pillar.pillar_id}`} className="border-b border-hairline/60">
              <td className="t-body py-2">{pillar.pillar_name}</td>
              <td className="t-body py-2 text-right tabular-nums text-ink-muted">
                {pillar.checks_passed}/{pillar.checks_evaluated}
              </td>
              <td className="t-body py-2 text-right font-semibold tabular-nums">
                {pillar.score.toFixed(1)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {review.delta && (
        <>
          <h2 className="t-heading mt-10 border-t border-hairline pt-6">
            Change since the previous review
          </h2>
          <p className="t-body mt-2 text-ink-muted">
            {review.delta.previous_overall_score.toFixed(1)} →{' '}
            {review.delta.current_overall_score.toFixed(1)} ·{' '}
            {review.delta.resolved_checks.length} resolved,{' '}
            {review.delta.new_checks.length} new,{' '}
            {review.delta.unchanged_failures.length} still open
          </p>
        </>
      )}

      {/*
        Stated on the page, not just in a code comment. The link is durable; the
        review behind it is not, and someone reading a score they intend to act on
        should know it can disappear. Text comes from the server so the API and
        the UI cannot drift apart on what the guarantee is.
      */}
      <p className="t-caption mt-10 border-t border-hairline pt-6 text-ink-faint">
        {review.expires_note}
      </p>
    </div>
  )
}
