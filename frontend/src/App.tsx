import { useState } from 'react'

import { StepTracker, type Step } from './components/StepTracker'
import { AnalyzingView } from './views/AnalyzingView'
import { ResultsView } from './views/ResultsView'
import { UploadView } from './views/UploadView'

/**
 * Single-page flow: Upload -> Analyzing -> Results.
 *
 * `previousReviewId` is what makes the flow a loop rather than a line — when set,
 * the upload step submits a re-review and the results step renders a delta.
 */
type Phase =
  | { name: 'upload'; previousReviewId?: string; returnToReviewId?: string }
  | { name: 'analyzing'; reviewId: string }
  | { name: 'results'; reviewId: string }

const STEP_FOR: Record<Phase['name'], Step> = {
  upload: 1,
  analyzing: 2,
  results: 3,
}

export default function App() {
  const [phase, setPhase] = useState<Phase>({ name: 'upload' })

  return (
    <div className="min-h-screen bg-surface text-ink">
      <header className="border-b border-hairline">
        <div className="mx-auto flex max-w-5xl items-baseline justify-between gap-4 px-6 py-5">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Trust7 Gatekeeper</h1>
            <p className="mt-0.5 text-sm text-ink-muted">
              Solution design review — AWS Well-Architected and Minfy TRUST-7
            </p>
          </div>
        </div>
      </header>

      <StepTracker current={STEP_FOR[phase.name]} />

      <main>
        {phase.name === 'upload' && (
          <UploadView
            {...(phase.previousReviewId !== undefined && {
              previousReviewId: phase.previousReviewId,
            })}
            onStarted={(reviewId) => setPhase({ name: 'analyzing', reviewId })}
            {...(phase.returnToReviewId !== undefined && {
              onCancelReReview: () =>
                setPhase({ name: 'results', reviewId: phase.returnToReviewId! }),
            })}
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
          />
        )}
      </main>
    </div>
  )
}
