import { useEffect, useState } from 'react'

import { ApiError, createShareLink, downloadReport, getReview, shareUrl } from '../api'
import { AiDetectionPanel, pillarNotApplicableReason } from '../components/AiDetectionPanel'
import { ChangeBadge } from '../components/ChangeBadge'
import { DataFidelity } from '../components/DataFidelity'
import { FeedbackBox } from '../components/FeedbackBox'
import { IngestWarnings } from '../components/IngestWarnings'
import { OpenQuestions } from '../components/OpenQuestions'
import { RemediationGapPanel } from '../components/RemediationGapPanel'
import { SeverityMark } from '../components/SeverityMark'
import { StructuredText } from '../components/StructuredText'
import { VersionBanner } from '../components/VersionBanner'
import {
  MATURITY_BOUND_NOTE,
  MATURITY_SCALE,
  maturityFor,
  scoreToneClass,
  type MaturityLabel,
} from '../maturity'
import type {
  AiDetection,
  Finding,
  FrameworkScore,
  PillarScore,
  ReviewResult,
  ScoreDelta,
  Severity,
  UseCaseNote,
} from '../types'
import { disagreesWithPillar } from '../types'
import {
  PHASE_LABEL,
  flattenActions,
  phaseFor,
  prioritizedActions,
  priorityFocus,
} from './roadmap'

interface Props {
  reviewId: string
  /** The re-analyze flow: a fresh design, submitted through the upload step. */
  onReReview: () => void
  /**
   * A follow-up ROUND started from the feedback box. Distinct from `onReReview`:
   * this one is already accepted and running, so the caller polls it rather than
   * routing back to the upload step.
   */
  onFollowUpStarted: (newReviewId: string, startedAt: number) => void
  /** Open another version of this review in place. */
  onOpenVersion: (reviewId: string) => void
  onStartOver: () => void
  onBackToHistory: () => void
}

