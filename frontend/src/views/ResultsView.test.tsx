import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { resultFixture } from '../test/fixtures'
import { ResultsView } from './ResultsView'

const { getReview, downloadReport } = vi.hoisted(() => ({
  getReview: vi.fn(),
  downloadReport: vi.fn(),
}))

vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {},
  getReview,
  downloadReport,
}))

describe('ResultsView', () => {
  it('renders the score, pillars, and findings', async () => {
    getReview.mockResolvedValue(resultFixture())

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)

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

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)

    expect(
      await screen.findByRole('heading', { name: /change since the previous review/i }),
    ).toBeInTheDocument()
    expect(screen.getByText('50.0 → 62.5')).toBeInTheDocument()
  })

  it('shows an error instead of an empty page when the fetch fails', async () => {
    getReview.mockRejectedValue(new Error('boom'))

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /could not load the review/i,
    )
  })

  describe('Download Report', () => {
    /** jsdom implements neither createObjectURL nor anchor navigation. */
    function stubObjectUrl() {
      const createObjectURL = vi.fn(() => 'blob:report')
      const revokeObjectURL = vi.fn()
      Object.assign(URL, { createObjectURL, revokeObjectURL })
      return { createObjectURL, revokeObjectURL }
    }

    it('downloads the PDF with the filename the server chose', async () => {
      getReview.mockResolvedValue(resultFixture())
      downloadReport.mockResolvedValue({
        blob: new Blob(['%PDF-1.4'], { type: 'application/pdf' }),
        filename: 'trust7-payments-platform.pdf',
      })
      const { createObjectURL, revokeObjectURL } = stubObjectUrl()
      const clicks: string[] = []
      vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
        this: HTMLAnchorElement,
      ) {
        clicks.push(this.download)
      })
      const user = userEvent.setup()

      render(<ResultsView
          reviewId="rev-1"
          onReReview={vi.fn()}
          onStartOver={vi.fn()}
          onBackToHistory={vi.fn()}
        />)

      await user.click(await screen.findByRole('button', { name: /download report/i }))

      await waitFor(() => expect(downloadReport).toHaveBeenCalledWith('rev-1'))
      expect(createObjectURL).toHaveBeenCalled()
      expect(clicks).toEqual(['trust7-payments-platform.pdf'])
      // Not revoking would pin the blob in memory for the life of the page.
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:report')
    })

    it('surfaces a generation failure instead of failing silently', async () => {
      getReview.mockResolvedValue(resultFixture())
      downloadReport.mockRejectedValue(new Error('boom'))
      stubObjectUrl()
      const user = userEvent.setup()

      render(<ResultsView
          reviewId="rev-1"
          onReReview={vi.fn()}
          onStartOver={vi.fn()}
          onBackToHistory={vi.fn()}
        />)

      await user.click(await screen.findByRole('button', { name: /download report/i }))

      expect(await screen.findByRole('alert')).toHaveTextContent(
        /could not download the report/i,
      )
      // The button must come back, so a transient failure is retryable.
      expect(screen.getByRole('button', { name: /download report/i })).toBeEnabled()
    })

    it('disables the button while the PDF is being prepared', async () => {
      getReview.mockResolvedValue(resultFixture())
      let release: (value: unknown) => void = () => {}
      downloadReport.mockReturnValue(new Promise((resolve) => (release = resolve)))
      stubObjectUrl()
      vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
      const user = userEvent.setup()

      render(<ResultsView
          reviewId="rev-1"
          onReReview={vi.fn()}
          onStartOver={vi.fn()}
          onBackToHistory={vi.fn()}
        />)

      await user.click(await screen.findByRole('button', { name: /download report/i }))

      const busy = screen.getByRole('button', { name: /preparing pdf/i })
      expect(busy).toBeDisabled()

      release({ blob: new Blob(['x']), filename: 'r.pdf' })
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /download report/i })).toBeEnabled(),
      )
    })
  })
})
