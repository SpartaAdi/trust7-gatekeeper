import { useId, useState } from 'react'
import { CaveatPanel } from './CaveatPanel'
import type { AiDetection, AiSignal, AiSignalTier } from '../types'

/**
 * The AI/ML evidence record, shown rather than trusted.
 *
 * Nineteen of the forty-five checks only mean anything if the design has an AI or ML
 * component in it, and whether they apply is decided inside the evaluate stage's
 * `not_applicable` verdict. Before this panel existed, the only trace of that
 * decision on screen was a pillar reading "Not applicable to this design" — a
 * conclusion with no argument attached, which a judge could neither check nor
 * contest.
 *
 * So this shows the argument: what was searched for, how many patterns ran, which
 * component labels they ran against, and every match with its source and the text
 * around it. A reviewer who reads "no AI/ML detected" next to a component list
 * containing "Personalization Service" can overrule it on sight.
 *
 * It reuses `CaveatPanel`, the same component `IngestWarnings` and `DataFidelity`
 * use. No new panel style.
 *
 * Tone follows what the reader has to DO, not how confident the record is:
 * `caution` when the record and the review disagree, or when the design contradicts
 * itself — both need a person. `neutral` otherwise, because a correctly-detected AI
 * component and a correctly-empty design are both just facts being reported.
 */

const TIER_LABELS: Record<AiSignalTier, string> = {
  classified_kind: 'classified as an AI model',
  named_service: 'named AI service',
  explicit_term: 'explicit AI/ML term',
  implicit_function: 'implied capability',
  denial: 'design states no AI/ML',
}

/**
 * The verdict, in one or two words, for the tag on the title line.
 *
 * Six verdicts used to reach the screen through two panel tones, so `present`,
 * `likely`, `absent`, `denied` and `not_run` all rendered identically and could only
 * be told apart by reading a full sentence. This is a new state space — the rest of
 * the app had nothing like it — and in an audit tool the state a review is in should
 * be scannable before it is read.
 *
 * The WORD carries the meaning and the colour only reinforces it. Three visual classes,
 * matched to the panel tone rather than inventing a per-verdict palette:
 *
 * - `navy` — a fact about the design, reported. AI is there.
 * - `amber` — a person should look. The design contradicts itself, or the signal is
 *   suggestive rather than conclusive. Same `sev-medium` the caution panel already uses.
 * - `grey` — nothing was found, or nothing ran. Quiet on purpose; a non-AI design is
 *   the ordinary case and should not read as a problem.
 */
const VERDICT_TAGS: Record<string, { label: string; tone: 'navy' | 'amber' | 'grey' }> = {
  present: { label: 'AI present', tone: 'navy' },
  likely: { label: 'AI likely', tone: 'amber' },
  contradicted: { label: 'Contradicted', tone: 'amber' },
  absent: { label: 'No AI found', tone: 'grey' },
  denied: { label: 'None declared', tone: 'grey' },
  not_run: { label: 'Not checked', tone: 'grey' },
}

/* Fill and text pass AA on the sunken panel (navy 12.9:1, sev-medium 4.7:1, ink-faint
   5.4:1 — see scripts/contrast_audit.py). The hairline border is decorative: the tint
   and the label carry the state, so WCAG 1.4.11 does not apply to it. */
const TAG_CLASS: Record<'navy' | 'amber' | 'grey', string> = {
  navy: 'border-minfy-navy/40 bg-minfy-navy/5 text-minfy-navy',
  amber: 'border-sev-medium/40 bg-sev-medium/8 text-sev-medium',
  grey: 'border-ink-faint/40 bg-ink-faint/5 text-ink-faint',
}

/**
 * What "96 patterns checked · 2 matches · 3 components searched" actually means.
 *
 * The count line was shown with no explanation at all, and each of its three numbers
 * invites a wrong reading. "96 patterns checked" looks like a property of the design
 * — a reviewer could reasonably take it for 96 things found, or 96 checks run against
 * their architecture — when it is the fixed size of the pattern set and is the same
 * on every review ever produced. Only the middle number says anything about the design
 * in front of you.
 *
 * Inline and always visible, matching how `DataFidelity` explains its OCR proxy in
 * prose beside the figure rather than behind a disclosure. A number a reader has to
 * guess the meaning of is the same problem as a verdict with no argument — this panel
 * exists because of the second one, so it should not ship the first.
 *
 * Nothing to explain when the counts are absent: `not_run` renders no detail line.
 */
function CountLegend({ detection }: { detection: AiDetection }) {
  if (detection.verdict === 'not_run') return null

  return (
    <p
      data-testid="ai-detection-count-legend"
      className="t-caption mt-1.5 max-w-prose text-ink-muted"
    >
      <span className="font-medium text-ink">Patterns checked</span> is the size of the
      fixed pattern set — the same number on every review, so it describes how hard the
      detector looked, not anything about this design.{' '}
      <span className="font-medium text-ink">Matches</span> is how many of them fired
      here, and <span className="font-medium text-ink">components searched</span> is how
      many extracted elements had their text read.
    </p>
  )
}

function VerdictTag({ detection, disagrees }: { detection: AiDetection; disagrees: boolean }) {
  // A disagreement outranks the verdict itself: the reader's job is no longer "what
  // did detection find" but "which of these two is right".
  const tag = disagrees
    ? { label: 'Disagrees', tone: 'amber' as const }
    : (VERDICT_TAGS[detection.verdict] ?? VERDICT_TAGS.absent!)

  return (
    <span
      data-testid="ai-detection-verdict-tag"
      className={`t-eyebrow shrink-0 border px-2 py-0.5 ${TAG_CLASS[tag.tone]}`}
    >
      {tag.label}
    </span>
  )
}

