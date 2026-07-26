import { useEffect, useRef, useState } from 'react'

import { ApiError, getStatus } from '../api'
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

  return (
    <div className="mx-auto max-w-2xl px-6 py-12">
      <header>
        <h2 className="text-2xl font-semibold tracking-tight">
          {failed ? 'Analysis failed' : 'Analyzing the design'}
        </h2>
        <p className="mt-2 text-sm text-ink-muted">
          {failed ? (
            'The pipeline stopped at the stage marked below.'
          ) : (
            <>
              Review <span className="font-mono text-xs">{reviewId}</span>
              {stages.length > 0 && (
                <span className="tnum">
                  {' '}
                  — {doneCount} of {stages.length} stages complete
                </span>
              )}
            </>
          )}
        </p>
      </header>

      {status === null && !gaveUp && (
        <p className="mt-10 text-sm text-ink-muted">Waiting for the first status…</p>
      )}

      {stages.length > 0 && (
        <ol className="mt-10 divide-y divide-hairline border-y border-hairline">
          {stages.map((stage) => (
            <StageRow key={stage.name} stage={stage} />
          ))}
        </ol>
      )}

      {failed && status?.error && (
        <div
          role="alert"
          className="mt-8 border-l-2 border-sev-high bg-surface-sunken px-4 py-3 text-sm"
        >
          <p className="font-medium text-sev-high">Pipeline error</p>
          <p className="mt-1 break-words text-ink-muted">{status.error}</p>
        </div>
      )}

      {pollError && !failed && (
        <div
          role="alert"
          className="mt-8 border-l-2 border-sev-medium bg-surface-sunken px-4 py-3 text-sm"
        >
          <p className="font-medium text-sev-medium">
            {gaveUp ? 'Stopped polling for updates' : 'Status update failed — retrying'}
          </p>
          <p className="mt-1 text-ink-muted">{pollError}</p>
          {gaveUp && (
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="mt-3 border border-minfy-navy px-3 py-1.5 text-xs font-medium text-minfy-navy hover:bg-minfy-navy hover:text-white"
            >
              Retry
            </button>
          )}
        </div>
      )}

      {(failed || gaveUp) && (
        <button
          type="button"
          onClick={onStartOver}
          className="mt-8 text-sm text-ink-muted underline underline-offset-2 hover:text-ink"
        >
          Start over
        </button>
      )}
    </div>
  )
}

function StageRow({ stage }: { stage: StageProgress }) {
  const label = STAGE_LABELS[stage.name] ?? stage.name
  const running = stage.state === 'running'

  return (
    <li className="flex items-start gap-4 py-4">
      <StageMarker state={stage.state} />
      <div className="min-w-0 flex-1">
        <p
          className={[
            'text-sm',
            running
              ? 'font-semibold text-ink'
              : stage.state === 'done'
                ? 'text-ink'
                : stage.state === 'error'
                  ? 'font-semibold text-sev-high'
                  : 'text-ink-muted',
          ].join(' ')}
        >
          {label}
        </p>
        {stage.detail && (
          <p className="mt-0.5 break-words text-xs text-ink-muted">{stage.detail}</p>
        )}
      </div>
      {stage.state === 'done' && stage.started_at && stage.finished_at && (
        <span className="tnum shrink-0 text-xs text-ink-muted">
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
      <span
        aria-label="done"
        className={`${base} bg-minfy-navy text-[10px] font-bold text-white`}
      >
        ✓
      </span>
    )
  }
  if (state === 'error') {
    return (
      <span
        aria-label="failed"
        className={`${base} bg-sev-high text-[10px] font-bold text-white`}
      >
        !
      </span>
    )
  }
  if (state === 'running') {
    return (
      <span aria-label="in progress" className={`${base} relative`}>
        <span className="absolute size-5 animate-ping rounded-full bg-minfy-orange/30" />
        <span className="size-2.5 rounded-full bg-minfy-orange" />
      </span>
    )
  }
  return (
    <span aria-label="pending" className={`${base} border border-hairline`}>
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
