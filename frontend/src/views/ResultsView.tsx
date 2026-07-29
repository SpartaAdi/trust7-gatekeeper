import { useEffect, useState } from 'react'

import { ApiError, createShareLink, downloadReport, getReview, shareUrl } from '../api'
import { ChangeBadge } from '../components/ChangeBadge'
import { SeverityMark } from '../components/SeverityMark'
import {
  MATURITY_BOUND_NOTE,
  MATURITY_SCALE,
  maturityFor,
  scoreToneClass,
  type MaturityLabel,
} from '../maturity'
import type {
  Finding,
  FrameworkScore,
  PillarScore,
  ReviewResult,
  ScoreDelta,
  Severity,
} from '../types'
import {
  PHASE_BLURB,
  PHASE_LABEL,
  PHASE_ORDER,
  flattenActions,
  prioritizedActions,
  type Phase,
} from './roadmap'

interface Props {
  reviewId: string
  onReReview: () => void
  onStartOver: () => void
  onBackToHistory: () => void
}

export function ResultsView({
  reviewId,
  onReReview,
  onStartOver,
  onBackToHistory,
}: Props) {
  const [result, setResult] = useState<ReviewResult | null>(null)
  const [error, setError] = useState('')

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
          <p className="t-body mt-3 max-w-prose text-pretty">{result.summary}</p>
        )}
        <div className="mt-6 space-y-10">
          {result.frameworks.map((framework) => (
            <FrameworkSection key={framework.framework} framework={framework} />
          ))}
        </div>
      </section>

      {/* (c) Action roadmap — the single prioritized action view. */}
      <ActionRoadmap findings={result.findings} />

      {/* (d) Detailed findings — the audit trail. */}
      <FindingsList findings={result.findings} />

      <footer className="mt-16 flex flex-wrap items-center gap-x-6 gap-y-4 border-t border-hairline pt-8">
        <button
          type="button"
          onClick={onReReview}
          className="t-body bg-minfy-indigo px-5 py-2.5 font-semibold text-white transition-colors duration-150 hover:bg-minfy-blue"
        >
          Re-review a revised design
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

export const FIX_IT_PREAMBLE =
  'Here is my architecture. A review found the following gaps — please revise ' +
  'the diagram to address each one:'

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

  const numbered = actions.map(
    (finding, index) => `${index + 1}. ${finding.remediation || finding.title}`,
  )
  return `${FIX_IT_PREAMBLE}\n\n${numbered.join('\n')}\n`
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
      <button
        type="button"
        onClick={run}
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
      {/* Polite, not an alert: a copy failure is recoverable and the user is
          already looking at the button they just pressed. */}
      <span aria-live="polite" className="t-caption text-sev-high">
        {state === 'failed' ? 'Could not copy to the clipboard.' : ''}
      </span>
    </span>
  )
}

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
 * "Action roadmap" — the single prioritized action view.
 *
 * This replaces the pair it grew out of: a flat top-ten shortlist and a separate
 * three-phase plan, which listed the same work twice under two headings and left
 * the reader to reconcile them. One section now answers "what do I do", sequenced
 * Immediate -> Short-term -> Structural.
 *
 * Selection is `prioritizedActions`: `groupByPhase` for the phase, then one entry
 * per pillar within each phase, ordered by severity. The dedupe lives in the
 * presentation layer, NOT in `groupByPhase` — that function is mirrored in
 * `backend/roadmap.py` and pinned by `fixtures/roadmap_cases.json`, and it stays a
 * pure partition so the PDF keeps printing every open finding.
 *
 * Nothing is hidden by the dedupe: Detailed Findings below is the complete record.
 *
 * Grouping is `groupByPhase`, which is pure: no request, no new field, and the same
 * findings always land in the same phases. The rule is documented in `roadmap.ts`.
 *
 * Collapsed by default, one level rather than the findings list's two: the phase
 * counts are what a reader scans for, and the roadmap sits above the full findings
 * list which already offers per-finding disclosure.
 */
