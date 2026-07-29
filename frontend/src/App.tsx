import { useEffect, useState } from 'react'

import { readShareParams } from './api'
import { MinfyMark } from './components/MinfyMark'
import { StepTracker, type Step } from './components/StepTracker'
import { TokenGate } from './components/TokenGate'
import { getToken, onTokenCleared } from './token'
import { AnalyzingView } from './views/AnalyzingView'
import { HistoryView } from './views/HistoryView'
import { ResultsView } from './views/ResultsView'
import { SharedView } from './views/SharedView'
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
  // `startedAt` rides on the phase so the elapsed clock survives re-renders without
  // a second source of truth. It is set once, by UploadView, and never recomputed.
  | { name: 'analyzing'; reviewId: string; startedAt: number }
  | { name: 'results'; reviewId: string }

const STEP_FOR: Record<Exclude<Phase['name'], 'history'>, Step> = {
  upload: 1,
  analyzing: 2,
  results: 3,
}

/** Header tabs, in flow order. Labels are set in small caps by `.t-tab`. */
const SECTIONS: readonly { phase: Phase['name']; label: string }[] = [
  { phase: 'history', label: 'Reviews' },
  { phase: 'upload', label: 'Submit' },
  { phase: 'analyzing', label: 'Analysis' },
  { phase: 'results', label: 'Findings' },
]

export default function App() {
  const [phase, setPhase] = useState<Phase>({ name: 'history' })
  const goHistory = () => setPhase({ name: 'history' })

  // Read once, from the URL this page was opened with. A share link is a whole
  // different entry point rather than a phase of the review flow: the recipient
  // has no demo token, so this has to be decided BEFORE the gate below, and the
  // shared view offers no way into the rest of the app.
  const [shared] = useState(readShareParams)

  // The demo gate. `rejected` distinguishes "you have not entered it yet" from
  // "what you entered was refused", which is the only feedback the user gets —
  // the server is the only thing that can judge the token.
  const [gate, setGate] = useState({ locked: getToken() === null, rejected: false })

  // api.ts drops the token on any 401, so this is how a stale or wrong token
  // brings the gate back rather than leaving every view showing its own error.
  useEffect(
    () => onTokenCleared(() => setGate({ locked: true, rejected: true })),
    [],
  )

  if (shared) {
    return <SharedView reviewId={shared.reviewId} token={shared.token} />
  }

  if (gate.locked) {
    return (
      <TokenGate
        rejected={gate.rejected}
        onUnlocked={() => {
          setGate({ locked: false, rejected: false })
          goHistory()
        }}
      />
    )
  }

  return (
    <div className="flex min-h-screen flex-col bg-surface text-ink">
      {/* Dark navy bar, as on the live site. */}
      <header className="bg-minfy-navy text-white">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-8 gap-y-3 px-6 py-4">
          <button
            type="button"
            onClick={goHistory}
            className="flex min-w-0 items-center gap-3 text-left"
            aria-label="Trust7 Gatekeeper — back to all reviews"
          >
            <MinfyMark size={26} tone="onDark" className="shrink-0" />
            <span aria-hidden="true" className="h-8 w-px shrink-0 bg-white/25" />
            <span className="min-w-0">
              <span className="t-title block truncate leading-tight">
                Trust7 Gatekeeper
              </span>
              <span className="t-caption block truncate text-white/60">
                Solution design review — AWS Well-Architected and Minfy TRUST-7
              </span>
            </span>
          </button>

          {/*
            Small-caps section tabs, as on the live site's nav. These describe
            where you are; they add no navigation the header did not already
            have. Only "Reviews" is interactive, because going home is what the
            wordmark already does — the rest are marks, not dead buttons.
          */}
          <nav aria-label="Sections" className="ml-auto flex items-center gap-6">
            {SECTIONS.map(({ phase: name, label }) => {
              const current = name === phase.name
              const tab = [
                't-tab border-b-2 pb-0.5 transition-colors duration-150',
                current
                  ? 'border-white text-white'
                  : 'border-transparent text-white/50',
              ].join(' ')

              return name === 'history' ? (
                <button
                  key={name}
                  type="button"
                  onClick={goHistory}
                  aria-current={current ? 'page' : undefined}
                  className={`${tab} hover:border-white/40 hover:text-white/85`}
                >
                  {label}
                </button>
              ) : (
                <span
                  key={name}
                  aria-current={current ? 'page' : undefined}
                  className={`${tab} hidden sm:block`}
                >
                  {label}
                </span>
              )
            })}
          </nav>
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
            onStarted={(reviewId, startedAt) =>
              setPhase({ name: 'analyzing', reviewId, startedAt })
            }
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
            startedAt={phase.startedAt}
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

      {/* Fine print, treated as the live site treats secondary info: small,
          muted, on a hairline, taking up no visual weight. */}
      <footer className="border-t border-hairline">
        <p className="t-caption mx-auto max-w-5xl px-6 py-5 text-[0.75rem] text-ink-faint">
          Built for the Minfy Buildathon — July 2026
        </p>
      </footer>
    </div>
  )
}
