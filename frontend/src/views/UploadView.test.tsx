import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { submitReview, uploadFile } from '../api'
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

  // The demo path: pick both files, submit, hand the review id upward.
  it('uploads both files and submits their keys', async () => {
    vi.mocked(uploadFile)
      .mockResolvedValueOnce('uploads/a/sow.pdf')
      .mockResolvedValueOnce('uploads/b/design.drawio')
    vi.mocked(submitReview).mockResolvedValue({
      review_id: 'rev-9',
      status_url: '/reviews/rev-9/status',
      result_url: '/reviews/rev-9',
    })
    const onStarted = vi.fn()
    const user = userEvent.setup()

    render(<UploadView onStarted={onStarted} />)
    await user.upload(
      screen.getByLabelText('Solution document / SoW'),
      new File(['sow'], 'sow.pdf', { type: 'application/pdf' }),
    )
    await user.upload(
      screen.getByLabelText('Architecture diagram'),
      new File(['<mxfile/>'], 'design.drawio', { type: 'application/xml' }),
    )
    await user.click(screen.getByRole('button', { name: /start review/i }))

    await waitFor(() => expect(onStarted).toHaveBeenCalledWith('rev-9'))
    expect(uploadFile).toHaveBeenCalledTimes(2)
    expect(submitReview).toHaveBeenCalledWith(
      expect.objectContaining({
        documentKey: 'uploads/a/sow.pdf',
        diagramKey: 'uploads/b/design.drawio',
      }),
    )
  })

  it('renders the re-review variant when a previous review is supplied', () => {
    render(<UploadView previousReviewId="rev-1" onStarted={vi.fn()} />)

    expect(
      screen.getByRole('heading', { name: /submit the revised design/i }),
    ).toBeInTheDocument()
    expect(screen.getByText('rev-1')).toBeInTheDocument()
  })
})
