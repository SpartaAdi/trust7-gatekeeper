import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, getSharedReview } from '../api'
import type { SharedReview } from '../types'
import { SharedView } from './SharedView'

vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {
    status: number
    constructor(message: string, status: number) {
      super(message)
      this.status = status
    }
  },
  getSharedReview: vi.fn(),
}))

const EXPIRES_NOTE =
  'This link stops working when the server restarts. Reviews are stored on ' +
  "Render's free-tier disk, which is wiped on restart."

function sharedFixture(overrides: Partial<SharedReview> = {}): SharedReview {
  return {
    review_id: 'rev-1',
    title: 'Payments platform',
    created_at: '2026-07-29T10:00:00Z',
    overall_score: 61.5,
    frameworks: ['AWS Well-Architected', 'Minfy TRUST-7'],
    pillars: [
      {
        framework: 'aws',
        pillar_id: 'security',
        pillar_name: 'Security',
        score: 48.0,
        checks_evaluated: 8,
        checks_passed: 4,
      },
    ],
    open_findings: 3,
    high_severity_open: 1,
    component_count: 4,
    delta: null,
    expires_note: EXPIRES_NOTE,
    ...overrides,
  }
}

describe('SharedView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the scoreboard for a valid link', async () => {
    vi.mocked(getSharedReview).mockResolvedValue(sharedFixture())

    render(<SharedView reviewId="rev-1" token="tok" />)

    expect(await screen.findByText('Payments platform')).toBeInTheDocument()
    expect(screen.getByText('61.5')).toBeInTheDocument()
    expect(screen.getByText('Security')).toBeInTheDocument()
    expect(screen.getByText('4/8')).toBeInTheDocument()
  })

  it('marks itself read-only, so nobody expects to act on it here', async () => {
    vi.mocked(getSharedReview).mockResolvedValue(sharedFixture())

    render(<SharedView reviewId="rev-1" token="tok" />)
    await screen.findByText('Payments platform')

    expect(screen.getByText(/read only/i)).toBeInTheDocument()
  })

  /**
   * The requirement this feature was given: do not silently assume persistence.
   * The note is rendered from the server's own text, so the two cannot drift.
   */
  it('states on the page that the link does not survive a restart', async () => {
    vi.mocked(getSharedReview).mockResolvedValue(sharedFixture())

    render(<SharedView reviewId="rev-1" token="tok" />)

    expect(await screen.findByText(EXPIRES_NOTE)).toBeInTheDocument()
  })

  it('explains a dead link in terms of the ephemeral disk, not a generic error', async () => {
    vi.mocked(getSharedReview).mockRejectedValue(new ApiError('nope', 404))

    render(<SharedView reviewId="rev-1" token="tok" />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/no longer valid/i)
    expect(alert).toHaveTextContent(/wiped when the server restarts/i)
  })

  it('shows the score movement when the review has a delta', async () => {
    vi.mocked(getSharedReview).mockResolvedValue(
      sharedFixture({
        delta: {
          previous_review_id: 'rev-0',
          previous_overall_score: 52.0,
          current_overall_score: 61.5,
          change: 9.5,
          pillars: [],
          resolved_checks: ['a', 'b'],
          new_checks: [],
          unchanged_failures: ['c'],
        },
      }),
    )

    render(<SharedView reviewId="rev-1" token="tok" />)
    await screen.findByText('Payments platform')

    expect(screen.getByText(/\+9\.5 since the previous review/)).toBeInTheDocument()
    expect(screen.getByText(/2 resolved, 0 new, 1 still open/)).toBeInTheDocument()
  })

  it('says "no change" rather than "+0.0", which reads as a rounding artefact', async () => {
    vi.mocked(getSharedReview).mockResolvedValue(
      sharedFixture({
        delta: {
          previous_review_id: 'rev-0',
          previous_overall_score: 61.5,
          current_overall_score: 61.5,
          change: 0,
          pillars: [],
          resolved_checks: [],
          new_checks: [],
          unchanged_failures: [],
        },
      }),
    )

    render(<SharedView reviewId="rev-1" token="tok" />)
    await screen.findByText('Payments platform')

    expect(screen.getByText('No change')).toBeInTheDocument()
  })

  it('omits the delta section entirely for a first review', async () => {
    vi.mocked(getSharedReview).mockResolvedValue(sharedFixture({ delta: null }))

    render(<SharedView reviewId="rev-1" token="tok" />)
    await screen.findByText('Payments platform')

    expect(screen.queryByText(/change since the previous review/i)).not.toBeInTheDocument()
  })
})
