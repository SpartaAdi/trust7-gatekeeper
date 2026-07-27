import { useState } from 'react'

import { StepTracker, type Step } from './components/StepTracker'
import { AnalyzingView } from './views/AnalyzingView'
import { HistoryView } from './views/HistoryView'
import { ResultsView } from './views/ResultsView'
import { UploadView } from './views/UploadView'

/**
 * History is home; the review flow is Upload -> Analyzing -> Results.
 *
 * `previousReviewId` is what makes the flow a loop rather than a line — when set,
 * the upload step submits a re-review and the results step renders a delta.
 * `returnToReviewId` remembers where a cancelled re-review should go back to.
 */
type Phase =
  | { name: 'history' }
  | { name: 'upload'; previousReviewId?: string; returnToReviewId?: string }
  | { name: 'analyzing'; reviewId: string }
  | { name: 'results'; reviewId: string }

const STEP_FOR: Record<Exclude<Phase['name'], 'history'>, Step> = {
  upload: 1,
  analyzing: 2,
  results: 3,
}

export default function App() {
  const [phase, setPhase] = useState<Phase>({ name: 'history' })
  const goHistory = () => setPhase({ name: 'history' })

  return (
    <div className="flex min-h-screen flex-col bg-surface text-ink">
      <header className="border-b border-hairline">
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-6 py-4">
          <button
            type="button"
            onClick={goHistory}
            className="flex min-w-0 items-center gap-3 text-left"
            aria-label="Trust7 Gatekeeper — back to all reviews"
          >
            <span
              aria-hidden="true"
              className="h-7 w-1 shrink-0 rounded-full bg-minfy-orange"
            />
            <span className="min-w-0">
              <span className="t-heading block truncate">Trust7 Gatekeeper</span>
              <span className="t-caption block truncate text-ink-muted">
                Solution design review — AWS Well-Architected and Minfy TRUST-7
              </span>
            </span>
          </button>
        </div>
      </header>

      {/* The tracker describes the review flow, so it is absent on the landing page. */}
      {phase.name !== 'history' && <StepTracker current={STEP_FOR[phase.name]} />}

      {/*
        Keying on the phase remounts the view, so the enter animation replays on
        every step change rather than only on first mount.
      */}
      <main key={phase.name} className="animate-enter flex-1">
        {phase.name === 'history' && (
          <HistoryView
            onOpen={(reviewId) => setPhase({ name: 'results', reviewId })}
            onNewReview={() => setPhase({ name: 'upload' })}
          />
        )}

        {phase.name === 'upload' && (
          <UploadView
            {...(phase.previousReviewId !== undefined && {
              previousReviewId: phase.previousReviewId,
            })}
            onStarted={(reviewId) => setPhase({ name: 'analyzing', reviewId })}
            onCancel={
              phase.returnToReviewId !== undefined
                ? () =>
                    setPhase({ name: 'results', reviewId: phase.returnToReviewId! })
                : goHistory
            }
          />
        )}

        {phase.name === 'analyzing' && (
          <AnalyzingView
            reviewId={phase.reviewId}
            onComplete={() => setPhase({ name: 'results', reviewId: phase.reviewId })}
            onStartOver={() => setPhase({ name: 'upload' })}
          />
        )}

        {phase.name === 'results' && (
          <ResultsView
            reviewId={phase.reviewId}
            onReReview={() =>
              setPhase({
                name: 'upload',
                previousReviewId: phase.reviewId,
                returnToReviewId: phase.reviewId,
              })
            }
            onStartOver={() => setPhase({ name: 'upload' })}
            onBackToHistory={goHistory}
          />
        )}
      </main>
    </div>
  )
}
