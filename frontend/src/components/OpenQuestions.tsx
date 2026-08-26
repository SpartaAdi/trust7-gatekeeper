import { useState } from 'react'

import { ApiError, reReview } from '../api'
import type { Finding } from '../types'
import { useDictation } from '../useDictation'
import { MAX_FEEDBACK_CHARS } from './FeedbackBox'

/**
 * The questions this review could not answer from the documents, asked directly.
 *
 * A design-time document review cannot see operational practice: whether an incident
 * runbook is rehearsed, whether cost anomalies are actually chased, whether a human
 * signs off a model change. Those checks fail or come back partial not because the
 * design is wrong but because a document cannot evidence them. This turns each of
 * those open findings back into the question it started as, and lets the person who
 * knows the answer type it.
 *
 * Both frameworks, deliberately. The WAF and TRUST-7 halves fail for the same
 * reason and a reviewer answering "how do you handle incidents" should not have to
 * answer it twice in two places because the rubric files it under two pillars.
 *
 * A PANEL over the results page, rather than a tab or a route. `App.tsx` drives a
 * four-phase state machine — Reviews, Submit, Analysis, Findings — that also feeds
 * the step tracker, and this is not a fifth step in that pipeline: it is a task
 * performed ON a finished review. A route would have to be special-cased out of the
 * tracker, and a tab would put it in competition with the findings it is derived
 * from. A dismissible panel also satisfies the requirement literally: the flow
 * starts, collates, edits and submits without leaving the view it opened from.
 */

const GENERIC_PROMPT =
  "Anything else about your team's operational practices, incident history, cost " +
  'governance, or AI oversight this document review could not see?'