function ActionRoadmap({ findings }: { findings: Finding[] }) {
  const grouped = prioritizedActions(findings)
  const total = PHASE_ORDER.reduce((sum, phase) => sum + grouped[phase].length, 0)
  if (total === 0) return null

  return (
    <section className="mt-12" data-testid="roadmap">
      <h3 className="t-eyebrow text-ink-muted">
        Action roadmap
        <span className="tnum font-normal normal-case tracking-normal text-ink-faint">
          {' '}
          · {total} prioritized {total === 1 ? 'action' : 'actions'} in three phases
        </span>
      </h3>
      {/*
        Says what the section is for in one line, so it is not mistaken for the
        audit trail below. "Ordered by effort" names the actual grouping signal —
        the phases come from `remediation_effort`, not from severity.
      */}
      <p className="t-caption mt-1.5 text-ink-faint">
        Prioritized next actions, ordered by effort. Open findings only.
      </p>

      {PHASE_ORDER.map((phase) => (
        <PhaseGroup key={phase} phase={phase} findings={grouped[phase]} />
      ))}
    </section>
  )
}

/**
 * One collapsible phase. Same disclosure shape as `SeverityGroup`: a real button
 * carrying `aria-expanded`, the count inside the accessible name, and the shared
 * `Chevron` so every accordion on the page rotates alike.
 *
 * An empty phase still renders, greyed and not expandable. "Structural (0)" is a
 * result — it says there is no architecture work — whereas an absent heading leaves
 * the reader to wonder whether the phase was omitted or never considered.
 */
