import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ReviewSummary } from '../types'
import { HistoryView } from './HistoryView'

const { listReviews } = vi.hoisted(() => ({ listReviews: vi.fn() }))

vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {},
  listReviews,
}))

function summary(overrides: Partial<ReviewSummary> = {}): ReviewSummary {
  return {
    review_id: 'rev-1',
    title: 'Payments platform',
    created_at: '2026-07-27T10:04:00Z',
    overall_score: 62.5,
    open_findings: 12,
    high_severity_open: 3,
    has_delta: false,
    pillars: [
      {
        framework: 'aws_waf',
        pillar_id: 'security',
        pillar_name: 'Security',
        score: 55,
        checks_evaluated: 7,
      },
      {
        framework: 'trust7',
        pillar_id: 'ai_governance',
        pillar_name: 'AI governance',
        score: 60,
        checks_evaluated: 3,
      },
    ],
    ...overrides,
  }
}

describe('HistoryView', () => {
  it('lists past reviews with name, score, and maturity', async () => {
    listReviews.mockResolvedValue([summary()])
    render(<HistoryView onOpen={vi.fn()} onNewReview={vi.fn()} />)

    expect(await screen.findByText('Payments platform')).toBeInTheDocument()
    expect(screen.getByText('62.5')).toBeInTheDocument()
    expect(screen.getByText('Governed')).toBeInTheDocument()
    expect(screen.getByText(/3 high/)).toBeInTheDocument()
  })

  it('renders a pillar heatmap with an accessible description', async () => {
    listReviews.mockResolvedValue([summary()])
    render(<HistoryView onOpen={vi.fn()} onNewReview={vi.fn()} />)

    expect(
      await screen.findByRole('img', { name: /pillar scores.*security 55/i }),
    ).toBeInTheDocument()
  })

  it('opens a stored review without re-analysis', async () => {
    listReviews.mockResolvedValue([summary()])
    const onOpen = vi.fn()
    const user = userEvent.setup()
    render(<HistoryView onOpen={onOpen} onNewReview={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: /payments platform/i }))

    expect(onOpen).toHaveBeenCalledWith('rev-1')
  })

  it('offers a first-run prompt when there is no history', async () => {
    listReviews.mockResolvedValue([])
    const onNewReview = vi.fn()
    const user = userEvent.setup()
    render(<HistoryView onOpen={vi.fn()} onNewReview={onNewReview} />)

    expect(await screen.findByText(/nothing reviewed yet/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /start the first review/i }))
    expect(onNewReview).toHaveBeenCalled()
  })

  it('surfaces a load failure instead of an empty list', async () => {
    listReviews.mockRejectedValue(new Error('boom'))
    render(<HistoryView onOpen={vi.fn()} onNewReview={vi.fn()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /could not load past reviews/i,
    )
  })
})
