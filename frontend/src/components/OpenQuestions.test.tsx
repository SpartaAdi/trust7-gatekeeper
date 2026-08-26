import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Finding } from '../types'
import { MAX_FEEDBACK_CHARS } from './FeedbackBox'
import { OpenQuestions, buildSummary } from './OpenQuestions'

const { reReview } = vi.hoisted(() => ({ reReview: vi.fn() }))

vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {},
  reReview,
}))

/**
 * Open questions — the findings a document review could not answer, asked back.
 *
 * Most of what stays open on a real review is open for want of operational
 * evidence rather than because the design is wrong: whether an incident runbook is
 * rehearsed, whether cost anomalies get chased, whether a human signs off a model
 * change. A document cannot carry that and a diagram certainly cannot. So the
 * properties worth pinning are about what reaches the endpoint and what does not:
 *
 * * the collated format, which is the contract with the re-review endpoint;
 * * that an unanswered question contributes NOTHING — not a heading, not an empty
 *   line — because "Regarding X: " with nothing after it reads as a reviewer who
 *   had nothing to say about X, which is a different claim from not being asked;
 * * that the edited text is what gets submitted, not the generated text;
 * * that both frameworks are present, since the same operational question lands in
 *   two pillars and should not have to be answered twice;
 * * that a resolved finding disappears by falling out of the open filter, with no
 *   separate tracking to drift.
 */

function finding(over: Partial<Finding> = {}): Finding {
  return {
    framework: 'aws_waf',
    pillar_id: 'operational_excellence',
    check_id: 'oe_incident_response',
    status: 'fail',
    severity: 'high',
    title: 'An incident response process is defined for the workload.',
    evidence: 'Not stated in the design.',
    affected_components: [],
    remediation: '',
    remediation_effort: '',
    remediation_grounded_in: '',
    priority: 1,
    confidence: 'high',
    ...over,
  }
}

const TRUST7 = finding({
  framework: 'trust7',
  pillar_id: 'ai_governance',
  check_id: 'gov_model_inventory',
  title: 'A model inventory records every model in production use.',
})

function mount(findings: Finding[], onStarted = vi.fn()) {
  render(
    <OpenQuestions
      reviewId="rev-1"
      findings={findings}
      onClose={vi.fn()}
      onStarted={onStarted}
    />,
  )
  return onStarted
}

beforeEach(() => {
  reReview.mockReset()
  reReview.mockResolvedValue({ review_id: 'rev-2' })
})

// --------------------------------------------------------------------------- #
// The collated format — the contract with the endpoint
// --------------------------------------------------------------------------- #

describe('buildSummary', () => {
  it('names the finding and its check_id before each answer', () => {
    const summary = buildSummary(
      [finding()],
      { oe_incident_response: 'We run a quarterly game day against the runbook.' },
      '',
    )

    expect(summary).toBe(
      'Regarding An incident response process is defined for the workload. ' +
        '(oe_incident_response): We run a quarterly game day against the runbook.',
    )
  })

  it('omits an unanswered finding entirely rather than emitting an empty heading', () => {
    const summary = buildSummary(
      [finding(), TRUST7],
      { gov_model_inventory: 'Tracked in a spreadsheet, reviewed monthly.' },
      '',
    )

    expect(summary).toContain('gov_model_inventory')
    // Not "Regarding <the other one>: " with nothing after it — that would read as
    // a reviewer who had nothing to say, not one who was not asked.
    expect(summary).not.toContain('oe_incident_response')
  })

  it('treats a whitespace-only answer as unanswered', () => {
    expect(buildSummary([finding()], { oe_incident_response: '   \n ' }, '')).toBe('')
  })

  it('puts the general note last, after every per-finding answer', () => {
    const summary = buildSummary(
      [finding()],
      { oe_incident_response: 'Quarterly game days.' },
      'We also run a weekly cost review that this document does not mention.',
    )

    expect(summary.indexOf('Quarterly game days.')).toBeLessThan(
      summary.indexOf('weekly cost review'),
    )
  })

  it('is empty when nothing at all was filled in', () => {
    expect(buildSummary([finding(), TRUST7], {}, '')).toBe('')
    expect(buildSummary([], {}, '')).toBe('')
  })

  it('carries the general note alone when no finding was answered', () => {
    expect(buildSummary([finding()], {}, 'Nothing per-check, but here is context.')).toBe(
      'Nothing per-check, but here is context.',
    )
  })
})