function PhaseGroup({ phase, findings }: { phase: Phase; findings: Finding[] }) {
  const [open, setOpen] = useState(false)
  const empty = findings.length === 0

  return (
    <div className="mt-8" data-testid={`phase-${phase}`}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        disabled={empty}
        className="group flex w-full items-baseline gap-2.5 py-1 text-left disabled:cursor-default"
      >
        <Chevron open={open} className={empty ? 'invisible' : ''} />
        <h4 className={`t-heading ${empty ? 'text-ink-faint' : ''}`}>
          {PHASE_LABEL[phase]}
        </h4>
        <span className="tnum t-caption text-ink-muted">({findings.length})</span>
        <span className="t-caption hidden text-ink-faint sm:block">
          {PHASE_BLURB[phase]}
        </span>
        <span aria-hidden="true" className="h-px flex-1 bg-hairline" />
      </button>

      {open && !empty && (
        <ol className="animate-enter mt-1 divide-y divide-hairline border-y border-hairline">
          {findings.map((finding, index) => (
            <li
              key={`${finding.framework}-${finding.check_id}`}
              className="flex items-start gap-4 py-3"
            >
              {/*
                A visible ordinal, restarting at 1 in each phase. The rows were
                already an <ol>, but with `list-style: none` the sequence existed
                only in the markup — a reader scanning for "the first thing to do"
                had nothing to anchor on, and the severity mark alone reads as a
                bullet. Numbering per phase rather than continuously because the
                phases are worked in sequence: item 1 of Short-term is the first
                thing to do in that phase, not the twelfth thing overall.
                `aria-hidden` because the <ol> already conveys position.
              */}
              <span
                aria-hidden="true"
                className="tnum t-body mt-px w-5 shrink-0 text-right font-semibold text-minfy-indigo"
              >
                {index + 1}
              </span>
              {/*
                Not decorative here: unlike the findings list, the phase rows do not
                state the severity in text, so the mark is the only place it appears
                and needs its accessible name.
              */}
              <span className="mt-1.5 shrink-0">
                <SeverityMark severity={finding.severity} />
              </span>
              <div className="min-w-0">
                <p className="t-body font-medium">{finding.title}</p>
                {/*
                  Verbatim, like everywhere else tonight: `remediation` is the model's
                  own imperative text and is rendered unmodified. A finding with none
                  says so rather than having something invented for it.
                */}
                <p className="t-body mt-1 max-w-prose text-pretty text-ink-muted">
                  {finding.remediation || 'No remediation text was generated for this check.'}
                </p>
                <p className="t-caption mt-1 text-ink-faint">
                  {finding.pillar_id.replace(/_/g, ' ')}
                  {finding.remediation_effort && (
                    <>
                      <span aria-hidden="true"> · </span>
                      {finding.remediation_effort} effort
                    </>
                  )}
                  {finding.affected_components.length > 0 && (
                    <>
                      <span aria-hidden="true"> · </span>
                      {finding.affected_components.length}{' '}
                      {finding.affected_components.length === 1
                        ? 'component'
                        : 'components'}
                    </>
                  )}
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
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
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="t-heading">Change since the previous review</h3>
        <p className="tnum t-caption text-ink-muted">
          {delta.previous_overall_score.toFixed(1)} → {delta.current_overall_score.toFixed(1)}
        </p>
        <ChangeBadge change={delta.change} />
      </div>

      <dl className="mt-3 flex flex-wrap gap-x-8 gap-y-1">
        <Stat label="Resolved" value={delta.resolved_checks.length} tone="text-verdict-pass" />
        <Stat label="New" value={delta.new_checks.length} tone="text-sev-high" />
        <Stat label="Still open" value={delta.unchanged_failures.length} tone="text-ink" />
      </dl>

      {moved.length > 0 ? (
        <ul className="mt-4 grid gap-x-10 gap-y-1.5 sm:grid-cols-2">
          {moved.map((pillar) => (
            <li
              key={`${pillar.framework}-${pillar.pillar_id}`}
              className="t-caption flex items-baseline justify-between gap-3"
            >
              <span className="truncate">{pillar.pillar_name}</span>
              <span className="tnum flex shrink-0 items-baseline gap-2 text-ink-muted">
                {pillar.previous_score.toFixed(0)} → {pillar.current_score.toFixed(0)}
                <ChangeBadge change={pillar.change} compact />
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="t-caption mt-3 text-ink-muted">
          No pillar score changed between the two reviews.
        </p>
      )}
    </section>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <dt className="t-caption text-ink-muted">{label}</dt>
      <dd className={`tnum t-caption font-semibold ${tone}`}>{value}</dd>
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

function FrameworkSection({ framework }: { framework: FrameworkScore }) {
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
      <div className="grid gap-x-10 gap-y-6 pt-6 sm:grid-cols-2 lg:grid-cols-3">
        {framework.pillars.map((pillar) => (
          <PillarCell key={pillar.pillar_id} pillar={pillar} />
        ))}
      </div>
    </section>
  )
}

function PillarCell({ pillar }: { pillar: PillarScore }) {
  const unevaluated = pillar.checks_evaluated === 0
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <p className="t-body truncate font-medium" title={pillar.pillar_name}>
          {pillar.pillar_name}
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
      <p className="t-caption mt-1.5 text-ink-muted">
        {unevaluated ? (
          'Not applicable to this design'
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
    </div>
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
        Says what this section is for, and what it is not. Without it a reader
        arriving from the roadmap above sees a second list of the same gaps and
        reasonably reads it as more work to do. The roadmap is the action list;
        this is the record, and it repeats the remediation text for reference
        rather than as a second set of instructions.
      */}
      <p className="t-caption mt-1.5 max-w-prose text-ink-faint">
        Complete evaluation record, including passed and not-applicable checks.
        Remediation is repeated here for reference — the Action roadmap above is
        the prioritized list to work from.
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
}: {
  heading: string
  severity?: Severity
  findings: Finding[]
  muted?: boolean
}) {
  const [open, setOpen] = useState(false)

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
              <p className="t-body mt-1.5">{finding.remediation}</p>
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