export function OpenQuestions({
  reviewId,
  findings,
  onClose,
  onStarted,
}: {
  reviewId: string
  findings: Finding[]
  onClose: () => void
  onStarted: (newReviewId: string, startedAt: number) => void
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [general, setGeneral] = useState('')
  const [draft, setDraft] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // Open only. A finding that a previous round resolved is no longer `fail` or
  // `partial`, so it simply stops appearing here — there is no "answered" flag and
  // nothing to keep in sync. The next visit re-derives this list from the review's
  // own statuses, which is the only state that could be authoritative anyway.
  const open = findings.filter(
    (finding) => finding.status === 'fail' || finding.status === 'partial',
  )

  // Keyed on framework AND pillar. `pillar_id` is shared across the two frameworks
  // — `sustainability` exists in both — so grouping on it alone silently merges two
  // different pillars into one heading.
  const groups = new Map<string, { framework: string; pillar: string; items: Finding[] }>()
  for (const finding of open) {
    const key = `${finding.framework}::${finding.pillar_id}`
    const group = groups.get(key) ?? {
      framework: finding.framework,
      pillar: finding.pillar_id,
      items: [],
    }
    group.items.push(finding)
    groups.set(key, group)
  }

  const answered = open.filter((finding) => (answers[finding.check_id] ?? '').trim())
  const collated = buildSummary(open, answers, general)
  const over = collated.length - MAX_FEEDBACK_CHARS

  const body = draft ?? collated
  const bodyOver = body.length - MAX_FEEDBACK_CHARS
  const canSubmit = body.trim().length > 0 && bodyOver <= 0 && !busy

  async function submit() {
    setBusy(true)
    setError('')
    const startedAt = Date.now()
    try {
      const accepted = await reReview(reviewId, { feedback: body.trim() })
      onStarted(accepted.review_id, startedAt)
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : 'Could not start the re-review.',
      )
      setBusy(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Open questions"
      data-testid="open-questions"
      className="fixed inset-0 z-40 flex justify-end bg-minfy-navy/30"
    >
      <div className="flex h-full w-full max-w-2xl flex-col overflow-y-auto bg-surface shadow-xl">
        <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-hairline bg-surface px-6 py-5">
          <div className="min-w-0">
            <h2 className="t-title">Open questions</h2>
            <p className="t-caption mt-1 max-w-prose text-ink-muted">
              {open.length} open {open.length === 1 ? 'finding' : 'findings'} across
              both frameworks. A document review cannot see how your team actually
              operates — answer what you can, skip what you cannot.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="t-caption shrink-0 text-ink-muted underline underline-offset-2 hover:text-ink"
          >
            Close
          </button>
        </header>

        <div className="flex-1 px-6 py-6">
          {open.length === 0 ? (
            <p className="t-body text-ink-muted">
              Nothing is open on this review — there is nothing to ask about.
            </p>
          ) : (
            [...groups.values()].map((group) => (
              <section key={`${group.framework}::${group.pillar}`} className="mb-8">
                <h3 className="t-eyebrow text-ink-muted">
                  {group.pillar.replace(/_/g, ' ')}
                  <span className="font-normal normal-case tracking-normal text-ink-faint">
                    {' '}
                    · {FRAMEWORK_LABEL[group.framework] ?? group.framework}
                  </span>
                </h3>
                {group.items.map((finding) => (
                  <AnswerField
                    key={`${finding.framework}-${finding.check_id}`}
                    finding={finding}
                    value={answers[finding.check_id] ?? ''}
                    onChange={(next) =>
                      setAnswers((prev) => ({ ...prev, [finding.check_id]: next }))
                    }
                  />
                ))}
              </section>
            ))
          )}

          {/*
            The catch-all. Every question above is tied to a check the rubric
            happens to hold; this is where the thing the rubric never asked about
            goes, and it is collated last so it reads as context rather than as an
            answer to whichever finding came before it.
          */}
          <section className="mb-8">
            <h3 className="t-eyebrow text-ink-muted">Anything else</h3>
            <label htmlFor="oq-general" className="t-body mt-2 block max-w-prose">
              {GENERIC_PROMPT}
            </label>
            <DictatedTextarea
              id="oq-general"
              value={general}
              onChange={setGeneral}
              label="general context"
            />
          </section>
        </div>

        <footer className="sticky bottom-0 border-t border-hairline bg-surface px-6 py-5">
          {draft === null ? (
            <>
              <button
                type="button"
                onClick={() => setDraft(collated)}
                disabled={collated.trim().length === 0}
                data-testid="generate-summary"
                className="t-body bg-minfy-indigo px-4 py-2.5 text-white transition-colors hover:bg-minfy-blue disabled:opacity-50"
              >
                Generate re-review summary
              </button>
              <p className="t-caption mt-2 text-ink-faint">
                {answered.length} of {open.length} answered
                {general.trim() ? ', plus general context' : ''}
                {collated.trim().length === 0
                  ? ' — answer at least one to continue.'
                  : '.'}
              </p>
            </>
          ) : (
            <>
              {/*
                Editable before it goes anywhere. What gets submitted is what is in
                this box, not what was generated into it — the collation is a
                starting point, and a reviewer rewriting it is the expected use.
              */}
              <label htmlFor="oq-draft" className="t-eyebrow text-ink-muted">
                Re-review summary — edit before submitting
              </label>
              <textarea
                id="oq-draft"
                value={body}
                onChange={(event) => setDraft(event.target.value)}
                rows={10}
                data-testid="summary-draft"
                className="t-body mt-2 w-full resize-y border border-hairline bg-surface px-3 py-2 font-mono text-[0.8125rem] focus:border-minfy-indigo"
              />
              <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                <p
                  className={`t-caption tnum ${bodyOver > 0 ? 'text-sev-high' : 'text-ink-faint'}`}
                  data-testid="budget"
                >
                  {bodyOver > 0
                    ? `${bodyOver} characters over the ${MAX_FEEDBACK_CHARS} limit — shorten it, or answer fewer questions.`
                    : `${MAX_FEEDBACK_CHARS - body.length} of ${MAX_FEEDBACK_CHARS} characters left.`}
                </p>
                <div className="flex items-center gap-4">
                  <button
                    type="button"
                    onClick={() => setDraft(null)}
                    className="t-caption text-ink-muted underline underline-offset-2 hover:text-ink"
                  >
                    Back to answers
                  </button>
                  <button
                    type="button"
                    onClick={submit}
                    disabled={!canSubmit}
                    data-testid="submit-re-review"
                    className="t-body bg-minfy-indigo px-4 py-2.5 text-white transition-colors hover:bg-minfy-blue disabled:opacity-50"
                  >
                    {busy ? 'Starting…' : 'Submit re-review'}
                  </button>
                </div>
              </div>
              {error && (
                <p role="alert" className="t-caption mt-2 text-sev-high">
                  {error}
                </p>
              )}
            </>
          )}
          {/*
            Shown before generating too, so a reviewer who has already typed past
            the limit learns it here rather than at the submit button. The cap is
            the server's, mirrored — see MAX_FEEDBACK_CHARS.
          */}
          {draft === null && over > 0 && (
            <p className="t-caption mt-2 text-sev-high" data-testid="pre-budget">
              These answers come to {collated.length} characters, {over} over the{' '}
              {MAX_FEEDBACK_CHARS} the re-review endpoint accepts. Shorten them, or
              answer fewer questions.
            </p>
          )}
        </footer>
      </div>
    </div>
  )
}

const FRAMEWORK_LABEL: Record<string, string> = {
  aws_waf: 'AWS Well-Architected',
  trust7: 'TRUST-7',
}

/**
 * The collated block.
 *
 * One entry per ANSWERED finding, in the order the findings are listed, with the
 * general note last. An unanswered finding contributes nothing at all — not a
 * heading, not an empty line — because a block full of "Regarding X: " with nothing
 * after it would read to the model as a reviewer who had nothing to say about X,
 * which is a different claim from not having been asked.
 *
 * Exported for its tests: the format is the contract between this view and the
 * re-review endpoint, and it is the one part worth pinning independently of the UI.
 */
export function buildSummary(
  findings: Finding[],
  answers: Record<string, string>,
  general: string,
): string {
  const parts = findings
    .map((finding) => ({ finding, answer: (answers[finding.check_id] ?? '').trim() }))
    .filter((entry) => entry.answer)
    .map(
      (entry) =>
        `Regarding ${entry.finding.title} (${entry.finding.check_id}): ${entry.answer}`,
    )

  if (general.trim()) parts.push(general.trim())
  return parts.join('\n\n')
}

function AnswerField({
  finding,
  value,
  onChange,
}: {
  finding: Finding
  value: string
  onChange: (next: string) => void
}) {
  const id = `oq-${finding.framework}-${finding.check_id}`

  return (
    <div className="mt-4 border-l-2 border-hairline pl-4">
      <label htmlFor={id} className="t-body block max-w-prose">
        {finding.title}
      </label>
      {/*
        Traceability, and the reason it is not decorative: an answer collated into
        the summary names this check_id, so a reader of the submitted block can find
        the finding it belongs to without guessing from the prose.
      */}
      <p className="t-caption mt-0.5 text-ink-faint">
        {FRAMEWORK_LABEL[finding.framework] ?? finding.framework}
        <span aria-hidden="true"> · </span>
        <span className="font-mono">{finding.check_id}</span>
      </p>
      <DictatedTextarea id={id} value={value} onChange={onChange} label={finding.title} />
    </div>
  )
}

/**
 * A textarea with dictation, one per question.
 *
 * The hook is per-field on purpose: `useDictation` owns one recogniser, and a single
 * shared instance would append whatever was said into whichever box happened to be
 * focused last. Same visible-state treatment the upload page's mic carries — a stop
 * square, a ring, and a live region — so listening is never signalled by colour
 * alone.
 */
function DictatedTextarea({
  id,
  value,
  onChange,
  label,
}: {
  id: string
  value: string
  onChange: (next: string) => void
  label: string
}) {
  const { supported, listening, toggle } = useDictation((spoken) => {
    onChange(value ? `${value} ${spoken}` : spoken)
  })

  return (
    <>
      <div className="mt-1.5 flex items-start gap-2">
        <textarea
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          rows={2}
          className="t-body min-w-0 flex-1 resize-y border border-hairline bg-surface px-3 py-2 transition-colors placeholder:text-ink-faint hover:border-ink-faint focus:border-minfy-indigo"
          placeholder="Optional"
        />
        {supported && (
          <button
            type="button"
            onClick={toggle}
            aria-pressed={listening}
            aria-label={listening ? 'Stop dictating' : `Dictate an answer about ${label}`}
            title={listening ? 'Stop dictating' : 'Dictate an answer'}
            className={`relative flex size-10 shrink-0 items-center justify-center transition-colors duration-150 ${
              listening
                ? 'bg-minfy-navy text-white ring-2 ring-minfy-navy ring-offset-2 ring-offset-surface'
                : 'bg-minfy-indigo text-white hover:bg-minfy-blue'
            }`}
          >
            {listening ? (
              <svg viewBox="0 0 16 16" aria-hidden="true" className="size-3.5 fill-current">
                <rect x="3" y="3" width="10" height="10" rx="1" />
              </svg>
            ) : (
              <svg viewBox="0 0 16 16" aria-hidden="true" className="size-4 fill-current">
                <path d="M8 1.5a2 2 0 0 1 2 2v4a2 2 0 0 1-4 0v-4a2 2 0 0 1 2-2Z" />
                <path d="M4 7a.75.75 0 0 1 1.5 0 2.5 2.5 0 0 0 5 0A.75.75 0 0 1 12 7a4 4 0 0 1-3.25 3.93v1.32h1.75a.75.75 0 0 1 0 1.5h-5a.75.75 0 0 1 0-1.5h1.75v-1.32A4 4 0 0 1 4 7Z" />
              </svg>
            )}
          </button>
        )}
      </div>
      <p className="t-caption font-medium text-ink" aria-live="polite">
        {listening ? 'Please speak now.' : ''}
      </p>
    </>
  )
}