export function ResultsView({
  reviewId,
  onReReview,
  onFollowUpStarted,
  onOpenVersion,
  onStartOver,
  onBackToHistory,
}: Props) {
  const [result, setResult] = useState<ReviewResult | null>(null)
  const [error, setError] = useState('')
  const [questionsOpen, setQuestionsOpen] = useState(false)

  useEffect(() => {
    let cancelled = false
    setError('')
    setResult(null)

    getReview(reviewId)
      .then((fetched) => {
        if (!cancelled) setResult(fetched)
      })
      .catch((caught: unknown) => {
        if (cancelled) return
        setError(
          caught instanceof ApiError ? caught.message : 'Could not load the review.',
        )
      })

    return () => {
      cancelled = true
    }
  }, [reviewId])

  if (error) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-16">
        <div
          role="alert"
          className="flex gap-3 border-l-2 border-sev-high bg-surface-sunken px-4 py-3.5"
        >
          <svg viewBox="0 0 16 16" aria-hidden="true" className="mt-0.5 size-4 shrink-0 fill-sev-high">
            <path d="M8 1.5 L14.5 13.5 L1.5 13.5 Z" />
          </svg>
          <div className="min-w-0">
            <p className="t-heading text-sev-high">Could not load the review</p>
            <p className="t-caption mt-1 break-words text-ink-muted">{error}</p>
          </div>
        </div>
        <button
          type="button"
          onClick={onStartOver}
          className="t-caption mt-6 text-ink-muted underline underline-offset-2 hover:text-ink"
        >
          Start over
        </button>
      </div>
    )
  }

  if (result === null) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-16" aria-live="polite">
        <p className="t-body text-ink-muted">Loading results…</p>
        <div className="mt-8 space-y-4">
          {[0, 1, 2, 3].map((row) => (
            <div
              key={row}
              className="h-3 animate-pulse bg-hairline"
              style={{ width: `${80 - row * 12}%` }}
            />
          ))}
        </div>
      </div>
    )
  }

  const open = result.findings.filter(
    (finding) => finding.status === 'fail' || finding.status === 'partial',
  )

  return (
    <div className="mx-auto max-w-5xl px-6 py-12 lg:py-16">
      <header className="flex flex-wrap items-end justify-between gap-x-10 gap-y-6 border-b border-hairline pb-8">
        <div className="min-w-0 flex-1">
          <p className="t-eyebrow text-ink-faint">Review complete</p>
          <h2 className="t-display mt-2">{result.title || 'Design review'}</h2>
          <p className="t-caption mt-2 text-ink-muted">
            <span className="font-mono">{result.review_id}</span>
            <span aria-hidden="true"> · </span>
            {open.length} open {open.length === 1 ? 'finding' : 'findings'} across{' '}
            <span className="tnum">{result.findings.length}</span> checks
          </p>
        </div>

        <div className="shrink-0 text-right">
          <p className="tnum text-5xl font-semibold leading-none tracking-tight">
            {result.overall_score.toFixed(1)}
            {/* The denominator, so a 4.5 is not mistaken for 4.5 out of 5. */}
            <span className="t-title align-baseline font-normal text-ink-muted">
              /100
            </span>
          </p>
          <p className="t-eyebrow mt-2 flex items-center justify-end gap-1.5 text-ink-muted">
            Overall · {maturityFor(result.overall_score)}
            <MaturityScaleHint current={maturityFor(result.overall_score)} />
          </p>
        </div>
      </header>

      {/*
        Which document this is, before anything about what it says. Renders nothing
        on an original review — a "version 1 of 1" banner on every first review is
        noise, and noise is what teaches people to stop reading banners.
      */}
      <VersionBanner result={result} onOpenVersion={onOpenVersion} />

      {/*
        The follow-up box, at the top of the review as asked.

        Reaching ResultsView at all means the review is complete: `GET /reviews/{id}`
        answers from the stored ReviewResult, which is written only by a run that
        finished, so there is no state in which this renders early. The check is the
        fetch, not a flag that could drift from it.

        The three caveat panels stay directly below rather than above, and that
        ordering is deliberate in both directions: they were put at the top in an
        earlier round to precede every number they qualify, which they still do —
        the executive summary and all five sections are below them.
      */}
      <FeedbackBox reviewId={result.review_id} onStarted={onFollowUpStarted} />

      {/*
        The other way into a follow-up round, beside the free-text box above.
        That one asks "what did we get wrong"; this one turns each open finding
        back into the question it started as, because most of them are open for
        want of operational evidence a document cannot carry rather than because
        the design is wrong. Rendered only when something is actually open.
      */}
      {open.length > 0 && (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => setQuestionsOpen(true)}
            data-testid="open-questions-launcher"
            className="t-caption text-minfy-indigo underline underline-offset-2 hover:text-minfy-blue"
          >
            Answer {open.length} open {open.length === 1 ? 'question' : 'questions'}
            {' '}this review could not
          </button>
        </div>
      )}

      {questionsOpen && (
        <OpenQuestions
          reviewId={result.review_id}
          findings={result.findings}
          onClose={() => setQuestionsOpen(false)}
          onStarted={onFollowUpStarted}
        />
      )}

      {/*
        ABOVE the executive summary and the score, deliberately. A warning says the
        review may have been scored on a fraction of the design; a reader who meets
        the 62.4 and the summary first has already formed a view by the time they
        reach the caveat, and the caveat is the more important of the two. It is also
        why this is not tucked in at the bottom with the metadata.
      */}
      <IngestWarnings warnings={result.warnings ?? []} className="mt-8" />

      {/*
        Directly under the warnings and still above the score. Same reasoning: these
        qualify every number below them, and a reader who meets the 62.5 first has
        already formed a view by the time they reach the caveat.
      */}
      <DataFidelity fidelity={result.fidelity} className="mt-3" />

      {/*
        Why the AI-dependent checks did or did not apply. Here rather than buried in
        the pillar grid, and for the same reason as the two panels above: it qualifies
        the TRUST-7 number a reader is about to see. Nineteen of the forty-five checks
        turn on this, so "no AI/ML component detected" is one of the largest single
        influences on the score and used to be invisible.

        `disagrees` is the case that matters — evidence of AI on a design whose AI
        pillar was scored not-applicable. It is shown, never acted on.
      */}
      <AiDetectionPanel
        detection={result.ai_detection}
        disagrees={result.frameworks.some((framework) =>
          framework.pillars.some((pillar) =>
            disagreesWithPillar(result.ai_detection, pillar),
          ),
        )}
        className="mt-3"
      />

      {/*
        Part of the deliverable that did not get produced, said once at the top.
        Below the fidelity numbers because those describe how well the design was
        READ, and this describes what was WRITTEN about it — but above the executive
        summary, because a reader who never scrolls to the roadmap would otherwise
        never learn that the roadmap is empty.
      */}
      <RemediationGapPanel gap={result.remediation_gap} className="mt-3" />

      {result.delta && <DeltaSummary delta={result.delta} />}

      {/*
        Four sections, in the order a reader needs them: what it means, how it
        scored, what to do, then the full record. The previous order opened with
        two overlapping action lists before the reader knew the verdict.
      */}

      {/* (a) Executive summary — the verdict, first. */}
      {result.executive_summary && (
        <section className="mt-12 bg-pastel-cream px-5 py-5" data-testid="executive-summary">
          <h3 className="t-eyebrow text-ink-muted">Executive summary</h3>
          <p className="t-body mt-2.5 max-w-prose text-pretty text-[0.9375rem] leading-relaxed">
            {result.executive_summary}
          </p>
        </section>
      )}

      {/* (b) Assessment — the prose assessment and the two heatmaps under one
          heading, since they answer the same question at different resolutions.
          Framework order follows the rubric: AWS Well-Architected, then TRUST-7. */}
      <section className="mt-12" data-testid="assessment">
        <h3 className="t-eyebrow text-ink-muted">Assessment · pillar maturity</h3>
        {result.summary && (
          <StructuredText
            text={result.summary}
            className="t-body mt-3 max-w-prose text-pretty"
          />
        )}
        <PriorityFocus findings={result.findings} />

        <div className="mt-6 space-y-10">
          {result.frameworks.map((framework) => (
            <FrameworkSection
              key={framework.framework}
              framework={framework}
              findings={result.findings}
              detection={result.ai_detection}
            />
          ))}
        </div>
      </section>

      {/*
        (c) Findings — the record AND the action list, which are now one section.

        There were two: a roadmap grouped by effort phase, and this list grouped by
        severity. They held the same open findings under two different headings, and
        a reader had to reconcile them to answer one question. Effort survives as a
        per-finding tag rather than as a grouping axis — see `FindingRow` — so
        nothing the roadmap showed is gone, it just stopped being a second list.

        "Fix these first" is unaffected by the merge: it lives under the assessment
        above and always did, not inside the roadmap that was removed.
      */}
      <FindingsList findings={result.findings} />

      {/* (e) Use-case notes — only when context was given and could be used. */}
      <UseCaseNotes notes={result.use_case_notes} context={result.context} />

      <footer className="mt-16 flex flex-wrap items-center gap-x-6 gap-y-4 border-t border-hairline pt-8">
        {/*
          Two operations on this page could be called "re-review", and they are not
          the same thing:

            this button    -> POST /reviews/{id}/reanalyze — a FRESH UPLOAD, scored
                              from scratch and compared against this review
            feedback box   -> POST /reviews/{id}/re-review — this same design
                              re-evaluated with the reviewer's words, as a new version

          The label was "Re-review a revised design", which described the feedback box
          at least as well as it described this. It now names the INPUT, which is the
          actual difference: one takes a file, the other takes a sentence.
        */}
        <button
          type="button"
          onClick={onReReview}
          title="Submit a different design as a new review and compare its score against this one. To correct or revise THIS review, use the follow-up box at the top of the page."
          className="t-body bg-minfy-indigo px-5 py-2.5 font-semibold text-white transition-colors duration-150 hover:bg-minfy-blue"
        >
          Upload a different design and compare
        </button>
        <DownloadReportButton reviewId={result.review_id} />
        {/* Shown only when the prompt would carry something. A button that copies a
            heading and an empty list is worse than no button. */}
        {buildFixItPrompt(result.findings) !== '' && (
          <CopyFixItPromptButton findings={result.findings} />
        )}
        <CopyShareLinkButton reviewId={result.review_id} />
        <button
          type="button"
          onClick={onStartOver}
          className="t-caption text-ink-muted underline underline-offset-2 transition-colors hover:text-ink"
        >
          Review a different design
        </button>
        <button
          type="button"
          onClick={onBackToHistory}
          className="t-caption text-ink-muted underline underline-offset-2 transition-colors hover:text-ink"
        >
          All reviews
        </button>
        {Object.keys(result.token_usage).length > 0 && (
          <p className="tnum t-caption ml-auto text-ink-faint">
            {(result.token_usage['input_tokens'] ?? 0).toLocaleString()} in /{' '}
            {(result.token_usage['output_tokens'] ?? 0).toLocaleString()} out tokens
            {(result.token_usage['cache_read_input_tokens'] ?? 0) > 0 && (
              <>
                {' '}
                · {(result.token_usage['cache_read_input_tokens'] ?? 0).toLocaleString()}{' '}
                cached
              </>
            )}
          </p>
        )}
      </footer>
    </div>
  )
}

/**
 * Cap on the copied prompt. The roadmap on screen is uncapped — it is a plan and
 * a plan that stops at ten is wrong — but a prompt pasted into an assistant is a
 * different artefact, and forty-five imperatives is not a usable instruction.
 */
