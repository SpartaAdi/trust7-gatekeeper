import { useEffect, useState } from 'react'

import { ApiError, getReview } from '../api'
import {
  SEVERITY_DOT_CLASS,
  SEVERITY_TEXT_CLASS,
  maturityFor,
  scoreToneClass,
} from '../maturity'
import type {
  Finding,
  FrameworkScore,
  PillarScore,
  ReviewResult,
  ScoreDelta,
} from '../types'

interface Props {
  reviewId: string
  onReReview: () => void
  onStartOver: () => void
}

export function ResultsView({ reviewId, onReReview, onStartOver }: Props) {
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
      <div className="mx-auto max-w-2xl px-6 py-12">
        <div
          role="alert"
          className="border-l-2 border-sev-high bg-surface-sunken px-4 py-3 text-sm"
        >
          <p className="font-medium text-sev-high">Could not load results</p>
          <p className="mt-1 text-ink-muted">{error}</p>
        </div>
        <button
          type="button"
          onClick={onStartOver}
          className="mt-6 text-sm text-ink-muted underline underline-offset-2 hover:text-ink"
        >
          Start over
        </button>
      </div>
    )
  }

  if (result === null) {
    return (
      <p className="mx-auto max-w-2xl px-6 py-12 text-sm text-ink-muted">
        Loading results…
      </p>
    )
  }

  const open = result.findings.filter(
    (finding) => finding.status === 'fail' || finding.status === 'partial',
  )

  return (
    <div className="mx-auto max-w-5xl px-6 py-12">
      <header className="flex flex-wrap items-end justify-between gap-6 border-b border-hairline pb-8">
        <div className="min-w-0">
          <h2 className="text-2xl font-semibold tracking-tight">
            {result.title || 'Design review'}
          </h2>
          <p className="mt-2 text-sm text-ink-muted">
            <span className="font-mono text-xs">{result.review_id}</span> ·{' '}
            {open.length} open {open.length === 1 ? 'finding' : 'findings'} across{' '}
            <span className="tnum">{result.findings.length}</span> checks
          </p>
        </div>
        <div className="text-right">
          <p className="tnum text-4xl font-semibold leading-none">
            {result.overall_score.toFixed(1)}
          </p>
          <p className="mt-1 text-xs uppercase tracking-wide text-ink-muted">
            Overall · {maturityFor(result.overall_score)}
          </p>
        </div>
      </header>

      {result.delta && <DeltaSummary delta={result.delta} />}

      {result.summary && (
        <section className="mt-10">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
            Assessment
          </h3>
          <p className="mt-3 max-w-3xl text-sm leading-relaxed">{result.summary}</p>
        </section>
      )}

      <section className="mt-12">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Pillar maturity
        </h3>
        {result.frameworks.map((framework) => (
          <FrameworkPillars key={framework.framework} framework={framework} />
        ))}
      </section>

      <FindingsList findings={result.findings} />

      <footer className="mt-14 flex flex-wrap items-center gap-6 border-t border-hairline pt-8">
        <button
          type="button"
          onClick={onReReview}
          className="bg-minfy-orange px-5 py-2.5 text-sm font-semibold text-white hover:bg-minfy-navy"
        >
          Re-review a revised design
        </button>
        <button
          type="button"
          onClick={onStartOver}
          className="text-sm text-ink-muted underline underline-offset-2 hover:text-ink"
        >
          Review a different design
        </button>
        {Object.keys(result.token_usage).length > 0 && (
          <p className="tnum ml-auto text-xs text-ink-muted">
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

function DeltaSummary({ delta }: { delta: ScoreDelta }) {
  const moved = delta.pillars.filter((pillar) => pillar.change !== 0)

  return (
    <section className="mt-10 border-l-2 border-minfy-orange bg-surface-sunken px-5 py-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-sm font-semibold">Change since the previous review</h3>
        <p className="tnum text-sm text-ink-muted">
          {delta.previous_overall_score.toFixed(1)} → {delta.current_overall_score.toFixed(1)}
        </p>
        <ChangeBadge change={delta.change} />
      </div>

      <dl className="mt-3 flex flex-wrap gap-x-8 gap-y-1 text-xs text-ink-muted">
        <div className="flex gap-1.5">
          <dt>Resolved:</dt>
          <dd className="tnum font-medium text-verdict-pass">
            {delta.resolved_checks.length}
          </dd>
        </div>
        <div className="flex gap-1.5">
          <dt>New:</dt>
          <dd className="tnum font-medium text-sev-high">{delta.new_checks.length}</dd>
        </div>
        <div className="flex gap-1.5">
          <dt>Still open:</dt>
          <dd className="tnum font-medium text-ink">
            {delta.unchanged_failures.length}
          </dd>
        </div>
      </dl>

      {moved.length > 0 && (
        <ul className="mt-4 grid gap-x-8 gap-y-1.5 sm:grid-cols-2">
          {moved.map((pillar) => (
            <li
              key={`${pillar.framework}-${pillar.pillar_id}`}
              className="flex items-baseline justify-between gap-3 text-xs"
            >
              <span className="truncate">{pillar.pillar_name}</span>
              <span className="tnum flex shrink-0 items-baseline gap-2 text-ink-muted">
                {pillar.previous_score.toFixed(0)} → {pillar.current_score.toFixed(0)}
                <ChangeBadge change={pillar.change} compact />
              </span>
            </li>
          ))}
        </ul>
      )}
      {moved.length === 0 && (
        <p className="mt-3 text-xs text-ink-muted">
          No pillar score changed between the two reviews.
        </p>
      )}
    </section>
  )
}

function ChangeBadge({ change, compact }: { change: number; compact?: boolean }) {
  const tone =
    change > 0 ? 'text-verdict-pass' : change < 0 ? 'text-sev-high' : 'text-ink-muted'
  const arrow = change > 0 ? '▲' : change < 0 ? '▼' : '–'
  return (
    <span className={`tnum ${compact ? 'text-xs' : 'text-sm'} font-semibold ${tone}`}>
      {arrow} {change === 0 ? '0.0' : `${Math.abs(change).toFixed(1)}`}
    </span>
  )
}

function FrameworkPillars({ framework }: { framework: FrameworkScore }) {
  return (
    <div className="mt-6">
      <div className="flex items-baseline justify-between gap-4 border-b border-hairline pb-2">
        <h4 className="text-sm font-semibold">{framework.framework_name}</h4>
        <p className="tnum text-sm text-ink-muted">
          {framework.score.toFixed(1)} · {maturityFor(framework.score)}
        </p>
      </div>
      <div className="grid gap-x-10 gap-y-5 pt-5 sm:grid-cols-2 lg:grid-cols-3">
        {framework.pillars.map((pillar) => (
          <PillarCell key={pillar.pillar_id} pillar={pillar} />
        ))}
      </div>
    </div>
  )
}

function PillarCell({ pillar }: { pillar: PillarScore }) {
  const unevaluated = pillar.checks_evaluated === 0
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <p className="truncate text-sm" title={pillar.pillar_name}>
          {pillar.pillar_name}
        </p>
        <p className="tnum shrink-0 text-sm font-semibold">
          {unevaluated ? '—' : pillar.score.toFixed(0)}
        </p>
      </div>
      <div
        className="mt-2 h-1 w-full bg-hairline"
        role="img"
        aria-label={`${pillar.pillar_name}: ${unevaluated ? 'not evaluated' : `${pillar.score} of 100`}`}
      >
        {!unevaluated && (
          <div
            className={`h-1 ${scoreToneClass(pillar.score)}`}
            style={{ width: `${Math.min(100, Math.max(0, pillar.score))}%` }}
          />
        )}
      </div>
      <p className="mt-1.5 text-xs text-ink-muted">
        {unevaluated ? (
          'Not applicable to this design'
        ) : (
          <>
            {maturityFor(pillar.score)} ·{' '}
            <span className="tnum">
              {pillar.checks_passed}/{pillar.checks_evaluated} passed
            </span>
            {pillar.checks_evaluated < pillar.checks_total && (
              <span className="tnum">
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

function FindingsList({ findings }: { findings: Finding[] }) {
  const [showPassing, setShowPassing] = useState(false)

  const open = findings.filter(
    (finding) => finding.status === 'fail' || finding.status === 'partial',
  )
  const rest = findings.filter(
    (finding) => finding.status === 'pass' || finding.status === 'not_applicable',
  )
  const shown = showPassing ? [...open, ...rest] : open

  return (
    <section className="mt-14">
      <div className="flex flex-wrap items-baseline justify-between gap-4">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Findings, in remediation order
        </h3>
        <button
          type="button"
          onClick={() => setShowPassing((value) => !value)}
          className="text-xs text-ink-muted underline underline-offset-2 hover:text-ink"
        >
          {showPassing
            ? 'Hide passing checks'
            : `Show ${rest.length} passing / not-applicable checks`}
        </button>
      </div>

      {open.length === 0 && (
        <p className="mt-6 text-sm text-verdict-pass">
          No gaps found — every applicable check passed.
        </p>
      )}

      <ol className="mt-6 divide-y divide-hairline border-y border-hairline">
        {shown.map((finding) => (
          <FindingRow key={`${finding.framework}-${finding.check_id}`} finding={finding} />
        ))}
      </ol>
    </section>
  )
}

function FindingRow({ finding }: { finding: Finding }) {
  const passing = finding.status === 'pass'
  const notApplicable = finding.status === 'not_applicable'

  return (
    <li className="py-6">
      <div className="flex items-start gap-4">
        <span className="tnum mt-0.5 w-6 shrink-0 text-right text-xs text-ink-muted">
          {finding.priority > 0 ? finding.priority : '·'}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <p
              className={`text-sm font-semibold ${passing || notApplicable ? 'text-ink-muted' : 'text-ink'}`}
            >
              {finding.title}
            </p>
            <StatusTag status={finding.status} />
          </div>

          <p className="mt-1.5 flex flex-wrap items-center gap-x-2 text-xs text-ink-muted">
            <span
              aria-hidden="true"
              className={`inline-block size-1.5 rounded-full ${SEVERITY_DOT_CLASS[finding.severity] ?? 'bg-sev-low'}`}
            />
            <span
              className={`font-medium ${SEVERITY_TEXT_CLASS[finding.severity] ?? 'text-sev-low'}`}
            >
              {finding.severity} severity
            </span>
            <span aria-hidden="true">·</span>
            <span>{finding.pillar_id.replace(/_/g, ' ')}</span>
            <span aria-hidden="true">·</span>
            <span className="font-mono">{finding.check_id}</span>
          </p>

          {finding.evidence && (
            <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-muted">
              {finding.evidence}
            </p>
          )}

          {finding.remediation && (
            <div className="mt-3 max-w-3xl border-l-2 border-hairline pl-4">
              <p className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
                Remediation
                {finding.remediation_effort && (
                  <span className="ml-2 font-normal normal-case">
                    · {finding.remediation_effort} effort
                  </span>
                )}
              </p>
              <p className="mt-1.5 text-sm leading-relaxed">{finding.remediation}</p>
            </div>
          )}

          {finding.affected_components.length > 0 && (
            <p className="mt-3 text-xs text-ink-muted">
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
    fail: 'border-sev-high text-sev-high',
    partial: 'border-sev-medium text-sev-medium',
    pass: 'border-verdict-pass text-verdict-pass',
    not_applicable: 'border-hairline text-ink-muted',
  }
  return (
    <span
      className={`border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${tone[status]}`}
    >
      {label[status]}
    </span>
  )
}