function titleFor(detection: AiDetection, disagrees: boolean): string {
  if (disagrees) return 'AI/ML evidence found, but AI checks were marked not applicable'
  switch (detection.verdict) {
    case 'present':
      return 'AI/ML component detected'
    case 'likely':
      return 'AI/ML component likely — not explicitly labelled'
    case 'contradicted':
      return 'The design denies AI/ML but shows evidence of it'
    case 'denied':
      return 'No AI/ML component detected — the design states it has none'
    case 'not_run':
      return 'AI/ML detection did not run for this review'
    default:
      return 'No AI/ML component detected'
  }
}

export function AiDetectionPanel({
  detection,
  /** True when a pillar was wholly skipped while this record says AI is present. */
  disagrees = false,
  className = '',
}: {
  detection?: AiDetection
  disagrees?: boolean
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const evidenceId = useId()
  if (!detection) return null

  const caution = disagrees || detection.verdict === 'contradicted'
  const signals = detection.signals ?? []

  return (
    <section
      aria-label="AI/ML detection"
      data-testid="ai-detection"
      data-verdict={detection.verdict}
      data-disagrees={disagrees ? 'true' : 'false'}
      className={`animate-enter ${className}`}
    >
      <CaveatPanel
        tone={caution ? 'caution' : 'neutral'}
        testId="ai-detection-panel"
        title={titleFor(detection, disagrees)}
        body={
          <>
            {/*
              The backend's own sentence, rendered verbatim. It is computed from the
              evidence rather than stored beside it, so the panel, the API response
              and the PDF cannot end up describing the same record differently.
            */}
            {detection.rationale}
            {disagrees && (
              <>
                {' '}
                <strong className="font-medium text-ink">
                  A pillar was scored as not applicable anyway.
                </strong>{' '}
                Nothing has been changed automatically — the evidence below and the
                verdicts are both shown so you can decide which is right.
              </>
            )}
          </>
        }
        detail={
          detection.verdict === 'not_run'
            ? undefined
            : `${detection.patterns_checked} patterns checked · ${signals.length} ${
                signals.length === 1 ? 'match' : 'matches'
              } · ${detection.components_seen.length} ${
                detection.components_seen.length === 1 ? 'component' : 'components'
              } searched`
        }
        badge={<VerdictTag detection={detection} disagrees={disagrees} />}
        /*
          Inside the panel, not after it. The disclosure and its list used to render on
          the page background below the block, which put the evidence — the one part a
          reviewer can check against the original document — outside the thing it is
          evidence FOR, reading as unrelated content that happened to follow.

          The footer slot also carries the legend for the count line, which sits
          directly above it. Reusing the slot rather than adding a third one keeps the
          count and its explanation in one block and in reading order.
        */
        footer={
          <>
            <CountLegend detection={detection} />
            {signals.length > 0 && (
              <>
              <button
                type="button"
                onClick={() => setOpen((value) => !value)}
                aria-expanded={open}
                aria-controls={evidenceId}
                className="t-caption mt-2.5 flex items-center gap-1.5 font-medium text-minfy-indigo underline underline-offset-2 transition-colors hover:text-minfy-blue"
              >
                {/* The same chevron affordance the findings accordion uses, so a
                    disclosure looks like a disclosure everywhere in the app. */}
                <svg
                  viewBox="0 0 16 16"
                  aria-hidden="true"
                  className={`size-3 shrink-0 fill-none stroke-current stroke-2 transition-transform duration-150 ${
                    open ? 'rotate-90' : ''
                  }`}
                >
                  <path d="M6 3.5 L11 8 L6 12.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                {open ? 'Hide the evidence' : `Show the evidence (${signals.length})`}
              </button>

              {open && (
                <ul
                  id={evidenceId}
                  data-testid="ai-detection-evidence"
                  className="animate-enter mt-2.5 divide-y divide-ink/10 border-t border-ink/10"
                >
                  {signals.map((signal: AiSignal, index: number) => (
                    <li
                      key={`${signal.tier}-${signal.signal}-${signal.source}-${index}`}
                      className="py-2.5"
                    >
                      <p className="t-caption">
                        <span className="font-semibold text-ink">{signal.signal}</span>
                        <span className="text-ink-faint"> · {TIER_LABELS[signal.tier]}</span>
                      </p>
                      {/*
                        Monospace and quoted: this is the submitted material, not our
                        prose about it, and the difference should be visible. It is the
                        only part of the panel a reviewer can check against the original
                        — which is why it now carries MORE ink than the sentence
                        describing it, rather than less. It was the faintest text in the
                        panel and it is the most important.
                      */}
                      <p className="t-caption mt-1 break-words font-mono text-ink-muted">
                        “{signal.excerpt}”
                      </p>
                      <p className="t-caption mt-0.5 text-ink-faint">in {signal.source}</p>
                    </li>
                  ))}
                </ul>
              )}
              </>
            )}
          </>
        }
      />
    </section>
  )
}

/**
 * The one-line version, for a pillar card that was skipped entirely.
 *
 * This replaces a bare "Not applicable to this design", which stated a conclusion
 * and gave no way to check it. Returns the record's own sentence, so the short form
 * and the panel cannot disagree.
 */
export function pillarNotApplicableReason(detection?: AiDetection): string {
  if (!detection || detection.verdict === 'not_run') {
    return 'Not applicable to this design — no detection record was stored for this review.'
  }
  return `Not applicable to this design. ${detection.rationale}`
}
