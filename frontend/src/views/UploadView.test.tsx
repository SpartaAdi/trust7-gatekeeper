import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { UploadView } from './UploadView'

vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {},
  uploadFile: vi.fn(),
  submitReview: vi.fn(),
}))

describe('UploadView', () => {
  it('renders both file pickers and the submit control', () => {
    render(<UploadView onStarted={vi.fn()} />)

    // By label, not by text: the intro paragraph also mentions both, and this
    // additionally asserts each label is wired to its input.
    expect(screen.getByLabelText('Solution document / SoW')).toBeInTheDocument()
    expect(screen.getByLabelText('Architecture diagram')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start review/i })).toBeInTheDocument()
  })

  it('disables submit until a file is chosen', () => {
    render(<UploadView onStarted={vi.fn()} />)
    expect(screen.getByRole('button', { name: /start review/i })).toBeDisabled()
  })

  it('renders the re-review variant when a previous review is supplied', () => {
    render(<UploadView previousReviewId="rev-1" onStarted={vi.fn()} />)

    expect(
      screen.getByRole('heading', { name: /submit the revised design/i }),
    ).toBeInTheDocument()
    expect(screen.getByText('rev-1')).toBeInTheDocument()
  })
})
