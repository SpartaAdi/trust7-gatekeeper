import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { AiDetectionPanel, pillarNotApplicableReason } from './AiDetectionPanel'
import type { AiDetection, AiSignal } from '../types'

/**
 * What this pins, and why.
 *
 * Nineteen of the forty-five checks only mean anything if the design has an AI or ML
 * component. Before this panel the only trace of that decision on screen was a pillar
 * reading "Not applicable to this design" — a conclusion with no argument, which
 * neither a reviewer nor a judge could contest.
 *
 * So the assertions here are about CONTESTABILITY, not cosmetics: that the reasoning
 * is on screen, that the component labels that were searched are on screen, that the
 * evidence is quotable back to its source, and that the two cases needing a human
 * (`contradicted`, and a disagreement with a skipped pillar) look different from the
 * two that do not.
 *
 * And one negative: `not_run` must never render as "no AI detected". One means nobody
 * looked; the other is a claim about the design.
 */

function signal(over: Partial<AiSignal> = {}): AiSignal {
  return {
    tier: 'named_service',
    signal: 'Amazon Bedrock',
    source: 'diagram component “Summariser”',
    excerpt: 'Summariser bedrock aws',
    ...over,
  }
}

function detection(over: Partial<AiDetection> = {}): AiDetection {
  return {
    signals: [signal()],
    patterns_checked: 96,
    components_seen: ['Summariser', 'API', 'Postgres'],
    verdict: 'present',
    rationale: 'AI/ML component detected. Evidence: Amazon Bedrock.',
    ...over,
  }
}

const ABSENT = detection({
  signals: [],
  verdict: 'absent',
  rationale:
    'No AI/ML component detected. 96 AI/ML patterns were checked against 3 ' +
    'components: Expense API, Receipts bucket, Claims database.',
  components_seen: ['Expense API', 'Receipts bucket', 'Claims database'],
})

