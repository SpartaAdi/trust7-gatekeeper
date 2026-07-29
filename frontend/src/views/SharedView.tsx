import { useEffect, useState } from 'react'

import { ApiError, getSharedReview } from '../api'
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
        {error && (
          <div
            role="alert"
            className="border-l-2 border-sev-high bg-surface-sunken px-4 py-3.5"
          >
            <p className="t-heading text-sev-high">Link unavailable</p>
            <p className="t-caption mt-1 max-w-prose text-ink-muted">{error}</p>
          </div>
        )}

        {!error && !review && (
          <p className="t-body text-ink-muted">Loading the shared review…</p>
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
        <p className="t-display text-minfy-indigo">{review.overall_score.toFixed(1)}</p>
        <p className="t-heading">{band}</p>
        {review.delta && <DeltaBadge change={review.delta.change} />}
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

function DeltaBadge({ change }: { change: number }) {
  const rounded = Number(change.toFixed(1))
  if (rounded === 0) {
    return <span className="t-caption text-ink-muted">No change</span>
  }
  return (
    <span
      className={`t-caption font-semibold ${rounded > 0 ? 'text-sev-low' : 'text-sev-high'}`}
    >
      {rounded > 0 ? '+' : ''}
      {rounded.toFixed(1)} since the previous review
    </span>
  )
}
