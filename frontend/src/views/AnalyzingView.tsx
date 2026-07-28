import { useEffect, useRef, useState } from 'react'

import { ApiError, getStatus } from '../api'
import { estimateRemainingSeconds, formatRemaining } from '../eta'
import { STAGE_LABELS, type ReviewStatus, type StageProgress } from '../types'

const POLL_INTERVAL_MS = 1500
/** Consecutive poll failures tolerated before we stop and surface the error. */
const MAX_CONSECUTIVE_FAILURES = 5

interface Props {
  reviewId: string
  onComplete: () => void
  onStartOver: () => void
}

export function AnalyzingView({ reviewId, onComplete, onStartOver }: Props) {
  const [status, setStatus] = useState<ReviewStatus | null>(null)
  const [pollError, setPollError] = useState('')
  const [gaveUp, setGaveUp] = useState(false)

  // Held in a ref so a re-created callback doesn't restart the poll loop.
  const onCompleteRef = useRef(onComplete)
  onCompleteRef.current = onComplete

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined
    let failures = 0

    async function poll() {
      try {
        const next = await getStatus(reviewId)
        if (cancelled) return

        failures = 0
        setPollError('')
        setStatus(next)

        // Terminal states stop the loop; nothing further will change.
        if (next.state === 'complete') {
          onCompleteRef.current()
          return
        }
        if (next.state === 'error') return
      } catch (caught) {
        if (cancelled) return
        failures += 1
        setPollError(
          caught instanceof ApiError ? caught.message : 'Could not read review status.',
        )
        if (failures >= MAX_CONSECUTIVE_FAILURES) {
          setGaveUp(true)
          return
        }
      }
      timer = window.setTimeout(poll, POLL_INTERVAL_MS)
    }

    void poll()
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [reviewId])

  const stages = status?.stages ?? []
  const doneCount = stages.filter((stage) => stage.state === 'done').length
  const failed = status?.state === 'error'
  const percent = stages.length > 0 ? Math.round((doneCount / stages.length) * 100) : 0

  // The estimate has to count down between polls, so it gets its own one-second
  // tick. It stops as soon as the run settles or we stop polling — an interval
  // still running on a finished screen is a leak, not a detail.
  const [nowMs, setNowMs] = useState(() => Date.now())
  const settling = failed || gaveUp || status?.state === 'complete'
  useEffect(() => {
    if (settling) return
    const ticker = window.setInterval(() => setNowMs(Date.now()), 1000)
    return () => window.clearInterval(ticker)
  }, [settling])

  const remainingSeconds = failed ? null : estimateRemainingSeconds(stages, nowMs)

  return (
    <div className="mx-auto max-w-2xl px-6 py-12 lg:py-16">
      <header>
        <h2 className="t-display">{failed ? 'Analysis stopped' : 'Analyzing the design'}</h2>
        <p className="t-body mt-3 text-ink-muted">
          {failed ? (
            'The pipeline failed at the stage marked below. Nothing was scored.'
          ) : (
            <>
              Reviewing against all 45 checks. This usually takes a few minutes —
              the steps below update as each one finishes.
            </>
          )}
        </p>
        <p className="t-caption mt-1.5 font-mono text-ink-faint">{reviewId}</p>
      </header>

      {stages.length > 0 && !failed && (
        <div className="mt-8">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <span className="t-eyebrow text-ink-muted">Progress</span>
            <span className="tnum t-caption text-ink-muted">
              {doneCount} of {stages.length} stages
              {remainingSeconds !== null && (
                <>
                  <span aria-hidden="true"> · </span>
                  {/*
                    Polite, not assertive: this text changes every second, and an
                    assertive region would have a screen reader interrupt itself
                    continuously. `data-testid` because the wording is an
                    approximation the tests should not have to spell out.
                  */}
                  <span aria-live="polite" data-testid="eta">
                    {formatRemaining(remainingSeconds)}
                  </span>
                </>
              )}
            </span>
          </div>
          <div
            className="mt-2 h-1 w-full overflow-hidden bg-hairline"
            role="progressbar"
            aria-valuenow={percent}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Analysis progress"
          >
            <div
              className="h-full bg-minfy-indigo transition-[width] duration-500 ease-out"
              style={{ width: `${percent}%` }}
            />
          </div>
        </div>
      )}

      {status === null && !gaveUp && (
        <div className="mt-10 space-y-3" aria-live="polite">
          <p className="t-body text-ink-muted">Starting the pipeline…</p>
          {/* Skeleton rows, so the layout does not jump when the first status lands. */}
          {[0, 1, 2].map((row) => (
            <div key={row} className="flex items-center gap-4 py-2">
              <span className="size-5 animate-pulse rounded-full bg-hairline" />
              <span
                className="h-3 animate-pulse bg-hairline"
                style={{ width: `${55 - row * 10}%` }}
              />
            </div>
          ))}
        </div>
      )}

      {stages.length > 0 && (
        <ol className="mt-8 divide-y divide-hairline border-y border-hairline">
          {stages.map((stage, index) => (
            <StageRow key={stage.name} stage={stage} index={index} />
          ))}
        </ol>
      )}

      {failed && status?.error && (
        <div
          role="alert"
          className="animate-enter mt-8 flex gap-3 border-l-2 border-sev-high bg-surface-sunken px-4 py-3.5"
        >
          <svg viewBox="0 0 16 16" aria-hidden="true" className="mt-0.5 size-4 shrink-0 fill-sev-high">
            <path d="M8 1.5 L14.5 13.5 L1.5 13.5 Z" />
          </svg>
          <div className="min-w-0">
            <p className="t-heading text-sev-high">Pipeline error</p>
            <p className="t-caption mt-1 break-words text-ink-muted">{status.error}</p>
          </div>
        </div>
      )}

      {pollError && !failed && (
        <div
          role="alert"
          className="animate-enter mt-8 border-l-2 border-sev-medium bg-surface-sunken px-4 py-3.5"
        >
          <p className="t-heading text-sev-medium">
            {gaveUp ? 'Stopped checking for updates' : 'Status update failed — retrying'}
          </p>
          <p className="t-caption mt-1 text-ink-muted">{pollError}</p>
          {gaveUp && (
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="t-caption mt-3 border border-minfy-navy px-3 py-1.5 font-medium text-minfy-navy transition-colors hover:bg-minfy-navy hover:text-white"
            >
              Reload and retry
            </button>
          )}
        </div>
      )}

      {(failed || gaveUp) && (
        <button
          type="button"
          onClick={onStartOver}
          className="t-caption mt-8 text-ink-muted underline underline-offset-2 transition-colors hover:text-ink"
        >
          Start over
        </button>
      )}
    </div>
  )
}

