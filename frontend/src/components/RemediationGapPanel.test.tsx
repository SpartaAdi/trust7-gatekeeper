import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { RemediationGapPanel } from './RemediationGapPanel'
import type { RemediationGap } from '../types'

/**
 * What a user is told when the roadmap has no guidance in it.
 *
 * Measured before this panel existed, on a review reproducing the real failure
 * (remediate 0 of 28, retry 0 of 28): the page carried zero `role="alert"`, one
 * `role="status"` — the data-fidelity panel reporting "Diagram structure read: 100%
 * of elements" — and sixteen identical rows reading "No remediation text was
 * generated for this check." The one page-level signal present was reassurance about
 * an unrelated thing.
 *
 * So the assertions here are about a reader learning the fact ONCE, at the top,
 * without having to scroll a roadmap and infer it from repetition.
 */

const gap = (over: Partial<RemediationGap> = {}): RemediationGap => ({
  open_findings: 28,
  without_guidance: 28,
  check_ids: ['rel_backup', 'sec_encryption_at_rest'],
  ...over,
})

describe('RemediationGapPanel', () => {
  it('renders nothing when every open finding has guidance', () => {
    const { container } = render(
      <RemediationGapPanel gap={gap({ without_guidance: 0, check_ids: [] })} />,
    )
    // A panel on every healthy review is noise, and noise is what teaches people
    // to stop reading panels.
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when the review carries no gap record', () => {
    const { container } = render(<RemediationGapPanel gap={undefined} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('says the whole roadmap is empty when it is', () => {
    render(<RemediationGapPanel gap={gap()} />)
    const panel = screen.getByTestId('remediation-gap-panel')
    expect(screen.getByTestId('remediation-gap')).toHaveAttribute('data-total', 'true')
    expect(panel).toHaveTextContent('No remediation guidance was generated for this review')
    expect(panel).toHaveTextContent(/nothing on the first attempt and nothing on the automatic retry/)
  })

  it('counts a partial shortfall rather than calling it total', () => {
    render(<RemediationGapPanel gap={gap({ without_guidance: 6 })} />)
    expect(screen.getByTestId('remediation-gap')).toHaveAttribute('data-total', 'false')
    expect(screen.getByTestId('remediation-gap-panel')).toHaveTextContent(
      '6 of 28 actions have no remediation guidance',
    )
  })

  it('uses the singular for one missing action', () => {
    render(<RemediationGapPanel gap={gap({ open_findings: 4, without_guidance: 1 })} />)
    expect(screen.getByTestId('remediation-gap-panel')).toHaveTextContent(
      '1 of 4 action has no remediation guidance',
    )
  })

  it('cautions rather than reporting a measurement', () => {
    // Unlike the fidelity numbers this is not something to weigh — it is part of
    // the deliverable that did not get produced.
    render(<RemediationGapPanel gap={gap()} />)
    expect(screen.getByTestId('remediation-gap-panel')).toHaveAttribute(
      'data-tone',
      'caution',
    )
  })

  it('names the affected checks so the claim is checkable', () => {
    render(<RemediationGapPanel gap={gap({ without_guidance: 2 })} />)
    const panel = screen.getByTestId('remediation-gap-panel')
    expect(panel).toHaveTextContent('rel_backup')
    expect(panel).toHaveTextContent('sec_encryption_at_rest')
  })

  it('caps the id list rather than printing fifty of them', () => {
    const many = Array.from({ length: 28 }, (_, i) => `check_${i}`)
    render(<RemediationGapPanel gap={gap({ check_ids: many })} />)
    const panel = screen.getByTestId('remediation-gap-panel')
    expect(panel).toHaveTextContent('+16 more')
    expect(panel).not.toHaveTextContent('check_27')
  })

  it('says the scores are unaffected, because they are', () => {
    // Remediation text is not a scoring input. Without saying so, a caution panel
    // above the score invites the reader to distrust the number too.
    for (const value of [gap(), gap({ without_guidance: 3 })]) {
      const { unmount } = render(<RemediationGapPanel gap={value} />)
      expect(screen.getByTestId('remediation-gap-panel')).toHaveTextContent(
        /scores?\b[^.]*\bunaffected/i,
      )
      unmount()
    }
  })

  it('states that nothing was invented to fill the gap', () => {
    render(<RemediationGapPanel gap={gap()} />)
    expect(screen.getByTestId('remediation-gap-panel')).toHaveTextContent(
      /Nothing has been invented to fill the gap/,
    )
  })

  it('warns about the two silent downstream effects on a total failure', () => {
    // Both mislead in the direction of looking complete, and a reader will not
    // discover either on their own.
    render(<RemediationGapPanel gap={gap()} />)
    const panel = screen.getByTestId('remediation-gap-panel')
    // The copied prompt falls back to finding titles and looks finished.
    expect(panel).toHaveTextContent(/Copy fix-it prompt/)
    // The roadmap's phases come from an effort estimate that is blank whenever the
    // text is, so "Immediate" reflects an absent estimate rather than a cheap fix.
    expect(panel).toHaveTextContent(/effort estimate that was also not returned/)
  })

  it('does not raise those on a partial shortfall, where the phases still mean something', () => {
    render(<RemediationGapPanel gap={gap({ without_guidance: 2 })} />)
    const panel = screen.getByTestId('remediation-gap-panel')
    expect(panel).not.toHaveTextContent(/Copy fix-it prompt/)
  })

  it('is announced as a status, not an alert', () => {
    // The review succeeded and its findings are usable. Announcing this as an alert
    // puts it in the same class as "the pipeline crashed".
    render(<RemediationGapPanel gap={gap()} />)
    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