const MAX_PROMPT_ITEMS = 10

/**
 * What the copied prompt asks the receiving tool to DO.
 *
 * "Please revise the diagram to address each one" was an implicit ask, and it got
 * an implicit answer: a redrawn diagram, or a paragraph of prose, with no way to
 * tell which gap each change was meant to close or what order to work in. The
 * findings below it are numbered and specific; the instruction above them was
 * neither, so the most structured part of the artefact was being thrown away at the
 * point of use.
 *
 * It now asks for a numbered plan keyed to the gap numbers, which makes the reply
 * checkable against the review it came from — and asks the receiving tool to say
 * when a gap cannot be closed in the diagram at all. Several checks in the rubric
 * are process or governance controls (an incident-response runbook, model
 * inventory, human-in-the-loop sign-off); silently "addressing" those in a diagram
 * produces a box that claims a control exists when nothing does, which is the
 * failure this whole tool is built to catch.
 */
export const FIX_IT_PREAMBLE =
  'Here is my architecture. A review found the following gaps.\n\n' +
  'For each one, give me a clear, numbered, step-by-step plan for how to fix it ' +
  'in my architecture diagram — assume I will be editing the diagram directly, ' +
  'so be concrete about what to add, remove, or reconnect. Number your steps ' +
  'against the gap numbers below so I can work through them one at a time. If a ' +
  'gap cannot be closed in the diagram alone — because it is a process or ' +
  'governance control rather than a component — say so plainly for that gap ' +
  'instead of inventing a box for it.\n\n' +
  'The gaps:'

/** Appended when any item lacks guidance, so the absence travels with the prompt. */
export const FIX_IT_GAP_NOTE =
  'Note: the review did not produce remediation guidance for the items marked ' +
  'above. For those, the finding and the evidence behind it are given instead — ' +
  'no fix was suggested, so treat them as gaps to solve rather than instructions ' +
  'to follow.'

/**
 * One numbered line for the fix-it prompt.
 *
 * When `remediation` exists this is the model's own imperative text, verbatim. When
 * it does not, the fallback used to be the bare `title` — which is the rubric
 * check's description, e.g. "Monitoring, logging, and alerting are defined for the
 * workload's key operational signals."
 *
 * That was the worst possible fallback. Pasted under "please revise the diagram to
 * address each one", a rubric description reads as a specific instruction about THIS
 * design while carrying nothing specific to it. The prompt looked complete, and this
 * is the artefact most likely to leave the app and land in someone else's editor —
 * where nobody can tell that ten confident-looking lines came from a stage that
 * returned nothing. A real run produced exactly that: 0 of 25 remediations, twice
 * over, and a copyable prompt that betrayed none of it.
 *
 * So the absence is now stated, and `evidence` is carried in its place. Evidence is
 * the right substitute because it is the one field the evaluate stage always writes
 * and it IS design-specific — it says what in this design drove the verdict, which
 * is the context a receiving tool needs in order to propose a fix itself.
 */
function fixItLine(finding: Finding, index: number): string {
  const number = `${index + 1}.`
  if (finding.remediation.trim()) {
    return `${number} ${finding.remediation}`
  }

  const evidence = finding.evidence.trim()
  return [
    `${number} [NO REMEDIATION GUIDANCE] ${finding.title}`,
    evidence
      ? `   Evidence from the review: ${evidence}`
      : '   The review recorded no evidence for this finding either.',
  ].join('\n')
}

/**
 * The fix-it prompt, built from the same prioritized actions the roadmap shows.
 *
 * Sourced from `prioritizedActions` rather than a separate selector, so what gets
 * copied is what is on screen, in the same order. When these were two functions
 * the prompt and the page could disagree about what the top actions were.
 */
export function buildFixItPrompt(findings: readonly Finding[]): string {
  const actions = flattenActions(prioritizedActions(findings)).slice(
    0,
    MAX_PROMPT_ITEMS,
  )
  if (actions.length === 0) return ''

  const numbered = actions.map(fixItLine)
  const anyMissing = actions.some((finding) => !finding.remediation.trim())
  const body = `${FIX_IT_PREAMBLE}\n\n${numbered.join('\n')}\n`
  return anyMissing ? `${body}\n${FIX_IT_GAP_NOTE}\n` : body
}

/**
 * Copies the fix-it prompt.
 *
 * `navigator.clipboard` is absent outside a secure context and can be refused by
 * permissions, so the failure is surfaced rather than swallowed — a copy button that
 * silently does nothing is worse than one that says it could not.
 */
function CopyFixItPromptButton({ findings }: { findings: Finding[] }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')

  async function run() {
    try {
      await navigator.clipboard.writeText(buildFixItPrompt(findings))
      setState('copied')
      window.setTimeout(() => setState('idle'), 2000)
    } catch {
      setState('failed')
    }
  }

  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {/*
        `group` drives the popover on hover; `focus-within` drives it for the
        keyboard, so the explanation is not mouse-only.
      */}
      <span className="group relative">
        <button
          type="button"
          onClick={run}
          // Supplementary, not a repeat of the label, so it is described rather
          // than hidden — the mic tooltip in UploadView is aria-hidden precisely
          // because it says the same words as its aria-label. This one does not.
          aria-describedby={FIX_IT_TOOLTIP_ID}
          // Fixed width: the label shortens to "Copied" and back, and without this
          // the whole action row reflows twice on every click.
          className={`t-body flex w-[13.5rem] items-center justify-center gap-2 border border-minfy-navy px-4 py-2.5 font-semibold transition-colors duration-150 ${
            state === 'copied'
              ? 'bg-minfy-navy text-white'
              : 'text-minfy-navy hover:bg-minfy-navy hover:text-white'
          }`}
        >
          <svg viewBox="0 0 16 16" aria-hidden="true" className="size-3.5 fill-current">
            <path d="M5.5 1.5h7A1.5 1.5 0 0 1 14 3v8h-1.5V3h-7Z M2 5h8.5A1.5 1.5 0 0 1 12 6.5v7A1.5 1.5 0 0 1 10.5 15H3.5A1.5 1.5 0 0 1 2 13.5Z" />
          </svg>
          {state === 'copied' ? 'Copied' : 'Copy fix-it prompt'}
        </button>

        {/*
          Says what lands on the clipboard. "Copy fix-it prompt" names the button
          but not the artefact, and the one question it left open — a prompt for
          what, containing what — is the one this answers. Wording matches what
          `buildFixItPrompt` actually produces: the roadmap's actions, not the
          whole findings list, and no assistant is named because the prompt names
          none either.
        */}
        {/*
          No `role="tooltip"`: this node is always mounted and only faded in, so
          the role would put a permanent tooltip in the accessibility tree and
          make `getByRole('tooltip')` ambiguous against the two disclosure
          tooltips on this page, which mount only while open. `aria-describedby`
          is what actually carries the text to a screen reader, and it works
          whether or not the node is visible.
        */}
        <span
          id={FIX_IT_TOOLTIP_ID}
          data-testid="fix-it-tooltip"
          className="pointer-events-none absolute bottom-full left-0 z-10 mb-2 w-64 border border-hairline bg-surface p-2.5 text-left opacity-0 shadow-sm transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100"
        >
          <span className="t-caption block text-ink-muted">
            Copies the prioritized actions as a ready-to-paste prompt asking an AI
            assistant to revise your architecture diagram.
          </span>
        </span>
      </span>

      {/* Polite, not an alert: a copy failure is recoverable and the user is
          already looking at the button they just pressed. */}
      <span aria-live="polite" className="t-caption text-sev-high">
        {state === 'failed' ? 'Could not copy to the clipboard.' : ''}
      </span>
    </span>
  )
}