describe('AiDetectionPanel', () => {
  it('renders nothing when the review carries no detection record', () => {
    const { container } = render(<AiDetectionPanel detection={undefined} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('states the reasoning, not just the conclusion', () => {
    render(<AiDetectionPanel detection={detection()} />)
    expect(screen.getByTestId('ai-detection')).toHaveAttribute('data-verdict', 'present')
    // The title states the conclusion; the body carries the argument. Both, not
    // either — the title alone is the silent not-applicable in a new font.
    const panel = screen.getByTestId('ai-detection-panel')
    expect(panel).toHaveTextContent('AI/ML component detected')
    expect(panel).toHaveTextContent('Evidence: Amazon Bedrock')
  })

  it('names the components it searched when it found nothing', () => {
    // The half that makes an "absent" verdict contestable: a reviewer who sees a
    // suspicious label in this list can overrule it on sight.
    render(<AiDetectionPanel detection={ABSENT} />)
    const panel = screen.getByTestId('ai-detection')
    expect(panel).toHaveTextContent('No AI/ML component detected')
    expect(panel).toHaveTextContent('Expense API')
    expect(panel).toHaveTextContent('Claims database')
    expect(panel).toHaveTextContent('96 AI/ML patterns')
  })

  it('reports how much work the search did, so "found nothing" is weighable', () => {
    render(<AiDetectionPanel detection={detection()} />)
    expect(screen.getByTestId('ai-detection-panel')).toHaveTextContent(
      /96 patterns checked · 1 match · 3 components searched/,
    )
  })

  it('shows every signal with its source and the quoted text, on request', async () => {
    const user = userEvent.setup()
    render(
      <AiDetectionPanel
        detection={detection({
          signals: [
            signal(),
            signal({
              tier: 'implicit_function',
              signal: 'recommendation engine',
              source: 'solution document',
              excerpt: '…the recommendation engine ranks offers per customer…',
            }),
          ],
        })}
      />,
    )

    // Collapsed by default: the panel's job is the verdict, the evidence is the
    // audit trail behind it.
    expect(screen.queryByTestId('ai-detection-evidence')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Show the evidence \(2\)/ }))

    const evidence = screen.getByTestId('ai-detection-evidence')
    expect(evidence).toHaveTextContent('Amazon Bedrock')
    expect(evidence).toHaveTextContent('named AI service')
    expect(evidence).toHaveTextContent('diagram component “Summariser”')
    expect(evidence).toHaveTextContent('recommendation engine')
    expect(evidence).toHaveTextContent('implied capability')
    expect(evidence).toHaveTextContent('the recommendation engine ranks offers')
  })

  it('has no evidence toggle when there is no evidence to show', () => {
    render(<AiDetectionPanel detection={ABSENT} />)
    expect(screen.queryByRole('button', { name: /evidence/i })).not.toBeInTheDocument()
  })

  // ---- tone: only the two cases needing a human get caution ---------------- //

  it('stays neutral when the record and the review agree', () => {
    render(<AiDetectionPanel detection={detection()} />)
    expect(screen.getByTestId('ai-detection-panel')).toHaveAttribute(
      'data-tone',
      'neutral',
    )
  })

  it('stays neutral on a correctly-empty design', () => {
    render(<AiDetectionPanel detection={ABSENT} />)
    expect(screen.getByTestId('ai-detection-panel')).toHaveAttribute(
      'data-tone',
      'neutral',
    )
  })

  it('cautions when the design contradicts itself', () => {
    render(
      <AiDetectionPanel
        detection={detection({
          verdict: 'contradicted',
          rationale:
            'The design states it has no AI/ML, but AI/ML evidence was also found: ' +
            'Amazon Bedrock. Document and diagram may disagree.',
        })}
      />,
    )
    expect(screen.getByTestId('ai-detection-panel')).toHaveAttribute(
      'data-tone',
      'caution',
    )
    expect(screen.getByTestId('ai-detection-panel')).toHaveTextContent(/may disagree/)
  })

  it('cautions, and says nothing was changed, when it disagrees with the review', () => {
    // The case the round exists for: AI evidence found, AI checks scored n/a anyway.
    // The panel must report the conflict AND state that it did not resolve it — a
    // keyword record is more auditable than the model, not more right.
    render(<AiDetectionPanel detection={detection()} disagrees />)
    const panel = screen.getByTestId('ai-detection-panel')
    expect(panel).toHaveAttribute('data-tone', 'caution')
    expect(panel).toHaveTextContent(/marked not applicable/i)
    expect(panel).toHaveTextContent(/Nothing has been changed automatically/)
    expect(screen.getByTestId('ai-detection')).toHaveAttribute('data-disagrees', 'true')
  })

  // ---- not_run is not a finding ------------------------------------------- //

  it('never presents a review with no detection as "no AI found"', () => {
    render(
      <AiDetectionPanel
        detection={detection({
          signals: [],
          patterns_checked: 0,
          components_seen: [],
          verdict: 'not_run',
          rationale:
            'AI/ML detection did not run for this review — it was stored before the ' +
            'check existed. This is not a finding that the design has no AI/ML ' +
            'component; re-run the review to establish that either way.',
        })}
      />,
    )
    const panel = screen.getByTestId('ai-detection-panel')
    expect(panel).toHaveTextContent(/did not run for this review/)
    expect(panel).toHaveTextContent(/not a finding that the design has no AI\/ML/)
    // A "0 patterns checked" line would read as a measurement of the design.
    expect(panel).not.toHaveTextContent(/0 patterns checked/)
  })

  it('does not caution on a not-run record — nobody looked, nothing conflicts', () => {
    render(
      <AiDetectionPanel
        detection={detection({ verdict: 'not_run', signals: [], patterns_checked: 0 })}
      />,
    )
    expect(screen.getByTestId('ai-detection-panel')).toHaveAttribute(
      'data-tone',
      'neutral',
    )
  })

  // ---- the count line explains itself ------------------------------------- //

  it('explains what the three counts mean, inline', () => {
    // The line shipped with no explanation, and each number invites a wrong reading.
    // "96 patterns checked" is the one that matters: it looks like a property of the
    // design and is a constant, so a reviewer could take it for 96 things found or 96
    // checks run against their architecture.
    render(<AiDetectionPanel detection={detection()} />)
    const legend = screen.getByTestId('ai-detection-count-legend')

    expect(legend).toHaveTextContent(/Patterns checked/)
    expect(legend).toHaveTextContent(/the same number on every review/)
    expect(legend).toHaveTextContent(/not anything about this design/)
    expect(legend).toHaveTextContent(/Matches/)
    expect(legend).toHaveTextContent(/how many of them fired here/)
    expect(legend).toHaveTextContent(/components searched/)
    expect(legend).toHaveTextContent(/had their text read/)
  })

  it('explains the counts inside the panel, not adrift below it', () => {
    // Same reasoning as the evidence disclosure: a legend for a line has to sit with
    // the line it describes, which means inside the block that draws it.
    render(<AiDetectionPanel detection={detection()} />)

    expect(screen.getByTestId('ai-detection-panel')).toContainElement(
      screen.getByTestId('ai-detection-count-legend'),
    )
  })

  it('omits the legend when there is no count line to explain', () => {
    // `not_run` renders no counts, so a legend for them would describe nothing.
    render(
      <AiDetectionPanel
        detection={detection({ verdict: 'not_run', signals: [], patterns_checked: 0 })}
      />,
    )
    expect(screen.queryByTestId('ai-detection-count-legend')).toBeNull()
  })

  // ---- singular/plural, because these strings are read closely ------------ //

  it('counts one match and one component in the singular', () => {
    render(
      <AiDetectionPanel
        detection={detection({ signals: [signal()], components_seen: ['Summariser'] })}
      />,
    )
    expect(screen.getByTestId('ai-detection-panel')).toHaveTextContent(
      /1 match · 1 component searched/,
    )
  })
})

describe('pillarNotApplicableReason', () => {
  it('replaces the bare conclusion with the record’s own reasoning', () => {
    const reason = pillarNotApplicableReason(ABSENT)
    expect(reason).toContain('Not applicable to this design')
    // The argument, not just the verdict.
    expect(reason).toContain('96 AI/ML patterns')
    expect(reason).toContain('Expense API')
  })

  it('says a record is missing rather than inventing a reason', () => {
    for (const value of [
      undefined,
      detection({ verdict: 'not_run', patterns_checked: 0, signals: [] }),
    ]) {
      const reason = pillarNotApplicableReason(value)
      expect(reason).toContain('no detection record was stored')
      expect(reason).not.toContain('patterns')
    }
  })

  it('reuses the backend sentence verbatim so the two surfaces cannot diverge', () => {
    const record = detection({
      verdict: 'likely',
      rationale: 'AI/ML component likely but never labelled as one. Suggestive: X.',
    })
    expect(pillarNotApplicableReason(record)).toContain(record.rationale)
  })
})
