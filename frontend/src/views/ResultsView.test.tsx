import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { resultFixture } from '../test/fixtures'
import { ResultsView } from './ResultsView'

const { getReview } = vi.hoisted(() => ({ getReview: vi.fn() }))

vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {},
  getReview,
}))

describe('ResultsView', () => {
  it('renders the score, pillars, and findings', async () => {
    getReview.mockResolvedValue(resultFixture())

    render(<ResultsView reviewId="rev-1" onReReview={vi.fn()} onStartOver={vi.fn()} />)

    expect(
      await screen.findByRole('heading', { name: /payments platform/i }),
    ).toBeInTheDocument()
    expect(screen.getByText('62.5')).toBeInTheDocument()
    expect(screen.getByText('AWS Well-Architected Framework')).toBeInTheDocument()
    expect(screen.getByText('Minfy TRUST-7 Framework')).toBeInTheDocument()
    expect(screen.getByText('Security')).toBeInTheDocument()
    expect(
      screen.getByText(/customer data store has no encryption at rest/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/enable sse-kms on the table/i)).toBeInTheDocument()
  })

  it('renders the delta panel when the review was a re-review', async () => {
    getReview.mockResolvedValue(
      resultFixture({
        delta: {
          previous_review_id: 'rev-0',
          previous_overall_score: 50,
          current_overall_score: 62.5,
          change: 12.5,
          pillars: [
            {
              framework: 'aws_waf',
              pillar_id: 'security',
              pillar_name: 'Security',
              previous_score: 40,
              current_score: 55,
              change: 15,
            },
          ],
          resolved_checks: ['sec_secrets_mgmt'],
          new_checks: [],
          unchanged_failures: ['sec_encryption_at_rest'],
        },
      }),
    )

    render(<ResultsView reviewId="rev-1" onReReview={vi.fn()} onStartOver={vi.fn()} />)

    expect(
      await screen.findByRole('heading', { name: /change since the previous review/i }),
    ).toBeInTheDocument()
    expect(screen.getByText('50.0 → 62.5')).toBeInTheDocument()
  })

  it('shows an error instead of an empty page when the fetch fails', async () => {
    getReview.mockRejectedValue(new Error('boom'))

    render(<ResultsView reviewId="rev-1" onReReview={vi.fn()} onStartOver={vi.fn()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /could not load the review/i,
    )
  })
})