/** Stable id so the button can point at its description with aria-describedby. */
const FIX_IT_TOOLTIP_ID = 'fix-it-prompt-description'

/**
 * Copies a read-only link to this review.
 *
 * Same shape as the fix-it copy button above, deliberately — the failure mode is
 * identical (clipboard unavailable or refused) and so is the recovery, so this
 * mirrors it rather than inventing a second idiom. The one addition is that the
 * link has to be fetched first, which can fail on its own.
 *
 * The note under the button is not decoration: the link outlives a restart but
 * the review it points at does not, and someone about to paste this into an
 * email should know that before they send it.
 */
function CopyShareLinkButton({ reviewId }: { reviewId: string }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const [note, setNote] = useState('')

  async function run() {
    try {
      const link = await createShareLink(reviewId)
      await navigator.clipboard.writeText(shareUrl(link))
      setNote(link.expires_note)
      setState('copied')
      window.setTimeout(() => setState('idle'), 2000)
    } catch (cause) {
      setNote(cause instanceof Error ? cause.message : '')
      setState('failed')
    }
  }

  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <button
        type="button"
        onClick={run}
        className={`t-body flex w-[13.5rem] items-center justify-center gap-2 border border-minfy-navy px-4 py-2.5 font-semibold transition-colors duration-150 ${
          state === 'copied'
            ? 'bg-minfy-navy text-white'
            : 'text-minfy-navy hover:bg-minfy-navy hover:text-white'
        }`}
      >
        <svg viewBox="0 0 16 16" aria-hidden="true" className="size-3.5 fill-current">
          <path d="M6.6 9.4a3 3 0 0 0 4.2 0l2.2-2.2a3 3 0 0 0-4.2-4.2l-1 1 1 1 1-1a1.6 1.6 0 0 1 2.2 2.2L9.8 8.2a1.6 1.6 0 0 1-2.2 0Z M9.4 6.6a3 3 0 0 0-4.2 0L3 8.8a3 3 0 0 0 4.2 4.2l1-1-1-1-1 1A1.6 1.6 0 0 1 4 9.8l2.2-2.2a1.6 1.6 0 0 1 2.2 0Z" />
        </svg>
        {state === 'copied' ? 'Link copied' : 'Copy share link'}
      </button>
      <span
        aria-live="polite"
        className={`t-caption ${state === 'failed' ? 'text-sev-high' : 'text-ink-faint'}`}
      >
        {state === 'failed'
          ? `Could not copy a share link. ${note}`.trim()
          : state === 'copied'
            ? note
            : ''}
      </span>
    </span>
  )
}

/**
 * The maturity scale, on hover and on tap.
 *
 * Both, deliberately: hover alone is unreachable on a touch screen, so the icon is
 * a real button that toggles as well. The panel is in the DOM only while shown, so
 * a screen reader is not read the whole scale on every results page.
 *
 * The tiers come from `MATURITY_SCALE`, which is derived from the same `BANDS`
 * table `maturityFor` uses — the tooltip cannot disagree with the badge above it.
 */
function MaturityScaleHint({ current }: { current: MaturityLabel }) {
  // Hover and tap are tracked separately on purpose. Sharing one flag means a
  // mouse click — which arrives after a hover has already opened the panel —
  // toggles it straight back shut, so the tooltip is unopenable with a mouse.
  const [pinned, setPinned] = useState(false)
  const [hovered, setHovered] = useState(false)
  const shown = pinned || hovered

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        type="button"
        onClick={() => setPinned((value) => !value)}
        aria-expanded={shown}
        aria-label="What the maturity tiers mean"
        className="flex size-4 items-center justify-center rounded-full border border-ink-faint text-[9px] font-bold text-ink-faint transition-colors hover:border-minfy-indigo hover:text-minfy-indigo"
      >
        i
      </button>

      {shown && (
        <span
          role="tooltip"
          // The badge above is `.t-eyebrow`, so uppercase and letter-spacing are
          // inherited; the panel is prose and has to opt out of both.
          className="animate-enter absolute right-0 top-6 z-10 w-60 border border-hairline bg-surface p-3 text-left font-normal normal-case tracking-normal shadow-sm"
        >
          <span className="t-eyebrow block text-ink-muted">Maturity tiers</span>
          <span className="mt-2 block">
            {MATURITY_SCALE.map((tier) => (
              <span
                key={tier.label}
                className={`t-caption flex items-baseline justify-between gap-4 py-0.5 ${
                  tier.label === current ? 'font-semibold text-ink' : 'text-ink-muted'
                }`}
              >
                <span>{tier.label}</span>
                <span className="tnum">{tier.range}</span>
              </span>
            ))}
          </span>
          <span className="t-caption mt-2 block text-[0.6875rem] leading-snug text-ink-faint">
            {MATURITY_BOUND_NOTE}
          </span>
        </span>
      )}
    </span>
  )
}

/**
 * Downloads the PDF report.
 *
 * The blob is turned into a temporary object URL and clicked through a
 * synthetic anchor — the standard way to give a fetched file the server's
 * filename. The URL is revoked immediately afterwards so the blob is not
 * retained for the life of the page.
 */
