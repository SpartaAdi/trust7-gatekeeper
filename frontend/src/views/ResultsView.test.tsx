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

    // The findings list itself, not the action-items shortlist above it — both
    // draw on the same finding, so this query is scoped to the collapsed group.
    const group = screen.getByRole('button', { name: /high severity/i })
    await userEvent.click(group)
    const list = group.parentElement!
    expect(list).toHaveTextContent(/customer data store has no encryption at rest/i)

    // The remediation text lives one level deeper, inside the finding itself.
    await userEvent.click(
      screen.getByRole('button', { name: /sec_encryption_at_rest/i }),
    )
    expect(list).toHaveTextContent(/enable sse-kms on the table/i)
  })

  it('shows the score out of 100, not as a bare number', async () => {
    getReview.mockResolvedValue(resultFixture({ overall_score: 4.5 }))

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)

    // A bare "4.5" reads as 4.5 out of 5 to anyone who has seen a star rating.
    expect(await screen.findByText('/100')).toBeInTheDocument()
  })

  it('explains the maturity tiers on demand, using the scoring boundaries', async () => {
    getReview.mockResolvedValue(resultFixture())
    const user = userEvent.setup()

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)

    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
    await user.click(
      await screen.findByRole('button', { name: /what the maturity tiers mean/i }),
    )

    const tip = screen.getByRole('tooltip')
    // Every tier, with the boundaries that maturity.ts actually uses.
    for (const { label, range } of [
      { label: 'Pioneering', range: '90–100' },
      { label: 'Certified', range: '75–90' },
      { label: 'Governed', range: '60–75' },
      { label: 'Managed', range: '40–60' },
      { label: 'Aware', range: '0–40' },
    ]) {
      expect(tip).toHaveTextContent(label)
      expect(tip).toHaveTextContent(range)
    }
  })

  it('lists top action items above the executive summary', async () => {
    getReview.mockResolvedValue(resultFixture())

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)

    const actions = await screen.findByTestId('top-actions')
    // The imperative comes from the finding's own remediation text.
    expect(actions).toHaveTextContent(/enable sse-kms on the table/i)
    // And the observation it addresses is kept as context.
    expect(actions).toHaveTextContent(/customer data store has no encryption/i)

    // Position matters: this is a shortlist to read before the narrative.
    const summary = screen.getByText(/this design scores 62.5 of 100/i)
    expect(
      actions.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it('omits the action list entirely when nothing is open at high severity', async () => {
    getReview.mockResolvedValue(
      resultFixture({
        findings: resultFixture().findings.map((f) => ({ ...f, status: 'pass' as const })),
      }),
    )

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)

    await screen.findByRole('heading', { name: /payments platform/i })
    expect(screen.queryByTestId('top-actions')).not.toBeInTheDocument()
  })

  describe('findings accordion', () => {
    it('starts every severity group collapsed', async () => {
      getReview.mockResolvedValue(resultFixture())

      render(<ResultsView
          reviewId="rev-1"
          onReReview={vi.fn()}
          onStartOver={vi.fn()}
          onBackToHistory={vi.fn()}
        />)

      const group = await screen.findByRole('button', { name: /high severity/i })
      expect(group).toHaveAttribute('aria-expanded', 'false')
      // The count is on the header, so it is readable while shut.
      expect(group).toHaveTextContent('(1)')
      // No finding row exists yet, so nothing inside can be read or tabbed to.
      expect(
        screen.queryByRole('button', { name: /sec_encryption_at_rest/i }),
      ).not.toBeInTheDocument()
    })

    it('expands a group to collapsed findings, then a finding to its detail', async () => {
      getReview.mockResolvedValue(resultFixture())
      const user = userEvent.setup()

      render(<ResultsView
          reviewId="rev-1"
          onReReview={vi.fn()}
          onStartOver={vi.fn()}
          onBackToHistory={vi.fn()}
        />)

      await user.click(await screen.findByRole('button', { name: /high severity/i }))

      // Level one open: the finding is present, but only as a summary row —
      // its title, its status, and how much it touches.
      const row = screen.getByRole('button', { name: /sec_encryption_at_rest/i })
      expect(row).toHaveAttribute('aria-expanded', 'false')
      expect(row).toHaveTextContent('1 component')
      expect(row).not.toHaveTextContent(/the design names a dynamodb table/i)

      // Level two open: the observation text appears.
      await user.click(row)
      expect(row).toHaveAttribute('aria-expanded', 'true')
      expect(
        screen.getByText(/the design names a dynamodb table/i),
      ).toBeInTheDocument()
    })

    it('collapses again on a second click', async () => {
      getReview.mockResolvedValue(resultFixture())
      const user = userEvent.setup()

      render(<ResultsView
          reviewId="rev-1"
          onReReview={vi.fn()}
          onStartOver={vi.fn()}
          onBackToHistory={vi.fn()}
        />)

      const group = await screen.findByRole('button', { name: /high severity/i })
      await user.click(group)
      expect(
        screen.getByRole('button', { name: /sec_encryption_at_rest/i }),
      ).toBeInTheDocument()

      await user.click(group)
      expect(
        screen.queryByRole('button', { name: /sec_encryption_at_rest/i }),
      ).not.toBeInTheDocument()
    })
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

  describe('Copy fix-it prompt', () => {
    /** jsdom exposes `navigator.clipboard` as a getter-only property. */
    function stubClipboard(
      writeText = vi.fn((_text: string) => Promise.resolve()),
    ) {
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText },
        configurable: true,
      })
      return writeText
    }

    it('copies the assembled prompt for the open findings', async () => {
      getReview.mockResolvedValue(resultFixture())
      const user = userEvent.setup()
      // After setup(): userEvent installs its own clipboard stub and would
      // otherwise replace this one, leaving the assertion watching a dead mock.
      const writeText = stubClipboard()

      render(<ResultsView
          reviewId="rev-1"
          onReReview={vi.fn()}
          onStartOver={vi.fn()}
          onBackToHistory={vi.fn()}
        />)

      await user.click(
        await screen.findByRole('button', { name: /copy fix-it prompt/i }),
      )

      expect(writeText).toHaveBeenCalledTimes(1)
      const copied = String(writeText.mock.calls[0]?.[0])
      expect(copied).toContain('please revise the diagram to address each one')
      // Verbatim remediation from the fixture's one open high-severity finding.
      expect(copied).toContain('1. Enable SSE-KMS on the table with a customer-managed key.')
      // The passing finding must not appear.
      expect(copied).not.toContain('AI decisions are logged')
    })

    it('confirms the copy, then goes back to its resting label', async () => {
      getReview.mockResolvedValue(resultFixture())
      const user = userEvent.setup()
      stubClipboard()

      render(<ResultsView
          reviewId="rev-1"
          onReReview={vi.fn()}
          onStartOver={vi.fn()}
          onBackToHistory={vi.fn()}
        />)

      await user.click(
        await screen.findByRole('button', { name: /copy fix-it prompt/i }),
      )

      expect(await screen.findByRole('button', { name: /copied/i })).toBeInTheDocument()
    })

    it('says so when the clipboard refuses, instead of doing nothing visible', async () => {
      getReview.mockResolvedValue(resultFixture())
      // How it fails outside a secure context, or when permission is denied.
      const user = userEvent.setup()
      stubClipboard(vi.fn((_text: string) => Promise.reject(new Error('denied'))))

      render(<ResultsView
          reviewId="rev-1"
          onReReview={vi.fn()}
          onStartOver={vi.fn()}
          onBackToHistory={vi.fn()}
        />)

      await user.click(
        await screen.findByRole('button', { name: /copy fix-it prompt/i }),
      )

      expect(await screen.findByText(/could not copy to the clipboard/i)).toBeInTheDocument()
    })

    it('is absent when no finding would appear in the prompt', async () => {
      getReview.mockResolvedValue(
        resultFixture({
          findings: resultFixture().findings.map((f) => ({ ...f, status: 'pass' as const })),
        }),
      )

      render(<ResultsView
          reviewId="rev-1"
          onReReview={vi.fn()}
          onStartOver={vi.fn()}
          onBackToHistory={vi.fn()}
        />)

      await screen.findByRole('heading', { name: /payments platform/i })
      expect(
        screen.queryByRole('button', { name: /copy fix-it prompt/i }),
      ).not.toBeInTheDocument()
    })

    it('does not navigate away', async () => {
      getReview.mockResolvedValue(resultFixture())
      stubClipboard()
      const onReReview = vi.fn()
      const onStartOver = vi.fn()
      const onBackToHistory = vi.fn()
      const user = userEvent.setup()

      render(<ResultsView
          reviewId="rev-1"
          onReReview={onReReview}
          onStartOver={onStartOver}
          onBackToHistory={onBackToHistory}
        />)

      await user.click(
        await screen.findByRole('button', { name: /copy fix-it prompt/i }),
      )

      expect(onReReview).not.toHaveBeenCalled()
      expect(onStartOver).not.toHaveBeenCalled()
      expect(onBackToHistory).not.toHaveBeenCalled()
      expect(screen.getByRole('heading', { name: /payments platform/i })).toBeInTheDocument()
    })
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