function StageRow({ stage, index }: { stage: StageProgress; index: number }) {
  const label = STAGE_LABELS[stage.name] ?? stage.name
  const running = stage.state === 'running'

  return (
    <li
      className="animate-enter flex items-start gap-4 py-4"
      style={{ animationDelay: `${Math.min(index, 5) * 40}ms` }}
      aria-current={running ? 'step' : undefined}
    >
      <StageMarker state={stage.state} />
      <div className="min-w-0 flex-1">
        <p
          className={[
            't-body',
            running
              ? 'font-semibold text-ink'
              : stage.state === 'done'
                ? 'text-ink'
                : stage.state === 'error'
                  ? 'font-semibold text-sev-high'
                  : 'text-ink-faint',
          ].join(' ')}
        >
          {label}
        </p>
        {stage.detail && (
          <p className="t-caption mt-0.5 break-words text-ink-muted">{stage.detail}</p>
        )}
      </div>
      {stage.state === 'done' && stage.started_at && stage.finished_at && (
        <span className="tnum t-caption shrink-0 text-ink-faint">
          {durationSeconds(stage.started_at, stage.finished_at)}
        </span>
      )}
    </li>
  )
}

function StageMarker({ state }: { state: StageProgress['state'] }) {
  const base = 'mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full'

  if (state === 'done') {
    return (
      <span aria-label="Complete" role="img" className={`${base} bg-minfy-navy`}>
        <svg viewBox="0 0 12 12" aria-hidden="true" className="size-3 fill-none stroke-white stroke-2">
          <path d="M2.5 6.2 L4.8 8.5 L9.5 3.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
    )
  }
  if (state === 'error') {
    return (
      <span
        aria-label="Failed"
        role="img"
        className={`${base} bg-sev-high text-[11px] font-bold text-white`}
      >
        !
      </span>
    )
  }
  if (state === 'running') {
    return (
      <span aria-label="In progress" role="img" className={`${base} relative`}>
        <span className="absolute size-5 animate-ping rounded-full bg-minfy-indigo/25" />
        <span className="size-2.5 rounded-full bg-minfy-indigo" />
      </span>
    )
  }
  return (
    <span aria-label="Pending" role="img" className={`${base} border border-hairline`}>
      <span className="size-1.5 rounded-full bg-hairline" />
    </span>
  )
}

function durationSeconds(startedAt: string, finishedAt: string): string {
  const started = Date.parse(startedAt)
  const finished = Date.parse(finishedAt)
  if (Number.isNaN(started) || Number.isNaN(finished)) return ''
  return `${Math.max(0, Math.round((finished - started) / 1000))}s`
}