function DownloadReportButton({ reviewId }: { reviewId: string }) {
  const [state, setState] = useState<'idle' | 'working'>('idle')
  const [error, setError] = useState('')

  async function run() {
    setState('working')
    setError('')
    try {
      const { blob, filename } = await downloadReport(reviewId)
      const url = URL.createObjectURL(blob)
      try {
        const anchor = document.createElement('a')
        anchor.href = url
        anchor.download = filename
        document.body.appendChild(anchor)
        anchor.click()
        anchor.remove()
      } finally {
        URL.revokeObjectURL(url)
      }
    } catch (caught: unknown) {
      setError(
        caught instanceof ApiError ? caught.message : 'Could not download the report.',
      )
    } finally {
      setState('idle')
    }
  }

  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <button
        type="button"
        onClick={run}
        disabled={state === 'working'}
        className="t-body flex items-center gap-2 border border-minfy-navy px-4 py-2.5 font-semibold text-minfy-navy transition-colors duration-150 hover:bg-minfy-navy hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
      >
        <svg viewBox="0 0 16 16" aria-hidden="true" className="size-3.5 fill-current">
          <path d="M7.25 1.5h1.5v7.19l2.22-2.22 1.06 1.06L8 12.06 3.97 7.53l1.06-1.06 2.22 2.22V1.5Z M2.5 12.5h11V14h-11Z" />
        </svg>
        {state === 'working' ? 'Preparing PDF…' : 'Download Report'}
      </button>
      {error && (
        <span role="alert" className="t-caption text-sev-high">
          {error}
        </span>
      )}
    </span>
  )
}

