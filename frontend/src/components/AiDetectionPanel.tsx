import { useState } from 'react'
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
      />

      {signals.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            className="t-caption mt-2 flex items-center gap-1 text-minfy-indigo underline underline-offset-2 transition-colors hover:text-minfy-blue"
          >
            {open ? 'Hide the evidence' : `Show the evidence (${signals.length})`}
          </button>

          {open && (
            <ul
              data-testid="ai-detection-evidence"
              className="animate-enter mt-2 space-y-2.5 border-l-2 border-ink/15 pl-4"
            >
              {signals.map((signal: AiSignal, index: number) => (
                <li key={`${signal.tier}-${signal.signal}-${signal.source}-${index}`}>
                  <p className="t-caption">
                    <span className="font-medium text-ink">{signal.signal}</span>
                    <span className="text-ink-faint"> · {TIER_LABELS[signal.tier]}</span>
                  </p>
                  <p className="t-caption text-ink-muted">in {signal.source}</p>
                  {/*
                    Monospace and quoted: this is the submitted material, not our
                    prose about it, and the difference should be visible. It is the
                    only part of the panel a reviewer can check against the original.
                  */}
                  <p className="t-caption mt-0.5 break-words font-mono text-ink-faint">
                    “{signal.excerpt}”
                  </p>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
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
