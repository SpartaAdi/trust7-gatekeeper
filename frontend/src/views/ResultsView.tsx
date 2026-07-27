import { useEffect, useState } from 'react'

import { ApiError, downloadReport, getReview } from '../api'
import { SeverityMark } from '../components/SeverityMark'
import { maturityFor, scoreToneClass } from '../maturity'
import type {
  Finding,
  FrameworkScore,
  PillarScore,
  ReviewResult,
  ScoreDelta,
  Severity,
} from '../types'

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
          </p>
          <p className="t-eyebrow mt-2 text-ink-muted">
            Overall · {maturityFor(result.overall_score)}
          </p>
        </div>
      </header>

      {result.delta && <DeltaSummary delta={result.delta} />}

      {result.executive_summary && (
        <section className="mt-12 border-l-2 border-minfy-navy bg-surface-sunken px-5 py-5">
          <h3 className="t-eyebrow text-ink-muted">Executive summary</h3>
          <p className="t-body mt-2.5 max-w-prose text-pretty text-[0.9375rem] leading-relaxed">
            {result.executive_summary}
          </p>
        </section>
      )}

      {result.summary && (
        <section className="mt-10">
          <h3 className="t-eyebrow text-ink-muted">Assessment</h3>
          <p className="t-body mt-3 max-w-prose text-pretty">{result.summary}</p>
        </section>
      )}

      <section className="mt-12">
        <h3 className="t-eyebrow text-ink-muted">Pillar maturity</h3>
        <div className="mt-4 space-y-10">
          {result.frameworks.map((framework) => (
            <FrameworkSection key={framework.framework} framework={framework} />
          ))}
        </div>
      </section>

      <FindingsList findings={result.findings} />

      <footer className="mt-16 flex flex-wrap items-center gap-x-6 gap-y-4 border-t border-hairline pt-8">
        <button
          type="button"
          onClick={onReReview}
          className="t-body bg-minfy-orange px-5 py-2.5 font-semibold text-white transition-colors duration-150 hover:bg-minfy-navy"
        >
          Re-review a revised design
        </button>
        <DownloadReportButton reviewId={result.review_id} />
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

  return (
    <section className="mt-10 border-l-2 border-minfy-orange bg-surface-sunken px-5 py-4.5">
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

function ChangeBadge({ change, compact }: { change: number; compact?: boolean }) {
  const improved = change > 0
  const worsened = change < 0
  const tone = improved ? 'text-verdict-pass' : worsened ? 'text-sev-high' : 'text-ink-muted'
  // Arrow direction carries the meaning as well as the colour.
  const arrow = improved ? '▲' : worsened ? '▼' : '–'
  const wording = improved ? 'up' : worsened ? 'down' : 'unchanged'

  return (
    <span className={`tnum ${compact ? 't-caption' : 't-body'} font-semibold ${tone}`}>
      <span aria-hidden="true">{arrow} </span>
      <span className="sr-only">{wording} </span>
      {change === 0 ? '0.0' : Math.abs(change).toFixed(1)}
    </span>
  )
}

function FrameworkSection({ framework }: { framework: FrameworkScore }) {
  return (
    <section>
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
      <div
        className="mt-2 h-1.5 w-full bg-hairline"
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
    <section className="mt-16">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
        <h3 className="t-eyebrow text-ink-muted">Findings, in remediation order</h3>
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
            <div key={severity} className="mt-8">
              <div className="flex items-center gap-2.5">
                <SeverityMark severity={severity} decorative />
                <h4 className="t-heading">{SEVERITY_HEADING[severity]}</h4>
                <span className="tnum t-caption text-ink-muted">({group.length})</span>
                <span aria-hidden="true" className="h-px flex-1 bg-hairline" />
              </div>
              <ol className="divide-y divide-hairline">
                {group.map((finding) => (
                  <FindingRow key={`${finding.framework}-${finding.check_id}`} finding={finding} />
                ))}
              </ol>
            </div>
          )
        })
      )}

      {showPassing && rest.length > 0 && (
        <div className="animate-enter mt-10">
          <div className="flex items-center gap-2.5">
            <h4 className="t-heading text-ink-muted">Passing and not applicable</h4>
            <span className="tnum t-caption text-ink-muted">({rest.length})</span>
            <span aria-hidden="true" className="h-px flex-1 bg-hairline" />
          </div>
          <ol className="divide-y divide-hairline">
            {rest.map((finding) => (
              <FindingRow key={`${finding.framework}-${finding.check_id}`} finding={finding} />
            ))}
          </ol>
        </div>
      )}
    </section>
  )
}

function FindingRow({ finding }: { finding: Finding }) {
  const muted = finding.status === 'pass' || finding.status === 'not_applicable'

  return (
    <li className="py-6">
      <div className="flex items-start gap-4">
        <span
          className="tnum t-caption mt-0.5 w-6 shrink-0 text-right text-ink-faint"
          aria-hidden={finding.priority === 0}
        >
          {finding.priority > 0 ? finding.priority : '·'}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
            <h5 className={`t-heading ${muted ? 'font-medium text-ink-muted' : ''}`}>
              {finding.title}
            </h5>
            <StatusTag status={finding.status} />
          </div>

          <p className="t-caption mt-1.5 flex flex-wrap items-center gap-x-2 text-ink-muted">
            <SeverityMark severity={finding.severity} />
            <span>{finding.pillar_id.replace(/_/g, ' ')}</span>
            <span aria-hidden="true">·</span>
            <span className="font-mono text-ink-faint">{finding.check_id}</span>
          </p>

          {finding.evidence && (
            <p className="t-body mt-3 max-w-prose text-ink-muted">{finding.evidence}</p>
          )}

          {finding.remediation && (
            <div className="mt-4 max-w-prose border-l-2 border-minfy-orange/40 bg-surface-sunken px-4 py-3">
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

          {finding.affected_components.length > 0 && (
            <p className="t-caption mt-3 text-ink-faint">
              Affects: {finding.affected_components.join(', ')}
            </p>
          )}
        </div>
      </div>
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