function DeltaSummary({ delta }: { delta: ScoreDelta }) {
  const moved = delta.pillars.filter((pillar) => pillar.change !== 0)

  // Border and sunken surface rather than a pastel block: this sits directly
  // above the pastel executive summary, and two filled blocks in a row fight
  // each other for the top of the page.
  return (
    <section className="mt-10 border-l-2 border-minfy-indigo bg-surface-sunken px-5 py-4.5">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h3 className="t-heading">Change since the previous review</h3>
        <p className="tnum t-caption text-ink-muted">
          {delta.previous_overall_score.toFixed(1)} → {delta.current_overall_score.toFixed(1)}
        </p>
        <ChangeBadge change={delta.change} />
      </div>

      {/* Value before label, and the value a step up in size. The three counts are what
          a reader scans this panel for; when both halves sat at caption weight the row
          read as one long sentence of alternating words. */}
      <dl className="mt-3.5 flex flex-wrap gap-x-7 gap-y-2">
        <Stat label="Resolved" value={delta.resolved_checks.length} tone="text-verdict-pass" />
        <Stat label="New" value={delta.new_checks.length} tone="text-sev-high" />
        <Stat label="Still open" value={delta.unchanged_failures.length} tone="text-ink" />
      </dl>

      {moved.length > 0 ? (
        /*
          A three-part row — name, scores, badge — with the numbers in their own fixed
          column rather than pushed right by `justify-between`. Previously a short pillar
          name and a long one put their figures in different places, so the two grid
          columns could not be read down.
        */
        <ul className="mt-4 grid gap-x-10 gap-y-2 border-t border-ink/10 pt-3 sm:grid-cols-2">
          {moved.map((pillar) => (
            <li
              key={`${pillar.framework}-${pillar.pillar_id}`}
              className="t-caption flex items-baseline gap-3"
            >
              <span className="min-w-0 flex-1 truncate" title={pillar.pillar_name}>
                {pillar.pillar_name}
              </span>
              <span className="tnum w-[4.5rem] shrink-0 text-right text-ink-muted">
                {pillar.previous_score.toFixed(0)} → {pillar.current_score.toFixed(0)}
              </span>
              <span className="w-[3.25rem] shrink-0 text-right">
                <ChangeBadge change={pillar.change} compact />
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="t-caption mt-3.5 text-ink-muted">
          No pillar score changed between the two reviews.
        </p>
      )}
    </section>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    /* DOM order stays dt-then-dd, which is what a definition list requires and what a
       screen reader reads; only the visual order is swapped. */
    <div className="flex items-baseline gap-1.5">
      <dt className="t-caption order-2 text-ink-muted">{label}</dt>
      <dd className={`tnum t-body order-1 font-semibold ${tone}`}>{value}</dd>
    </div>
  )
}

/**
 * Flat pastel block per framework, as the live site does for feature cards.
 *
 * Presentation only — it separates the two frameworks at a glance and carries no
 * score or severity meaning. An unrecognised framework falls back to the teal
 * block rather than to no block, so a third framework would still read as a card.
 */
const FRAMEWORK_BLOCK: Record<string, string> = {
  aws_waf: 'bg-pastel-sky',
  trust7: 'bg-pastel-mint',
}

/**
 * Component trade-offs the submitter's stated use case makes relevant.
 *
 * Rendered only when context was supplied AND the remediate stage could ground a
 * recommendation in something that context actually states. Both conditions
 * matter: no context means there is nothing to be specific about, and a note the
 * backend could not tie to a quoted phrase was discarded before it ever reached
 * here, so an empty list is a correct and common outcome rather than a failure
 * worth apologising for on screen.
 *
 * Last on the page on purpose. This is the one section that is advisory rather
 * than assessed — it weighs choices the design got to make, not gaps it failed
 * to close — so it sits after the audit trail rather than competing with the
 * roadmap for the reader's attention.
 */
function UseCaseNotes({ notes, context }: { notes: UseCaseNote[]; context: string }) {
  if (!context || notes.length === 0) return null

  return (
    <section className="mt-16" data-testid="use-case-notes">
      <h3 className="t-eyebrow text-ink-muted">For your stated use case</h3>
      <p className="t-caption mt-1.5 max-w-prose text-ink-faint">
        Trade-offs that the purpose and use case you submitted make relevant.
        Each one quotes the part of your context it rests on.
      </p>

      <ul className="mt-4 space-y-5">
        {notes.map((note, index) => (
          <li key={index} className="border-l-2 border-minfy-indigo bg-pastel-sky px-5 py-4">
            <p className="t-heading">{note.component}</p>
            <StructuredText
              text={note.recommendation}
              className="t-body mt-1.5 max-w-prose text-pretty"
            />
            {/*
              The quote is shown, not just used. It is what separates a
              recommendation grounded in this submission from a generic
              comparison, and a reader can only judge that if they can see it.
            */}
            <p className="t-caption mt-2.5 border-t border-ink/15 pt-2 text-ink-muted">
              <span className="font-semibold">Based on what you wrote:</span>{' '}
              <q className="italic">{note.grounded_in}</q>
            </p>
          </li>
        ))}
      </ul>
    </section>
  )
}

/**
 * "Fix these first" — the few most urgent gaps, directly under the assessment.
 *
 * Curation, not computation. Every item is a finding the review already carries,
 * selected by `priorityFocus`, which reads the same `prioritizedActions` the
 * roadmap does. Nothing is re-ranked and nothing is asked of the model.
 *
 * It deliberately repeats what the roadmap says lower down. The assessment ends
 * with a reader who now knows the score and has thirteen pillars in front of
 * them; naming the handful that matter at that moment is worth the repetition,
 * and each row says which phase it belongs to so the connection is explicit.
 */
function PriorityFocus({ findings }: { findings: Finding[] }) {
  const focus = priorityFocus(findings)
  if (focus.length === 0) return null

  return (
    <section
      className="mt-6 border-l-2 border-sev-high bg-surface-sunken px-5 py-4.5"
      data-testid="priority-focus"
    >
      <h4 className="t-heading">Fix these first</h4>
      <p className="t-caption mt-1 text-ink-muted">
        The most urgent gaps from this review, in the order to take them on.
      </p>
      <ol className="mt-3 space-y-2">
        {focus.map((finding, index) => (
          <li
            key={`${finding.framework}-${finding.check_id}`}
            className="flex items-start gap-3"
          >
            <span
              aria-hidden="true"
              className="tnum t-body mt-px w-4 shrink-0 text-right font-semibold text-sev-high"
            >
              {index + 1}
            </span>
            <span className="min-w-0">
              <span className="t-body block font-medium">{finding.title}</span>
              <span className="t-caption mt-0.5 block text-ink-muted">
                {finding.pillar_id.replace(/_/g, ' ')}
                <span aria-hidden="true"> · </span>
                {finding.severity} severity
                <span aria-hidden="true"> · </span>
                {PHASE_LABEL[phaseFor(finding)]}
              </span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  )
}

/**
 * The checks behind one pillar's score, in the order the findings list uses.
 *
 * A pure regroup of data the review already carries — no request, no new field,
 * and deliberately no model call: `evidence` is written for every check by the
 * evaluate stage, whose schema requires it, so the reasoning for a pillar score
 * already exists and only needs collecting.
 *
 * Matched on framework AND pillar_id: `sustainability` exists in both frameworks,
 * and keying on pillar_id alone would show TRUST-7's checks under the AWS pillar.
 */
export function checksForPillar(
  findings: readonly Finding[],
  framework: string,
  pillarId: string,
): Finding[] {
  const rank: Record<Finding['status'], number> = {
    fail: 0,
    partial: 1,
    pass: 2,
    not_applicable: 3,
  }
  return findings
    .filter((f) => f.framework === framework && f.pillar_id === pillarId)
    .slice()
    .sort((a, b) => rank[a.status] - rank[b.status] || a.check_id.localeCompare(b.check_id))
}

function FrameworkSection({
  framework,
  findings,
  detection,
}: {
  framework: FrameworkScore
  findings: Finding[]
  /** Threaded down so a wholly-skipped pillar can explain itself. */
  detection?: AiDetection
}) {
  const block = FRAMEWORK_BLOCK[framework.framework] ?? 'bg-pastel-teal'
  return (
    <section className={`${block} px-5 py-5 sm:px-6 sm:py-6`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-ink/15 pb-2">
        <h4 className="t-title">{framework.framework_name}</h4>
        <p className="t-caption text-ink-muted">
          <span className="tnum font-semibold text-ink">{framework.score.toFixed(1)}</span>
          <span aria-hidden="true"> · </span>
          {maturityFor(framework.score)}
          <span aria-hidden="true"> · </span>
          {framework.pillars.length} pillars
        </p>
      </div>
      {/* items-start so an expanded pillar grows on its own rather than
          stretching every sibling cell in its row to match. */}
      <div className="grid items-start gap-x-10 gap-y-6 pt-6 sm:grid-cols-2 lg:grid-cols-3">
        {framework.pillars.map((pillar) => (
          <PillarCell
            key={pillar.pillar_id}
            pillar={pillar}
            checks={checksForPillar(findings, framework.framework, pillar.pillar_id)}
            detection={detection}
          />
        ))}
      </div>
    </section>
  )
}

function PillarCell({
  pillar,
  checks,
  detection,
}: {
  pillar: PillarScore
  checks: Finding[]
  detection?: AiDetection
}) {
  const [open, setOpen] = useState(false)
  const unevaluated = pillar.checks_evaluated === 0
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <p className="t-body flex min-w-0 items-baseline gap-1.5 font-medium">
          <span className="truncate" title={pillar.pillar_name}>
            {pillar.pillar_name}
          </span>
          <PillarScoreHint pillar={pillar} />
        </p>
        <p className="tnum t-body shrink-0 font-semibold">
          {unevaluated ? '—' : pillar.score.toFixed(0)}
        </p>
      </div>
      {/* Track is ink at low alpha, not the hairline grey: these bars now sit on
          a pastel block, where a light grey track all but disappears. */}
      <div
        className="mt-2 h-1.5 w-full bg-ink/15"
        role="img"
        aria-label={`${pillar.pillar_name}: ${
          unevaluated ? 'not evaluated' : `${pillar.score} out of 100, ${maturityFor(pillar.score)}`
        }`}
      >
        {!unevaluated && (
          <div
            className={`h-full transition-[width] duration-700 ease-out ${scoreToneClass(pillar.score)}`}
            style={{ width: `${Math.min(100, Math.max(0, pillar.score))}%` }}
          />
        )}
      </div>
      <p className="t-caption mt-1.5 text-ink-muted" data-testid={`pillar-caption-${pillar.pillar_id}`}>
        {unevaluated ? (
          /*
            Was the bare string "Not applicable to this design" — a conclusion with
            no argument attached. For a pillar whose checks all turn on there being
            an AI/ML component, that sentence was the entire visible trace of a
            decision worth nineteen of the forty-five checks, and neither we nor a
            judge could check it.

            Now it carries the detection record's own reasoning, including the list
            of components that were searched, so it can be disagreed with. The
            sentence comes from the backend, so this and the panel above cannot
            describe the same record differently.
          */
          <>
            {pillarNotApplicableReason(detection)}
            {disagreesWithPillar(detection, pillar) && (
              <strong className="mt-1 block font-medium text-sev-medium">
                AI/ML evidence was found in this design — this not-applicable is
                worth checking.
              </strong>
            )}
          </>
        ) : (
          <>
            {maturityFor(pillar.score)}
            <span aria-hidden="true"> · </span>
            <span className="tnum">
              {pillar.checks_passed}/{pillar.checks_evaluated} passed
            </span>
            {pillar.checks_evaluated < pillar.checks_total && (
              <span className="tnum text-ink-faint">
                {' '}
                ({pillar.checks_total - pillar.checks_evaluated} n/a)
              </span>
            )}
          </>
        )}
      </p>

      {/*
        The reasoning behind the number, from data the review already carries.
        Every check has `evidence` — the evaluate stage's schema requires it — so
        this is a regroup, not a synthesis, and costs no model call.
      */}
      {checks.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            className="t-caption mt-2 flex items-center gap-1 text-minfy-indigo underline underline-offset-2 transition-colors hover:text-minfy-blue"
          >
            <Chevron open={open} />
            {open ? 'Hide detail' : 'Explain more'}
          </button>

          {open && (
            <ul
              className="animate-enter mt-2 space-y-2.5 border-t border-ink/15 pt-2.5"
              data-testid={`pillar-explain-${pillar.framework}-${pillar.pillar_id}`}
            >
              {checks.map((check) => (
                <li key={check.check_id}>
                  <div className="flex items-baseline gap-2">
                    <StatusTag status={check.status} />
                    <span className="t-caption font-medium text-ink">{check.title}</span>
                  </div>
                  {/*
                    One line of reasoning per check, the model's own words. A check
                    whose evidence came back empty says so rather than being given
                    text it never produced — the schema requires the field, but
                    OpenRouter documents that enforcement varies by provider.
                  */}
                  <p className="t-caption mt-0.5 text-pretty text-ink-muted">
                    {check.evidence || 'No reasoning was recorded for this check.'}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

/**
 * What the pillar number is and how it was reached — hover or tap.
 *
 * Same shape as `MaturityScaleHint`, including the separate hover and pin flags:
 * sharing one would let a mouse click close the panel a hover had just opened.
 */
function PillarScoreHint({ pillar }: { pillar: PillarScore }) {
  const [pinned, setPinned] = useState(false)
  const [hovered, setHovered] = useState(false)
  const shown = pinned || hovered
  const unevaluated = pillar.checks_evaluated === 0

  return (
    <span
      className="relative inline-flex shrink-0"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        type="button"
        onClick={() => setPinned((value) => !value)}
        aria-expanded={shown}
        aria-label={`How the ${pillar.pillar_name} score was reached`}
        className="flex size-4 items-center justify-center rounded-full border border-ink-faint text-[9px] font-bold text-ink-faint transition-colors hover:border-minfy-indigo hover:text-minfy-indigo"
      >
        i
      </button>

      {shown && (
        <span
          role="tooltip"
          className="animate-enter absolute left-0 top-6 z-10 w-56 border border-hairline bg-surface p-3 text-left font-normal shadow-sm"
        >
          <span className="t-caption block text-ink-muted">
            {unevaluated ? (
              'No check in this pillar applied to this design, so it is not scored.'
            ) : (
              <>
                Weighted by severity across the{' '}
                <span className="tnum">{pillar.checks_evaluated}</span> applicable{' '}
                {pillar.checks_evaluated === 1 ? 'check' : 'checks'} in this pillar.
                A partial verdict earns half credit; not-applicable checks are left
                out of the total rather than counted as failures.
              </>
            )}
          </span>
          <span className="t-caption mt-2 block text-ink-faint">
            Open “Explain more” for the verdict and reasoning on each check.
          </span>
        </span>
      )}
    </span>
  )
}

const SEVERITY_ORDER: readonly Severity[] = ['high', 'medium', 'low']
const SEVERITY_HEADING: Record<Severity, string> = {
  high: 'High severity',
  medium: 'Medium severity',
  low: 'Low severity',
}

function FindingsList({ findings }: { findings: Finding[] }) {
  const [showPassing, setShowPassing] = useState(false)

  const open = findings.filter(
    (finding) => finding.status === 'fail' || finding.status === 'partial',
  )
  const rest = findings.filter(
    (finding) => finding.status === 'pass' || finding.status === 'not_applicable',
  )

  return (
    <section className="mt-16" data-testid="detailed-findings">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
        <h3 className="t-eyebrow text-ink-muted">Detailed findings</h3>
        {rest.length > 0 && (
          <button
            type="button"
            onClick={() => setShowPassing((value) => !value)}
            aria-expanded={showPassing}
            className="t-caption text-ink-muted underline underline-offset-2 transition-colors hover:text-ink"
          >
            {showPassing
              ? 'Hide passing checks'
              : `Show ${rest.length} passing / not-applicable checks`}
          </button>
        )}
      </div>

      {/*
        This paragraph used to exist to tell a reader that the identical-looking
        list above was the action list and this one was only the record. There is no
        list above any more, so the disclaimer goes with it — what is left says what
        the grouping means and where effort went.
      */}
      <p className="t-caption mt-1.5 max-w-prose text-ink-faint">
        Every open finding, grouped by severity — worst first. Each one carries the
        effort it was estimated at. Passed and not-applicable checks are the
        complete record and stay behind the toggle above.
      </p>

      {open.length === 0 ? (
        <div className="mt-6 flex items-center gap-3 border-l-2 border-verdict-pass bg-surface-sunken px-4 py-4">
          <svg viewBox="0 0 16 16" aria-hidden="true" className="size-4 shrink-0 fill-verdict-pass">
            <path d="M8 1 A7 7 0 1 1 8 15 A7 7 0 1 1 8 1 Z M6.9 10.8 L11.8 5.9 L10.9 5 L6.9 9 L5.1 7.2 L4.2 8.1 Z" />
          </svg>
          <p className="t-body">No gaps found — every applicable check passed.</p>
        </div>
      ) : (
        // Grouped by severity rather than one flat list: a reviewer scanning for
        // blockers should not have to read past the low-severity items.
        SEVERITY_ORDER.map((severity) => {
          const group = open.filter((finding) => finding.severity === severity)
          if (group.length === 0) return null
          return (
            <SeverityGroup
              key={severity}
              heading={SEVERITY_HEADING[severity]}
              severity={severity}
              findings={group}
              initiallyOpen
            />
          )
        })
      )}

      {showPassing && rest.length > 0 && (
        <div className="animate-enter">
          <SeverityGroup heading="Passing and not applicable" findings={rest} muted />
        </div>
      )}
    </section>
  )
}

/**
 * One collapsible severity group. Closed by default.
 *
 * Closed-by-default because a full review is 45 checks: expanded, the page opens
 * on a wall of text and the reviewer has to scroll to find whether there are any
 * blockers at all. The count in the header is the thing they actually came for, so
 * it stays visible whether the group is open or shut.
 *
 * A real `<button>` with `aria-expanded`, not a click handler on a div — this has
 * to work from the keyboard, and the count belongs inside the accessible name so a
 * screen reader announces "High severity, 14, collapsed".
 */
function SeverityGroup({
  heading,
  severity,
  findings,
  muted,
  initiallyOpen = false,
}: {
  heading: string
  severity?: Severity
  findings: Finding[]
  muted?: boolean
  /**
   * Open findings default to EXPANDED now that this is the only findings view.
   *
   * The closed default made sense while the roadmap sat above: this list was the
   * record, the roadmap was the thing to act on, and opening the record by default
   * put a wall of text between the reader and the actions. With the roadmap gone
   * this IS the action list, and a primary view that opens closed asks the reader
   * to click before they can see whether there is anything to do.
   *
   * The passing / not-applicable group keeps the closed default and its own toggle
   * — it is the audit trail, and it is the half a reviewer is not scanning for.
   */
  initiallyOpen?: boolean
}) {
  const [open, setOpen] = useState(initiallyOpen)

  return (
    <div className="mt-8">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="group flex w-full items-center gap-2.5 py-1 text-left"
      >
        <Chevron open={open} />
        {severity && <SeverityMark severity={severity} decorative />}
        <h4 className={`t-heading ${muted ? 'text-ink-muted' : ''}`}>{heading}</h4>
        <span className="tnum t-caption text-ink-muted">({findings.length})</span>
        <span aria-hidden="true" className="h-px flex-1 bg-hairline" />
      </button>

      {open && (
        <ol className="animate-enter divide-y divide-hairline">
          {findings.map((finding) => (
            <FindingRow
              key={`${finding.framework}-${finding.check_id}`}
              finding={finding}
            />
          ))}
        </ol>
      )}
    </div>
  )
}

/** Shared disclosure affordance, so both levels of the accordion rotate alike. */
function Chevron({ open, className }: { open: boolean; className?: string }) {
  return (
    <svg
      viewBox="0 0 12 12"
      aria-hidden="true"
      className={`size-3 shrink-0 fill-none stroke-ink-muted stroke-2 transition-transform duration-200 ${
        open ? 'rotate-90' : ''
      } ${className ?? ''}`}
    >
      <path d="M4 2.5 L8 6 L4 9.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/**
 * One finding, collapsed to its title and how much it touches.
 *
 * The collapsed row carries the title, the status tag, and the affected-component
 * count — enough to decide whether to open it. Evidence and remediation are the
 * long text and are what expanding reveals.
 *
 * The count is deliberately a count and not the list of names: names are as long as
 * the design's naming convention allows, and a collapsed row has to stay one line.
 */
function FindingRow({ finding }: { finding: Finding }) {
  const [open, setOpen] = useState(false)
  const muted = finding.status === 'pass' || finding.status === 'not_applicable'
  const affected = finding.affected_components.length

  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-start gap-4 py-4 text-left transition-colors hover:bg-surface-sunken"
      >
        <span
          className="tnum t-caption mt-0.5 w-6 shrink-0 text-right text-ink-faint"
          aria-hidden={finding.priority === 0}
        >
          {finding.priority > 0 ? finding.priority : '·'}
        </span>
        <Chevron open={open} className="mt-1.5" />

        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
            <h5 className={`t-heading ${muted ? 'font-medium text-ink-muted' : ''}`}>
              {finding.title}
            </h5>
            <StatusTag status={finding.status} />
          </span>

          <span className="t-caption mt-1.5 flex flex-wrap items-center gap-x-2 text-ink-muted">
            <SeverityMark severity={finding.severity} />
            <span>{finding.pillar_id.replace(/_/g, ' ')}</span>
            <span aria-hidden="true">·</span>
            <span className="font-mono text-ink-faint">{finding.check_id}</span>
            {/*
              The effort phase, which used to be the roadmap's grouping axis and is
              now a per-item detail. On the COLLAPSED row rather than inside the
              expanded body: it is what the removed section let a reader scan for,
              and a tag only visible after a click would not replace that.
              Suppressed on passed and not-applicable checks — there is no work to
              schedule, so a phase on one is noise.
            */}
            {!muted && (
              <>
                <span aria-hidden="true">·</span>
                <span data-testid={`effort-${finding.check_id}`}>
                  {PHASE_LABEL[phaseFor(finding)]}
                </span>
              </>
            )}
            {affected > 0 && (
              <>
                <span aria-hidden="true">·</span>
                <span className="tnum">
                  {affected} component{affected === 1 ? '' : 's'}
                </span>
              </>
            )}
          </span>
        </span>
      </button>

      {open && (
        <div className="animate-enter pb-6 pl-14">
          {finding.evidence && (
            <p className="t-body max-w-prose text-ink-muted">{finding.evidence}</p>
          )}

          {finding.remediation && (
            <div className="mt-4 max-w-prose border-l-2 border-minfy-indigo/40 bg-surface-sunken px-4 py-3">
              <p className="t-eyebrow text-ink-muted">
                Remediation
                {finding.remediation_effort && (
                  <span className="font-normal normal-case tracking-normal">
                    {' '}
                    · {finding.remediation_effort} effort
                  </span>
                )}
              </p>
              <StructuredText text={finding.remediation} className="t-body mt-1.5" />

              {/*
                What the remediation was checked against, when it was checked.

                Monospace and quoted, the same treatment `AiDetectionPanel` gives an
                extracted excerpt, and for the same reason: this is the submitted
                material, not our prose about it, and the difference should be
                visible. It is the one part of the block a reviewer can verify
                against their own document.

                The label is deliberately narrow. `remediation_grounded_in` means
                the model quoted a phrase and that phrase was found in the design
                source — nothing was checked about whether the remediation is
                correct, appropriate, or complete. "Grounded in the source" says
                exactly that much. Anything reading as "verified" would claim a
                check nobody ran, on the most actionable text in the review.

                Absent when empty, with no tick and no placeholder. Per Segment 7 an
                ungrounded remediation is blanked entirely, so an empty quote beside
                real remediation text should not occur — and if it ever does, the
                honest render is silence rather than a tick or an accusation.
              */}
              {finding.remediation_grounded_in && (
                <div
                  className="mt-3 border-t border-hairline pt-2.5"
                  data-testid={`grounding-${finding.check_id}`}
                >
                  <p className="t-caption flex items-center gap-1.5 text-ink-muted">
                    <svg
                      viewBox="0 0 16 16"
                      aria-hidden="true"
                      className="size-3.5 shrink-0 fill-verdict-pass"
                    >
                      <path d="M6.9 11.4 L2.8 7.3 L3.9 6.2 L6.9 9.2 L12.1 4 L13.2 5.1 Z" />
                    </svg>
                    Grounded in the source
                  </p>
                  <p className="t-caption mt-1 break-words font-mono text-ink-muted">
                    “{finding.remediation_grounded_in}”
                  </p>
                </div>
              )}
            </div>
          )}

          {affected > 0 && (
            <p className="t-caption mt-3 text-ink-faint">
              Affects: {finding.affected_components.join(', ')}
            </p>
          )}
        </div>
      )}
    </li>
  )
}

function StatusTag({ status }: { status: Finding['status'] }) {
  const label: Record<Finding['status'], string> = {
    fail: 'Not met',
    partial: 'Partial',
    pass: 'Met',
    not_applicable: 'N/A',
  }
  const tone: Record<Finding['status'], string> = {
    fail: 'border-sev-high/40 bg-sev-high/8 text-sev-high',
    partial: 'border-sev-medium/40 bg-sev-medium/8 text-sev-medium',
    pass: 'border-verdict-pass/40 bg-verdict-pass/8 text-verdict-pass',
    not_applicable: 'border-hairline text-ink-faint',
  }
  return (
    <span
      className={`t-eyebrow shrink-0 border px-1.5 py-0.5 text-[10px] tracking-wider ${tone[status]}`}
    >
      {label[status]}
    </span>
  )
}
