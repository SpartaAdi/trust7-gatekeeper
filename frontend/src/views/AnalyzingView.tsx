import { useEffect, useRef, useState } from 'react'

import { ApiError, cancelReview, getStatus } from '../api'
import { elapsedSeconds, formatElapsed } from '../elapsed'
import { STAGE_LABELS, type ReviewStatus, type StageProgress } from '../types'

const POLL_INTERVAL_MS = 1500
/** Consecutive poll failures tolerated before we stop and surface the error. */
const MAX_CONSECUTIVE_FAILURES = 5

interface Props {
  reviewId: string
  /** Epoch ms when the submission began — set by UploadView, never recomputed. */
  startedAt: number
  onComplete: () => void
  onStartOver: () => void
}

export function AnalyzingView({
  reviewId,
  startedAt,
  onComplete,
  onStartOver,
}: Props) {
  const [status, setStatus] = useState<ReviewStatus | null>(null)
  const [pollError, setPollError] = useState('')
  const [gaveUp, setGaveUp] = useState(false)
  // Set the instant the button is pressed rather than waiting for the server's
  // answer. The reviewer asked for this to stop; the screen should say so at once,
  // and the request is on its way regardless of how long it takes to acknowledge.
  const [stopping, setStopping] = useState(false)
  // Its own state, not `pollError`: polling resumes when a stop fails, and its next
  // success calls `setPollError('')` — which would wipe the one message telling the
  // reviewer their stop did not take effect.
  const [stopError, setStopError] = useState('')

  // One tick drives the elapsed clock, and `frozenMs` is a latch over it: once set, the
  // displayed instant can never move again. A latch rather than
  // just tearing the interval down, because teardown is an effect and an effect can
  // flush a beat after the state change it reacts to — long enough for one more tick
  // to land past the end of the run.
  const [tickMs, setTickMs] = useState(() => Date.now())
  const [frozenMs, setFrozenMs] = useState<number | null>(null)
  const nowMs = frozenMs ?? tickMs

  /** Stop the clock at this instant. First call wins — a freeze is not revisable. */
  function freeze() {
    setFrozenMs((already) => already ?? Date.now())
  }

  // Held in a ref so a re-created callback doesn't restart the poll loop.
  const onCompleteRef = useRef(onComplete)
  onCompleteRef.current = onComplete

  useEffect(() => {
    // Polling stops the moment the reviewer asks it to. `stopping` is in the deps so
    // this effect tears down and does not re-arm — the request to the server is what
    // stops the work, and continuing to ask about it would just be noise.
    if (stopping) return
    let abandoned = false
    let timer: number | undefined
    let failures = 0

    async function poll() {
      try {
        const next = await getStatus(reviewId)
        if (abandoned) return

        failures = 0
        setPollError('')
        setStatus(next)

        // Terminal states stop the loop; nothing further will change. The clock is
        // frozen here rather than in an effect reacting to the state change, because
        // this is the moment the run is known to be over.
        if (next.state === 'complete') {
          freeze()
          onCompleteRef.current()
          return
        }
        if (next.state === 'error' || next.state === 'cancelled') {
          // `cancelled` can arrive without this tab having asked — a second tab, or
          // the reviewer's own click landing between two polls — so it is terminal
          // here as well as on the button's own path.
          freeze()
          return
        }
      } catch (caught) {
        if (abandoned) return
        failures += 1
        setPollError(
          caught instanceof ApiError ? caught.message : 'Could not read review status.',
        )
        if (failures >= MAX_CONSECUTIVE_FAILURES) {
          freeze()
          setGaveUp(true)
          return
        }
      }
      timer = window.setTimeout(poll, POLL_INTERVAL_MS)
    }

    void poll()
    return () => {
      abandoned = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [reviewId, stopping])

  /**
   * Stop the review.
   *
   * The clock freezes through the SAME latch the complete and error paths use — this
   * is `freeze()`, not a second copy of the logic — so a cancelled run reports the
   * time it actually ran for, and reports it the same way a failed one does.
   *
   * A 409 means the review finished between the last poll and the click. There is
   * nothing to stop, and nothing to apologise for either: the screen has already
   * moved on, so the rejection is swallowed rather than shown as a failure.
   */
  async function stop() {
    setStopping(true)
    setStopError('')
    freeze()
    try {
      setStatus(await cancelReview(reviewId))
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) return
      // Anything else and the server may still be running the pipeline, so say so
      // rather than leaving the screen claiming a stop that did not happen.
      setStopping(false)
      setStopError(
        caught instanceof ApiError ? caught.message : 'Could not stop the review.',
      )
    }
  }

  const stages = status?.stages ?? []
  const doneCount = stages.filter((stage) => stage.state === 'done').length
  const failed = status?.state === 'error'
  // `stopping` counts as cancelled straight away: the request may take a moment and
  // the pipeline only notices at its next stage boundary, but the reviewer's intent
  // is already known and nothing on this screen should keep implying work is queued.
  const cancelled = stopping || status?.state === 'cancelled'
  const percent = stages.length > 0 ? Math.round((doneCount / stages.length) * 100) : 0

  // The tick only runs while the run does — an interval still firing on a finished
  // screen is a leak, even though the latch above means it could no longer be seen.
  const settling = failed || gaveUp || cancelled || status?.state === 'complete'
  useEffect(() => {
    if (settling) return
    const ticker = window.setInterval(() => setTickMs(Date.now()), 1000)
    return () => window.clearInterval(ticker)
  }, [settling])

  // A CLOCK, and deliberately the only duration figure on this screen. There was an
  // estimate here; it went because latency for one review has ranged from 14 seconds to
  // 44 minutes on the same provider, so "about 3 min remaining" was wrong most of the
  // time and read as broken beside a run twenty minutes in. Elapsed time is always
  // true. It freezes with `nowMs` above: on a failure this screen stays mounted, and
  // how long the run got before it broke is itself the finding.
  const elapsed = elapsedSeconds(startedAt, nowMs)

  return (
    <div className="mx-auto max-w-2xl px-6 py-12 lg:py-16">
      {/*
        Three headings, because there are three outcomes and conflating two of them
        would be a lie either way round: a failure is not a stop the reviewer chose,
        and a stop the reviewer chose is not a failure.
      */}
      <header>
        <h2 className="t-display">
          {cancelled
            ? 'Review cancelled'
            : failed
              ? 'Analysis stopped'
              : 'Analyzing the design'}
        </h2>
        <p className="t-body mt-3 text-ink-muted">
          {cancelled ? (
            'You stopped this review. Nothing was scored and no result was saved — ' +
            'submit the design again to start a fresh review.'
          ) : failed ? (
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

      {stages.length > 0 && !failed && !cancelled && (
        <div className="mt-8">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <span className="t-eyebrow text-ink-muted">Progress</span>
            <span className="tnum t-caption text-ink-muted">
              {doneCount} of {stages.length} stages
              <span aria-hidden="true"> · </span>
              <ElapsedClock seconds={elapsed} />
            </span>
          </div>
          {/*
            Static, and deliberately a range rather than a figure. Observed latency
            has spanned 14 seconds to 44 minutes on the same provider, so anything
            claiming to know when this run ends would be wrong most of the time —
            see the ETA note in HANDOFF. This sets an expectation without making a
            promise, and it never changes as the run proceeds.
          */}
          <p className="t-caption mt-1.5 text-ink-faint" data-testid="duration-note">
            Typically 1–10 min; can occasionally run longer depending on provider
            load.
          </p>
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

      {/*
        Under the progress bar, not beside the heading: it is an action on the run in
        progress, and the reviewer looks here to judge whether it is worth waiting.
        Absent rather than disabled once the run has settled — there is nothing left
        to stop, and a dead button invites a click that does nothing.
      */}
      {!settling && (
        <div className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-2">
          <button
            type="button"
            onClick={stop}
            className="t-caption border border-hairline px-3.5 py-2 font-medium text-ink-muted transition-colors duration-150 hover:border-sev-high hover:text-sev-high"
          >
            Stop this review
          </button>
          <p className="t-caption text-ink-faint">
            Stops before the next step starts. The step already running may still
            finish and be charged.
          </p>
        </div>
      )}

      {status === null && !gaveUp && !cancelled && (
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

      {/*
        The progress block above is hidden on a failure, so the clock is repeated
        here — the elapsed time up to a failure is information, not decoration, and
        losing it would mean the one screen that most needs it does not show it.
      */}
      {(failed || cancelled) && (
        <div className="mt-8 flex items-baseline justify-between gap-x-4">
          <span className="t-eyebrow text-ink-muted">Ran for</span>
          <ElapsedClock seconds={elapsed} />
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

      {cancelled && (
        <div
          role="status"
          className="animate-enter mt-8 flex gap-3 border-l-2 border-minfy-navy bg-surface-sunken px-4 py-3.5"
        >
          {/* A stop glyph: ring plus square. Two paths rather than one — a single
              path fills the inner square under nonzero winding and reads as a dot. */}
          <svg viewBox="0 0 16 16" aria-hidden="true" className="mt-0.5 size-4 shrink-0">
            <circle cx="8" cy="8" r="6.75" className="fill-none stroke-minfy-navy" strokeWidth="1.4" />
            <rect x="5.4" y="5.4" width="5.2" height="5.2" className="fill-minfy-navy" />
          </svg>
          <div className="min-w-0">
            <p className="t-heading">Cancelled at your request</p>
            <p className="t-caption mt-1 text-ink-muted">
              No further steps were started. This review was not saved and will not
              appear in your history.
            </p>
          </div>
        </div>
      )}

      {stopError && (
        <div
          role="alert"
          className="animate-enter mt-8 border-l-2 border-sev-medium bg-surface-sunken px-4 py-3.5"
        >
          <p className="t-heading text-sev-medium">Could not stop the review</p>
          <p className="t-caption mt-1 break-words text-ink-muted">
            {stopError} The review may still be running — try again, or leave it to
            finish.
          </p>
        </div>
      )}

      {pollError && !failed && !cancelled && (
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

      {(failed || gaveUp || cancelled) && (
        <button
          type="button"
          onClick={onStartOver}
          className="t-caption mt-8 text-ink-muted underline underline-offset-2 transition-colors hover:text-ink"
        >
          {cancelled ? 'Submit a design again' : 'Start over'}
        </button>
      )}
    </div>
  )
}

/**
 * Elapsed wall clock. Deliberately not a bar and not a percentage.
 *
 * `aria-live="polite"` rather than assertive: the text changes every second, and an
 * assertive region would have a screen reader interrupt itself continuously. Existing
 * tokens only — `tnum` keeps the digits from jittering as they change width.
 */
function ElapsedClock({ seconds }: { seconds: number }) {
  return (
    <span
      className="tnum t-caption text-ink-muted"
      aria-live="polite"
      data-testid="elapsed"
    >
      {formatElapsed(seconds)} elapsed
    </span>
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
                  : stage.state === 'cancelled'
                    ? 'font-semibold text-ink'
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
  if (state === 'cancelled') {
    // A filled square: stopped, not failed and not pending. Muted navy rather than a
    // severity colour, because nothing went wrong here.
    return (
      <span aria-label="Stopped" role="img" className={`${base} border border-minfy-navy`}>
        <span className="size-2 bg-minfy-navy" />
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