// --------------------------------------------------------------------------- #
// The view
// --------------------------------------------------------------------------- #

describe('OpenQuestions', () => {
  it('asks about open findings from BOTH frameworks', () => {
    mount([finding(), TRUST7])

    const panel = screen.getByTestId('open-questions')
    expect(panel).toHaveTextContent(/AWS Well-Architected/)
    expect(panel).toHaveTextContent(/TRUST-7/)
    expect(panel).toHaveTextContent('oe_incident_response')
    expect(panel).toHaveTextContent('gov_model_inventory')
  })

  it('groups by pillar without merging a pillar id the two frameworks share', () => {
    // `sustainability` exists in both rubrics. Grouping on pillar_id alone would
    // collapse two different pillars under one heading.
    mount([
      finding({ framework: 'aws_waf', pillar_id: 'sustainability', check_id: 'sus_region' }),
      finding({ framework: 'trust7', pillar_id: 'sustainability', check_id: 'sai_inference' }),
    ])

    const headings = screen.getAllByText(/sustainability/i)
    expect(headings.length).toBeGreaterThanOrEqual(2)
    expect(screen.getByTestId('open-questions')).toHaveTextContent('sus_region')
    expect(screen.getByTestId('open-questions')).toHaveTextContent('sai_inference')
  })

  it('shows the real check description as the question', () => {
    mount([finding()])

    expect(
      screen.getByLabelText('An incident response process is defined for the workload.'),
    ).toBeInTheDocument()
  })

  it('never lists a finding that is no longer open', () => {
    // The whole of item 7: resolved findings disappear because they stop matching
    // the open filter. There is no answered-flag, nothing to keep in sync, and
    // nothing that could disagree with the review's own statuses.
    mount([
      finding({ status: 'pass', check_id: 'oe_resolved' }),
      finding({ status: 'not_applicable', check_id: 'oe_na' }),
      finding({ status: 'partial', check_id: 'oe_still_open' }),
    ])

    const panel = screen.getByTestId('open-questions')
    expect(panel).toHaveTextContent('oe_still_open')
    expect(panel).not.toHaveTextContent('oe_resolved')
    expect(panel).not.toHaveTextContent('oe_na')
  })

  it('does not block on partial completion', async () => {
    const user = userEvent.setup()
    mount([finding(), TRUST7])

    await user.type(
      screen.getByLabelText('An incident response process is defined for the workload.'),
      'Quarterly game days.',
    )
    await user.click(screen.getByTestId('generate-summary'))

    const draft = screen.getByTestId('summary-draft') as HTMLTextAreaElement
    expect(draft.value).toContain('Quarterly game days.')
    expect(draft.value).not.toContain('gov_model_inventory')
  })

  it('cannot generate a summary with nothing filled in', () => {
    mount([finding()])

    expect(screen.getByTestId('generate-summary')).toBeDisabled()
  })

  // ------------------------------------------------------------------------- #
  // Edit before submit, and what actually reaches the endpoint
  // ------------------------------------------------------------------------- #

  it('submits the EDITED text, not the text that was generated', async () => {
    const user = userEvent.setup()
    mount([finding()])

    await user.type(
      screen.getByLabelText('An incident response process is defined for the workload.'),
      'Quarterly game days.',
    )
    await user.click(screen.getByTestId('generate-summary'))

    const draft = screen.getByTestId('summary-draft')
    await user.clear(draft)
    await user.type(draft, 'Rewritten by hand before sending.')
    await user.click(screen.getByTestId('submit-re-review'))

    expect(reReview).toHaveBeenCalledWith('rev-1', {
      feedback: 'Rewritten by hand before sending.',
    })
  })

  it('sends the block as an ordinary feedback string and nothing else', async () => {
    // No new endpoint, no new field, no format the server has to understand. The
    // collated block is just text, exactly as a hand-typed note would be.
    const user = userEvent.setup()
    mount([finding()])

    await user.type(
      screen.getByLabelText('An incident response process is defined for the workload.'),
      'Quarterly game days.',
    )
    await user.click(screen.getByTestId('generate-summary'))
    await user.click(screen.getByTestId('submit-re-review'))

    expect(reReview).toHaveBeenCalledTimes(1)
    const [id, options] = reReview.mock.calls[0]!
    expect(id).toBe('rev-1')
    expect(Object.keys(options)).toEqual(['feedback'])
    expect(options.feedback).toContain('Regarding An incident response process')
    expect(options.feedback).toContain('(oe_incident_response)')
  })

  it('hands the new review id back so the caller can poll it', async () => {
    const user = userEvent.setup()
    const onStarted = mount([finding()])

    await user.type(
      screen.getByLabelText('An incident response process is defined for the workload.'),
      'Quarterly game days.',
    )
    await user.click(screen.getByTestId('generate-summary'))
    await user.click(screen.getByTestId('submit-re-review'))

    expect(onStarted).toHaveBeenCalledWith('rev-2', expect.any(Number))
  })

  it('can go back to the answers without losing them', async () => {
    const user = userEvent.setup()
    mount([finding()])

    const field = screen.getByLabelText(
      'An incident response process is defined for the workload.',
    )
    await user.type(field, 'Quarterly game days.')
    await user.click(screen.getByTestId('generate-summary'))
    await user.click(screen.getByRole('button', { name: /back to answers/i }))

    expect(
      screen.getByLabelText('An incident response process is defined for the workload.'),
    ).toHaveValue('Quarterly game days.')
  })

  // ------------------------------------------------------------------------- #
  // The server's character cap
  // ------------------------------------------------------------------------- #

  it('refuses to submit an over-length block rather than truncating it', async () => {
    // Silently cutting a governance submission at the cap would send
    // something the reviewer never wrote and never saw. The block is refused with
    // the overage named instead.
    const user = userEvent.setup()
    mount([finding()])

    await user.type(
      screen.getByLabelText('An incident response process is defined for the workload.'),
      'x',
    )
    await user.click(screen.getByTestId('generate-summary'))

    const draft = screen.getByTestId('summary-draft')
    await user.clear(draft)
    // Paste rather than type: 4,100 keystrokes is not a test, it is a timeout.
    await user.click(draft)
    await user.paste('y'.repeat(MAX_FEEDBACK_CHARS + 100))

    expect(screen.getByTestId('budget')).toHaveTextContent(
      new RegExp(`over the ${MAX_FEEDBACK_CHARS} limit`, 'i'),
    )
    expect(screen.getByTestId('submit-re-review')).toBeDisabled()
    expect(reReview).not.toHaveBeenCalled()
  })

  it('shows the remaining budget while it is still within the limit', async () => {
    const user = userEvent.setup()
    mount([finding()])

    await user.type(
      screen.getByLabelText('An incident response process is defined for the workload.'),
      'Quarterly game days.',
    )
    await user.click(screen.getByTestId('generate-summary'))

    expect(screen.getByTestId('budget')).toHaveTextContent(/characters left/i)
    expect(screen.getByTestId('submit-re-review')).toBeEnabled()
  })

  it('warns before generating when the answers already exceed the cap', async () => {
    const user = userEvent.setup()
    mount([finding()])

    const field = screen.getByLabelText(
      'An incident response process is defined for the workload.',
    )
    await user.click(field)
    await user.paste('z'.repeat(MAX_FEEDBACK_CHARS + 100))

    expect(screen.getByTestId('pre-budget')).toHaveTextContent(
      new RegExp(`over the ${MAX_FEEDBACK_CHARS}`, 'i'),
    )
  })

  it('says so when the endpoint refuses, instead of failing silently', async () => {
    reReview.mockRejectedValue(new Error('network down'))
    const user = userEvent.setup()
    mount([finding()])

    await user.type(
      screen.getByLabelText('An incident response process is defined for the workload.'),
      'Quarterly game days.',
    )
    await user.click(screen.getByTestId('generate-summary'))
    await user.click(screen.getByTestId('submit-re-review'))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /could not start the re-review/i,
    )
  })

  it('says there is nothing to ask when nothing is open', () => {
    mount([finding({ status: 'pass' })])

    expect(screen.getByTestId('open-questions')).toHaveTextContent(/nothing is open/i)
    expect(screen.getByTestId('generate-summary')).toBeDisabled()
  })

  it('offers the general catch-all field regardless of the findings', () => {
    mount([finding()])

    expect(
      within(screen.getByTestId('open-questions')).getByText(
        /operational practices, incident history, cost governance, or AI oversight/i,
      ),
    ).toBeInTheDocument()
  })
})
