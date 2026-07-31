import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Finding, ReviewResult } from '../types'
import { resultFixture } from '../test/fixtures'
import { ResultsView } from './ResultsView'

const { getReview, downloadReport, reReview, uploadFile, getReviewVersions } =
  vi.hoisted(() => ({
    getReview: vi.fn(),
    downloadReport: vi.fn(),
    reReview: vi.fn(),
    uploadFile: vi.fn(),
    getReviewVersions: vi.fn(),
  }))

vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {},
  getReview,
  downloadReport,
  reReview,
  uploadFile,
  getReviewVersions,
}))

describe('ResultsView', () => {
  it('renders the score, pillars, and findings', async () => {
    getReview.mockResolvedValue(resultFixture())

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
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
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
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
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
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

  /**
   * The page is four sections in a fixed order: what it means, how it scored,
   * what to do, then the full record. This replaces two older tests that pinned
   * the previous order, in which a flat top-ten shortlist and a separate roadmap
   * both sat above the executive summary and listed the same work twice.
   */
  it('runs executive summary -> assessment -> roadmap -> detailed findings', async () => {
    getReview.mockResolvedValue(resultFixture())

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)

    await screen.findByTestId('executive-summary')
    const order = ['executive-summary', 'assessment', 'roadmap', 'detailed-findings']
      .map((id) => screen.getByTestId(id))

    for (let i = 0; i < order.length - 1; i += 1) {
      expect(
        order[i]!.compareDocumentPosition(order[i + 1]!) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy()
    }
  })

  it('has no separate flat top-action list any more', async () => {
    getReview.mockResolvedValue(resultFixture())

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)

    await screen.findByTestId('roadmap')
    // The roadmap is the single prioritized action view; a second list of the
    // same items under its own heading is what this restructure removed.
    expect(screen.queryByTestId('top-actions')).toBeNull()
    expect(screen.queryByText(/top action items/i)).toBeNull()
  })

  it('still surfaces the imperative remediation text, now inside the roadmap', async () => {
    getReview.mockResolvedValue(resultFixture())

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)

    const user = userEvent.setup()
    await screen.findByTestId('roadmap')
    await user.click(screen.getByRole('button', { name: /^Immediate/ }))

    expect(screen.getByTestId('roadmap')).toHaveTextContent(/enable sse-kms on the table/i)
  })

  it('omits the roadmap entirely when nothing is open', async () => {
    // Was written against the old flat shortlist, which no longer exists — so it
    // asserted the absence of something absent for everyone. Repointed at the
    // roadmap, which is what now has to hide itself on a clean review.
    getReview.mockResolvedValue(
      resultFixture({
        findings: resultFixture().findings.map((f) => ({ ...f, status: 'pass' as const })),
      }),
    )

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)

    await screen.findByRole('heading', { name: /payments platform/i })
    expect(screen.queryByTestId('roadmap')).not.toBeInTheDocument()
    expect(screen.queryByTestId('priority-focus')).not.toBeInTheDocument()
    // The audit trail still renders: a clean review is a result, not an absence.
    expect(screen.getByTestId('detailed-findings')).toBeInTheDocument()
  })

  describe('How to Improve roadmap', () => {
    /** Enough shape to exercise all three phases from one review. */
    const spread = () =>
      resultFixture({
        findings: [
          // low effort + high severity + one component -> Immediate
          {
            ...resultFixture().findings[0]!,
            check_id: 'sec_encryption_at_rest',
          },
          // medium effort -> Short-term
          {
            ...resultFixture().findings[0]!,
            check_id: 'ops_runbook',
            pillar_id: 'operational_excellence',
            severity: 'medium' as const,
            title: 'No runbook is referenced for the payment flow',
            remediation: 'Reference the on-call runbook in the design document.',
            remediation_effort: 'medium' as const,
            priority: 2,
          },
          // spans components -> Structural
          {
            ...resultFixture().findings[0]!,
            check_id: 'rel_multi_az',
            pillar_id: 'reliability',
            title: 'Single-AZ deployment for the order pipeline',
            remediation: 'Move the order pipeline to a multi-AZ deployment.',
            remediation_effort: 'high' as const,
            affected_components: ['orders-db', 'orders-worker', 'alb'],
            priority: 3,
          },
        ],
      })

    function mount(result = spread()) {
      getReview.mockResolvedValue(result)
      return render(<ResultsView
          reviewId="rev-1"
          onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
          onStartOver={vi.fn()}
          onBackToHistory={vi.fn()}
        />)
    }

    async function renderRoadmap(result = spread()) {
      mount(result)
      return screen.findByTestId('roadmap')
    }

    /** Open all three phases, then read the section back. */
    async function expandAll(user: ReturnType<typeof userEvent.setup>) {
      for (const label of [/^Immediate/, /^Short-term/, /^Structural/]) {
        const header = screen.getByRole('button', { name: label })
        if (!(header as HTMLButtonElement).disabled) await user.click(header)
      }
      return screen.getByTestId('roadmap').textContent
    }

    it('states what the section is for, in the reader\'s terms', async () => {
      await renderRoadmap()

      expect(screen.getByTestId('roadmap')).toHaveTextContent(
        /prioritized next actions, ordered by effort\. open findings only\./i,
      )
    })

    it('numbers the rows within each phase, restarting at 1', async () => {
      const user = userEvent.setup()
      await renderRoadmap()
      await expandAll(user)

      // Each phase in the fixture holds one action, so each starts its own count
      // rather than continuing a running total across the three.
      for (const phase of ['immediate', 'short_term', 'structural']) {
        const rows = within(screen.getByTestId(`phase-${phase}`)).getAllByRole('listitem')
        expect(rows[0]!.textContent).toMatch(/^1/)
      }
    })

    it('carries the ordinal visually without announcing it twice', async () => {
      const user = userEvent.setup()
      await renderRoadmap()
      await expandAll(user)

      const row = within(screen.getByTestId('phase-immediate')).getAllByRole('listitem')[0]!
      const ordinal = row.querySelector('[aria-hidden="true"].tnum')
      // The <ol> already conveys position to a screen reader; the digit is for
      // the eye, so it must not be read out a second time.
      expect(ordinal).not.toBeNull()
      expect(ordinal!.textContent).toBe('1')
    })

    it('sits below the assessment and above the detailed findings', async () => {
      const roadmap = await renderRoadmap()

      expect(
        screen.getByTestId('assessment').compareDocumentPosition(roadmap) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy()
      expect(
        roadmap.compareDocumentPosition(screen.getByTestId('detailed-findings')) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy()
    })

    it('starts every phase collapsed, showing only the counts', async () => {
      await renderRoadmap()

      for (const label of [/^Immediate/, /^Short-term/, /^Structural/]) {
        const header = screen.getByRole('button', { name: label })
        expect(header).toHaveAttribute('aria-expanded', 'false')
      }
      // The count is in the header, so it is legible while shut.
      expect(screen.getByTestId('phase-immediate')).toHaveTextContent('(1)')
      expect(screen.getByTestId('phase-short_term')).toHaveTextContent('(1)')
      expect(screen.getByTestId('phase-structural')).toHaveTextContent('(1)')

      // And no remediation text is on the page from the roadmap yet.
      expect(
        screen.getByTestId('roadmap').textContent,
      ).not.toMatch(/multi-AZ deployment/i)
    })

    it('expands one phase to the finding titles and verbatim remediation', async () => {
      const user = userEvent.setup()
      await renderRoadmap()

      await user.click(screen.getByRole('button', { name: /^Structural/ }))

      const phase = screen.getByTestId('phase-structural')
      expect(phase).toHaveTextContent('Single-AZ deployment for the order pipeline')
      // Verbatim — the exact string from the finding, not a rephrase.
      expect(phase).toHaveTextContent('Move the order pipeline to a multi-AZ deployment.')
      // Context a reviewer sequencing work needs.
      expect(phase).toHaveTextContent(/high effort/)
      expect(phase).toHaveTextContent(/3 components/)
    })

    it('expands independently — opening one phase does not open the others', async () => {
      const user = userEvent.setup()
      await renderRoadmap()

      await user.click(screen.getByRole('button', { name: /^Immediate/ }))

      expect(screen.getByRole('button', { name: /^Immediate/ })).toHaveAttribute(
        'aria-expanded',
        'true',
      )
      expect(screen.getByRole('button', { name: /^Structural/ })).toHaveAttribute(
        'aria-expanded',
        'false',
      )
    })

    it('places each open finding in exactly one phase', async () => {
      const user = userEvent.setup()
      await renderRoadmap()
      await expandAll(user)

      // Three findings in, three rows out across all phases — nothing duplicated
      // into two phases and nothing dropped.
      const rows = screen.getByTestId('roadmap').querySelectorAll('li')
      expect(rows).toHaveLength(3)
    })

    it('shows an empty phase rather than hiding it, and does not expand it', async () => {
      // "Structural (0)" is a result: it says there is no architecture work. An
      // absent heading would leave the reader unsure it was considered.
      const user = userEvent.setup()
      await renderRoadmap(
        resultFixture({ findings: [resultFixture().findings[0]!] }),
      )

      const structural = screen.getByRole('button', { name: /^Structural/ })
      expect(screen.getByTestId('phase-structural')).toHaveTextContent('(0)')
      expect(structural).toBeDisabled()

      await user.click(structural)
      expect(structural).toHaveAttribute('aria-expanded', 'false')
    })

    it('is absent entirely when nothing is open', async () => {
      mount(
        resultFixture({
          findings: resultFixture().findings.map((f) => ({ ...f, status: 'pass' as const })),
        }),
      )

      await screen.findByRole('heading', { name: /payments platform/i })
      expect(screen.queryByTestId('roadmap')).not.toBeInTheDocument()
    })

    it('groups the same review identically however the findings are ordered', async () => {
      // The determinism guarantee, seen from the UI rather than the pure function:
      // two fetches of one review may return its findings in any order, and the
      // phases must read the same both times.
      const user = userEvent.setup()
      const result = spread()

      const first = mount(result)
      await screen.findByTestId('roadmap')
      const before = await expandAll(user)
      first.unmount()

      mount({ ...result, findings: [...result.findings].reverse() })
      await screen.findByTestId('roadmap')
      const after = await expandAll(user)

      expect(after).toBe(before)
    })
  })

  describe('findings accordion', () => {
    it('starts every severity group collapsed', async () => {
      getReview.mockResolvedValue(resultFixture())

      render(<ResultsView
          reviewId="rev-1"
          onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
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
          onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
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
          onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
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
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
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
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
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
          onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
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
          onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
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
          onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
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
          onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
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
          onReReview={onReReview} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
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
          onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
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
          onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
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
          onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
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

/**
 * The two sections show overlapping data on purpose. What keeps them from
 * reading as two competing to-do lists is that each says what it is.
 */
describe('ResultsView — roadmap vs detailed findings', () => {
  function mountFull() {
    getReview.mockResolvedValue(resultFixture())
    return render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)
  }

  it('tells the reader the findings section is the record, not a second action list', async () => {
    mountFull()

    const findings = await screen.findByTestId('detailed-findings')
    expect(findings).toHaveTextContent(
      /complete evaluation record, including passed and not-applicable checks/i,
    )
    expect(findings).toHaveTextContent(/remediation is repeated here for reference/i)
    expect(findings).toHaveTextContent(/action roadmap above is the prioritized list/i)
  })

  it('gives each section a distinct purpose line', async () => {
    mountFull()

    await screen.findByTestId('roadmap')
    const roadmap = screen.getByTestId('roadmap').textContent ?? ''
    const findings = screen.getByTestId('detailed-findings').textContent ?? ''

    expect(roadmap).toMatch(/prioritized next actions/i)
    expect(findings).toMatch(/complete evaluation record/i)
    // Neither borrows the other's framing.
    expect(roadmap).not.toMatch(/complete evaluation record/i)
    expect(findings).not.toMatch(/prioritized next actions/i)
  })
})

/**
 * Pillar "Explain more".
 *
 * A regroup of data the review already carries, not a synthesis: `evidence` is a
 * required field on every finding the evaluate stage returns, so the reasoning
 * behind a pillar score already exists and only needs collecting. These tests
 * pin that it stays a regroup — no request, and nothing invented for a check
 * whose evidence came back empty.
 */
describe('ResultsView — pillar explain', () => {
  const pillarFindings = () => {
    const base = resultFixture().findings[0]!
    return [
      { ...base, check_id: 'sec_a', pillar_id: 'security', framework: 'aws_waf',
        status: 'fail' as const, title: 'No encryption at rest',
        evidence: 'The orders table is described without any encryption setting.' },
      { ...base, check_id: 'sec_b', pillar_id: 'security', framework: 'aws_waf',
        status: 'pass' as const, title: 'TLS in transit',
        evidence: 'All edges are labelled HTTPS.' },
      // Same pillar id, other framework — must not leak into the AWS pillar.
      { ...base, check_id: 't7_shadow', pillar_id: 'security', framework: 'trust7',
        status: 'fail' as const, title: 'TRUST-7 only check',
        evidence: 'Belongs to the other framework.' },
      // A real TRUST-7 pillar, so the control is exercised on both frameworks.
      { ...base, check_id: 't7_ai', pillar_id: 'ai_governance', framework: 'trust7',
        status: 'fail' as const, title: 'No model governance stated',
        evidence: 'The design names a model but no oversight process.' },
    ]
  }

  function mountPillars() {
    getReview.mockResolvedValue(resultFixture({ findings: pillarFindings() }))
    return render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)
  }

  it('starts collapsed and makes no request to expand', async () => {
    mountPillars()
    const user = userEvent.setup()

    await screen.findByTestId('assessment')
    const toggle = screen.getAllByRole('button', { name: /explain more/i })[0]!
    expect(toggle).toHaveAttribute('aria-expanded', 'false')

    getReview.mockClear()
    await user.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    // The reasoning is already in the review; expanding must not fetch anything.
    expect(getReview).not.toHaveBeenCalled()
  })

  it('shows a verdict and a line of reasoning for every check in the pillar', async () => {
    mountPillars()
    const user = userEvent.setup()

    await screen.findByTestId('assessment')
    await user.click(screen.getAllByRole('button', { name: /explain more/i })[0]!)

    const panel = screen.getByTestId('pillar-explain-aws_waf-security')
    expect(panel).toHaveTextContent('No encryption at rest')
    expect(panel).toHaveTextContent(/orders table is described without any encryption/i)
    expect(panel).toHaveTextContent('TLS in transit')
    expect(panel).toHaveTextContent(/all edges are labelled https/i)
  })

  it('includes passing and not-applicable checks, since they explain the score', async () => {
    mountPillars()
    const user = userEvent.setup()

    await screen.findByTestId('assessment')
    await user.click(screen.getAllByRole('button', { name: /explain more/i })[0]!)

    const panel = screen.getByTestId('pillar-explain-aws_waf-security')
    expect(within(panel).getByText('Met')).toBeInTheDocument()
    expect(within(panel).getByText('Not met')).toBeInTheDocument()
  })

  it('does not mix a pillar id shared across the two frameworks', async () => {
    mountPillars()
    const user = userEvent.setup()

    await screen.findByTestId('assessment')
    await user.click(screen.getAllByRole('button', { name: /explain more/i })[0]!)

    const panel = screen.getByTestId('pillar-explain-aws_waf-security')
    expect(panel).not.toHaveTextContent('TRUST-7 only check')
  })

  it('says so rather than inventing text when a check has no evidence', async () => {
    const base = resultFixture().findings[0]!
    getReview.mockResolvedValue(
      resultFixture({
        findings: [
          { ...base, check_id: 'sec_a', pillar_id: 'security', framework: 'aws_waf',
            status: 'fail' as const, title: 'Nothing recorded', evidence: '' },
        ],
      }),
    )
    const user = userEvent.setup()
    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)

    await screen.findByTestId('assessment')
    await user.click(screen.getAllByRole('button', { name: /explain more/i })[0]!)

    expect(screen.getByTestId('pillar-explain-aws_waf-security')).toHaveTextContent(
      /no reasoning was recorded for this check/i,
    )
  })

  it('explains what the number is, on the info icon, without expanding', async () => {
    mountPillars()
    const user = userEvent.setup()

    await screen.findByTestId('assessment')
    await user.click(
      screen.getAllByRole('button', { name: /how the security score was reached/i })[0]!,
    )

    const tip = screen.getByRole('tooltip')
    expect(tip).toHaveTextContent(/weighted by severity/i)
    expect(tip).toHaveTextContent(/partial verdict earns half credit/i)
    expect(tip).toHaveTextContent(/not-applicable checks are left out/i)
  })

  it('offers the control on both frameworks, not just the first', async () => {
    mountPillars()

    await screen.findByTestId('assessment')
    // One pillar carries findings in each framework, so the control must appear
    // in both blocks — it is not an AWS-only affordance.
    const controls = screen.getAllByRole('button', { name: /explain more/i })
    expect(controls).toHaveLength(2)

    const user = userEvent.setup()
    await user.click(controls[1]!)
    expect(
      screen.getByTestId('pillar-explain-trust7-ai_governance'),
    ).toHaveTextContent(/no model governance stated/i)
  })
})

describe('ResultsView — copy fix-it prompt affordance', () => {
  function mountWithActions() {
    getReview.mockResolvedValue(resultFixture())
    return render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)
  }

  it('explains in one line what lands on the clipboard', async () => {
    mountWithActions()

    const tip = await screen.findByTestId('fix-it-tooltip')
    expect(tip).toHaveTextContent(/ready-to-paste prompt/i)
    expect(tip).toHaveTextContent(/revise your architecture diagram/i)
  })

  /**
   * The description is supplementary, not a restatement of the label, so it must
   * reach a screen reader rather than being hidden the way the mic tooltip is.
   */
  it('is wired to the button with aria-describedby', async () => {
    mountWithActions()

    const button = await screen.findByRole('button', { name: /copy fix-it prompt/i })
    const tip = screen.getByTestId('fix-it-tooltip')
    expect(button).toHaveAttribute('aria-describedby', tip.id)
    expect(tip.id).not.toBe('')
  })

  it('reveals on keyboard focus, not only on hover', async () => {
    mountWithActions()

    await screen.findByRole('button', { name: /copy fix-it prompt/i })
    const tip = screen.getByTestId('fix-it-tooltip')
    // Opacity is driven by group-hover AND group-focus-within, so tabbing to the
    // button shows the same explanation a mouse user gets.
    expect(tip.className).toContain('group-focus-within:opacity-100')
    expect(tip.className).toContain('group-hover:opacity-100')
  })

  it('names no assistant, matching the prompt it describes', async () => {
    mountWithActions()

    const tip = await screen.findByTestId('fix-it-tooltip')
    for (const vendor of ['claude', 'chatgpt', 'gemini', 'copilot', 'anthropic', 'openai']) {
      expect(tip.textContent?.toLowerCase()).not.toContain(vendor)
    }
  })

  it('does not add a second element competing for the tooltip role', async () => {
    mountWithActions()
    const user = userEvent.setup()

    await screen.findByTestId('fix-it-tooltip')
    // Nothing is open yet, so no tooltip role should exist at all.
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /what the maturity tiers mean/i }))
    // Exactly the one that was opened.
    expect(screen.getByRole('tooltip')).toHaveTextContent(/maturity tiers/i)
  })
})

/**
 * Part 5: the prompts now ask for bullets where scanning beats prose, so the
 * page has to render them as lists rather than printing the markers.
 */
describe('ResultsView — structured copy', () => {
  it('renders a bulleted assessment as a list, not a dashed paragraph', async () => {
    getReview.mockResolvedValue(
      resultFixture({
        summary:
          '- Security is the weakest pillar at 48.\n' +
          '- Two high-severity gaps block deployment.\n' +
          '- Cost optimization is the strongest at 81.',
      }),
    )

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)

    const assessment = await screen.findByTestId('assessment')
    // The "Fix these first" callout lives in this section too and is its own
    // list; remove it so this counts the assessment's own bullets.
    screen.getByTestId('priority-focus').remove()
    expect(within(assessment).getAllByRole('listitem')).toHaveLength(3)
    expect(assessment.textContent).not.toContain('- Security')
  })

  it('leaves a prose assessment as a paragraph, so older reviews are unaffected', async () => {
    getReview.mockResolvedValue(
      resultFixture({ summary: 'Solid shape, with encryption and audit gaps to close.' }),
    )

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)

    const assessment = await screen.findByTestId('assessment')
    screen.getByTestId('priority-focus').remove()
    expect(within(assessment).queryAllByRole('listitem')).toHaveLength(0)
    expect(assessment).toHaveTextContent(/solid shape, with encryption/i)
  })

  it('renders multi-step remediation as steps in the roadmap', async () => {
    const base = resultFixture().findings[0]!
    getReview.mockResolvedValue(
      resultFixture({
        findings: [
          {
            ...base,
            remediation:
              '- Create a customer-managed KMS key.\n' +
              '- Enable SSE-KMS on the orders table.\n' +
              '- Re-encrypt existing snapshots.',
          },
        ],
      }),
    )
    const user = userEvent.setup()

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)

    await screen.findByTestId('roadmap')
    await user.click(screen.getByRole('button', { name: /^Immediate/ }))

    const roadmap = screen.getByTestId('roadmap')
    expect(within(roadmap).getAllByRole('listitem').length).toBeGreaterThanOrEqual(3)
    expect(roadmap.textContent).toContain('Re-encrypt existing snapshots.')
    expect(roadmap.textContent).not.toContain('- Create a customer-managed')
  })

  /** The executive summary stays prose on purpose — it is a summary, not a list. */
  it('keeps the executive summary as prose', async () => {
    getReview.mockResolvedValue(resultFixture())

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)

    const exec = await screen.findByTestId('executive-summary')
    expect(within(exec).queryAllByRole('listitem')).toHaveLength(0)
    expect(exec.querySelector('p')).not.toBeNull()
  })
})

describe('ResultsView — priority focus callout', () => {
  const spreadFindings = () => {
    const base = resultFixture().findings[0]!
    return [
      { ...base, check_id: 'sec_a', pillar_id: 'security',
        title: 'No encryption at rest', remediation_effort: 'low' as const },
      { ...base, check_id: 'ops_a', pillar_id: 'operational_excellence',
        title: 'No runbook referenced', severity: 'medium' as const,
        remediation_effort: 'medium' as const },
      { ...base, check_id: 'rel_a', pillar_id: 'reliability',
        title: 'Single-AZ deployment', remediation_effort: 'high' as const,
        affected_components: ['a', 'b'] },
    ]
  }

  function mountFocus(findings: Finding[] = spreadFindings()) {
    getReview.mockResolvedValue(resultFixture({ findings }))
    return render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)
  }

  it('sits inside the assessment, above the pillar heatmaps', async () => {
    mountFocus()

    const focus = await screen.findByTestId('priority-focus')
    const assessment = screen.getByTestId('assessment')
    expect(assessment.contains(focus)).toBe(true)
    // Before the heatmaps: it is what to do about the scores, so it reads first.
    expect(
      focus.compareDocumentPosition(screen.getByText('AWS Well-Architected Framework')) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it('names the urgent gaps, numbered, with pillar severity and phase', async () => {
    mountFocus()

    const focus = await screen.findByTestId('priority-focus')
    expect(focus).toHaveTextContent(/fix these first/i)
    expect(focus).toHaveTextContent('No encryption at rest')
    expect(focus).toHaveTextContent(/security · high severity · Immediate/i)
    expect(within(focus).getAllByRole('listitem').length).toBeGreaterThanOrEqual(1)
  })

  it('shows at most five, so it stays a glance not a second roadmap', async () => {
    const base = resultFixture().findings[0]!
    mountFocus(
      Array.from({ length: 9 }, (_, i) => ({
        ...base, check_id: `c${i}`, pillar_id: `p${i}`, title: `Gap ${i}`,
        remediation_effort: 'low' as const,
      })),
    )

    const focus = await screen.findByTestId('priority-focus')
    expect(within(focus).getAllByRole('listitem')).toHaveLength(5)
  })

  it('hides itself entirely when nothing is open', async () => {
    mountFocus(
      resultFixture().findings.map((f) => ({ ...f, status: 'pass' as const })),
    )

    await screen.findByTestId('assessment')
    expect(screen.queryByTestId('priority-focus')).not.toBeInTheDocument()
  })

  it('adds no numbers of its own — every item is a finding already on the page', async () => {
    mountFocus()
    const user = userEvent.setup()

    const focus = await screen.findByTestId('priority-focus')
    // The roadmap starts collapsed, so its rows are not in the DOM until opened.
    for (const label of [/^Immediate/, /^Short-term/, /^Structural/]) {
      const header = screen.getByRole('button', { name: label })
      if (!(header as HTMLButtonElement).disabled) await user.click(header)
    }
    const roadmap = screen.getByTestId('roadmap')
    // Whatever the callout names must also appear in the roadmap below; it is a
    // curated surface of the same data, not a separate judgement.
    for (const row of within(focus).getAllByRole('listitem')) {
      const title = row.querySelector('.font-medium')!.textContent!
      expect(roadmap.textContent).toContain(title)
    }
  })
})

/**
 * Genuine model output from a live run — the dense-copy round was blocked on
 * verifying against real findings rather than a synthetic example. Kept verbatim.
 */
describe('ResultsView — real findings from a live run', () => {
  const REMEDIATION_TLS =
    'Reconfigure the ALB-to-API target group to use HTTPS on port 443 with a TLS ' +
    'certificate on the EC2 instance... Enable RDS SSL/TLS by setting...'
  const EVIDENCE_TLS =
    "Data flow from ALB to API is explicitly listed as 'HTTP (internal)' (not " +
    'HTTPS)... the design does not establish TLS/SSL for ALB->API, API->DB...'

  it('renders multi-step prose as a paragraph, not mis-split into fragments', async () => {
    const base = resultFixture().findings[0]!
    getReview.mockResolvedValue(
      resultFixture({
        findings: [
          { ...base, check_id: 'sec_encryption_transit', pillar_id: 'security',
            title: 'No TLS on internal data flows', evidence: EVIDENCE_TLS,
            remediation: REMEDIATION_TLS, remediation_effort: 'low' as const },
        ],
      }),
    )
    const user = userEvent.setup()

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)
    await screen.findByTestId('roadmap')
    await user.click(screen.getByRole('button', { name: /^Immediate/ }))

    const row = within(screen.getByTestId('roadmap')).getAllByRole('listitem')[0]!
    // One paragraph carrying the whole remediation, not a list of fragments.
    expect(row.querySelector('ul')).toBeNull()
    expect(row.querySelector('ol')).toBeNull()
    expect(row.textContent).toContain(REMEDIATION_TLS)
  })

  it('does not mangle the arrow and quotes in real evidence', async () => {
    const base = resultFixture().findings[0]!
    getReview.mockResolvedValue(
      resultFixture({
        findings: [
          { ...base, check_id: 'sec_encryption_transit', pillar_id: 'security',
            title: 'No TLS on internal data flows', evidence: EVIDENCE_TLS,
            remediation: REMEDIATION_TLS },
        ],
      }),
    )
    const user = userEvent.setup()

    render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)
    await screen.findByTestId('detailed-findings')
    await user.click(screen.getByRole('button', { name: /high severity/i }))
    await user.click(screen.getByRole('button', { name: /sec_encryption_transit/i }))

    expect(screen.getByTestId('detailed-findings').textContent).toContain('ALB->API')
  })
})

describe('ResultsView — use-case notes', () => {
  const note = {
    component: 'Claims lookup store',
    recommendation:
      'A read replica in front of RDS fits better than scaling the primary.',
    grounded_in: 'roughly 95% of traffic is agents looking up existing claims',
  }

  function mountNotes(over: Partial<ReviewResult>) {
    getReview.mockResolvedValue(resultFixture(over))
    return render(<ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />)
  }

  it('is absent when no context was supplied', async () => {
    mountNotes({ context: '', use_case_notes: [note] })

    await screen.findByTestId('detailed-findings')
    expect(screen.queryByTestId('use-case-notes')).not.toBeInTheDocument()
  })

  it('is absent when context was supplied but nothing could be grounded', async () => {
    mountNotes({ context: 'A read-heavy internal portal.', use_case_notes: [] })

    await screen.findByTestId('detailed-findings')
    expect(screen.queryByTestId('use-case-notes')).not.toBeInTheDocument()
  })

  it('renders the trade-off and the quote it rests on', async () => {
    mountNotes({ context: 'A read-heavy internal portal.', use_case_notes: [note] })

    const section = await screen.findByTestId('use-case-notes')
    expect(section).toHaveTextContent('Claims lookup store')
    expect(section).toHaveTextContent(/read replica in front of RDS/i)
    expect(section).toHaveTextContent(/based on what you wrote/i)
    expect(section).toHaveTextContent(/95% of traffic/i)
  })

  it('sits after the detailed findings, being advisory rather than assessed', async () => {
    mountNotes({ context: 'A read-heavy internal portal.', use_case_notes: [note] })

    const section = await screen.findByTestId('use-case-notes')
    expect(
      screen.getByTestId('detailed-findings').compareDocumentPosition(section) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  describe('extraction warnings on a stored review', () => {
    const nearEmpty = {
      code: 'diagram_near_empty' as const,
      message:
        'Almost nothing could be read from the uploaded diagram — 1 component was ' +
        'extracted from an image of 488 KB.',
      detail: 'screenshot.png: 500000 bytes, 1 components',
    }

    it('shows the warning above the score and the executive summary', async () => {
      // Order is the point. A warning says the review may have been scored on a
      // fraction of the design; a reader who meets the score and the summary first
      // has already formed a view by the time they reach the caveat.
      getReview.mockResolvedValue(resultFixture({ warnings: [nearEmpty] }))

      render(<ResultsView reviewId="rev-1" onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()} onStartOver={vi.fn()} onBackToHistory={vi.fn()} />)

      const panel = await screen.findByTestId('ingest-warnings')
      const summary = screen.getByTestId('executive-summary')
      expect(panel.compareDocumentPosition(summary)).toBe(
        Node.DOCUMENT_POSITION_FOLLOWING,
      )
    })

    it('still renders the full review — a warning is not a failure', async () => {
      getReview.mockResolvedValue(resultFixture({ warnings: [nearEmpty] }))

      render(<ResultsView reviewId="rev-1" onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()} onStartOver={vi.fn()} onBackToHistory={vi.fn()} />)

      expect(await screen.findByTestId('ingest-warnings')).toBeInTheDocument()
      // The score and the summary are both still there — 62.5 appears in more than
      // one place on this page, so the assertion targets the headline figure.
      expect(screen.getByTestId('executive-summary')).toBeInTheDocument()
      expect(screen.getByText('/100')).toBeInTheDocument()
    })

    it('shows no panel on a clean review', async () => {
      getReview.mockResolvedValue(resultFixture())

      render(<ResultsView reviewId="rev-1" onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()} onStartOver={vi.fn()} onBackToHistory={vi.fn()} />)
      await screen.findByTestId('executive-summary')

      expect(screen.queryByTestId('ingest-warnings')).toBeNull()
    })

    it('survives an older stored review that has no warnings field at all', async () => {
      // Reviews written before this field existed load with it absent, and the view
      // reads `result.warnings ?? []` for exactly that reason.
      const { warnings: _dropped, ...older } = resultFixture()
      getReview.mockResolvedValue(older)

      render(<ResultsView reviewId="rev-1" onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()} onStartOver={vi.fn()} onBackToHistory={vi.fn()} />)

      expect(await screen.findByTestId('executive-summary')).toBeInTheDocument()
      expect(screen.queryByTestId('ingest-warnings')).toBeNull()
    })
  })

})

describe('ResultsView — a not-applicable pillar explains itself', () => {
  /**
   * The silent not-applicable was the whole problem. A pillar whose checks all turn
   * on there being an AI/ML component used to render the bare string "Not applicable
   * to this design" — a conclusion worth nineteen of the forty-five checks, with no
   * argument attached and no way for a reviewer or a judge to contest it.
   */
  const skippedPillar = (overrides = {}) =>
    resultFixture({
      frameworks: [
        {
          framework: 'trust7',
          framework_name: 'Minfy TRUST-7 Framework',
          score: 0,
          pillars: [
            {
              framework: 'trust7',
              pillar_id: 'trust_foundations',
              pillar_name: 'Trust foundations',
              score: 0,
              checks_total: 4,
              checks_evaluated: 0,
              checks_passed: 0,
            },
          ],
        },
      ],
      ...overrides,
    })

  const ABSENT_RECORD = {
    signals: [],
    patterns_checked: 96,
    components_seen: ['Expense API', 'Receipts bucket'],
    verdict: 'absent' as const,
    rationale:
      'No AI/ML component detected. 96 AI/ML patterns were checked against 2 ' +
      'components: Expense API, Receipts bucket.',
  }

  function mount(result: unknown) {
    getReview.mockResolvedValue(result)
    return render(
      <ResultsView reviewId="rev-1" onReReview={vi.fn()} onFollowUpStarted={vi.fn()} onOpenVersion={vi.fn()} onStartOver={vi.fn()} onBackToHistory={vi.fn()} />,
    )
  }

  it('gives the reason and the components searched, not just the conclusion', async () => {
    mount(skippedPillar({ ai_detection: ABSENT_RECORD }))
    const caption = await screen.findByTestId('pillar-caption-trust_foundations')

    expect(caption).toHaveTextContent('Not applicable to this design')
    // The argument. Without these two the sentence is the old bare string.
    expect(caption).toHaveTextContent('96 AI/ML patterns')
    expect(caption).toHaveTextContent('Expense API')
  })

  it('flags a not-applicable that the evidence contradicts', async () => {
    // The case the round exists for: AI evidence present, AI pillar scored n/a.
    mount(
      skippedPillar({
        ai_detection: {
          signals: [
            {
              tier: 'implicit_function' as const,
              signal: 'personalisation',
              source: 'diagram component “Personalization Service”',
              excerpt: 'Personalization Service',
            },
          ],
          patterns_checked: 96,
          components_seen: ['Personalization Service', 'API'],
          verdict: 'likely' as const,
          rationale:
            'AI/ML component likely but never labelled as one. Suggestive evidence: ' +
            'personalisation. A capability like this is usually model-backed, but ' +
            'could be implemented as rules.',
        },
      }),
    )

    const caption = await screen.findByTestId('pillar-caption-trust_foundations')
    expect(caption).toHaveTextContent(/worth checking/i)

    // And the panel at the top says the same thing, cautions, and states that
    // nothing was changed on the strength of a keyword match.
    const panel = screen.getByTestId('ai-detection-panel')
    expect(panel).toHaveAttribute('data-tone', 'caution')
    expect(panel).toHaveTextContent(/Nothing has been changed automatically/)
  })

  it('does not flag a correctly-empty design', async () => {
    mount(skippedPillar({ ai_detection: ABSENT_RECORD }))
    const caption = await screen.findByTestId('pillar-caption-trust_foundations')

    expect(caption).not.toHaveTextContent(/worth checking/i)
    expect(screen.getByTestId('ai-detection-panel')).toHaveAttribute('data-tone', 'neutral')
  })

  it('never claims no AI was found on a review stored before detection existed', async () => {
    // `ai_detection` absent entirely. Saying "no AI/ML component detected" here would
    // put a claim in front of a reviewer that nothing in the system established.
    const { ai_detection: _dropped, ...older } = skippedPillar({
      ai_detection: ABSENT_RECORD,
    })
    mount(older)

    const caption = await screen.findByTestId('pillar-caption-trust_foundations')
    expect(caption).toHaveTextContent(/no detection record was stored/)
    expect(caption).not.toHaveTextContent(/patterns/)
    // No record, so nothing to render a panel from.
    expect(screen.queryByTestId('ai-detection')).toBeNull()
  })

  it('leaves an evaluated pillar reading as it always did', async () => {
    mount(resultFixture({ ai_detection: ABSENT_RECORD }))
    await screen.findByTestId('assessment')

    const caption = screen.getByTestId('pillar-caption-security')
    expect(caption).toHaveTextContent('3/7 passed')
    expect(caption).not.toHaveTextContent(/Not applicable/)
    expect(caption).not.toHaveTextContent(/patterns/)
  })
})


// --------------------------------------------------------------------------- #
// Follow-up round, from the results page
// --------------------------------------------------------------------------- #

describe('ResultsView — following up on a completed review', () => {
  function mount(result: unknown) {
    getReview.mockResolvedValue(result)
    const props = {
      onReReview: vi.fn(),
      onFollowUpStarted: vi.fn(),
      onOpenVersion: vi.fn(),
      onStartOver: vi.fn(),
      onBackToHistory: vi.fn(),
    }
    render(<ResultsView reviewId="rev-1" {...props} />)
    return props
  }

  beforeEach(() => {
    reReview.mockReset()
    uploadFile.mockReset()
    getReviewVersions.mockReset()
    getReviewVersions.mockResolvedValue({
      root_review_id: 'rev-1',
      latest_review_id: 'rev-2',
      versions: [],
    })
    reReview.mockResolvedValue({
      review_id: 'rev-2',
      status_url: '/reviews/rev-2/status',
      result_url: '/reviews/rev-2',
    })
  })

  it('shows the feedback box on a completed review, near the top', async () => {
    mount(resultFixture())
    const box = await screen.findByTestId('feedback-box')

    // Above all five content sections. Reaching this view at all means the review
    // is complete: GET /reviews/{id} answers from the stored result, which only a
    // finished run writes, so there is no state where this renders early.
    for (const id of ['executive-summary', 'assessment', 'roadmap', 'detailed-findings']) {
      expect(
        box.compareDocumentPosition(screen.getByTestId(id)) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy()
    }
  })

  it('is absent while the review is still loading', () => {
    // The one pre-complete state this view has.
    getReview.mockReturnValue(new Promise(() => {}))
    render(
      <ResultsView
        reviewId="rev-1"
        onReReview={vi.fn()}
        onFollowUpStarted={vi.fn()}
        onOpenVersion={vi.fn()}
        onStartOver={vi.fn()}
        onBackToHistory={vi.fn()}
      />,
    )
    expect(screen.queryByTestId('feedback-box')).toBeNull()
  })

  it('posts the feedback for the review being viewed and hands the new id up', async () => {
    const user = userEvent.setup()
    const props = mount(resultFixture())

    await screen.findByTestId('feedback-box')
    await user.type(
      screen.getByLabelText(/What this review got wrong/i),
      'Encryption is specified in section 4.',
    )
    await user.click(screen.getByRole('button', { name: /Re-review with this feedback/i }))

    await waitFor(() =>
      expect(reReview).toHaveBeenCalledWith(
        'rev-1',
        expect.objectContaining({ feedback: 'Encryption is specified in section 4.' }),
      ),
    )
    // Already accepted and running, so the caller polls it rather than routing
    // back to the upload step — that is what separates this from onReReview.
    expect(props.onFollowUpStarted).toHaveBeenCalledWith('rev-2', expect.any(Number))
    expect(props.onReReview).not.toHaveBeenCalled()
  })

  it('marks a follow-up version and links back, with the delta the API returned', async () => {
    getReviewVersions.mockResolvedValue({
      root_review_id: 'rev-1',
      latest_review_id: 'rev-2',
      versions: [
        {
          review_id: 'rev-1', version: 1, created_at: '2026-07-31T10:00:00Z',
          overall_score: 62.5, open_findings: 4, feedback: '',
          based_on_review_id: '', is_original: true,
        },
        {
          review_id: 'rev-2', version: 2, created_at: '2026-07-31T10:40:00Z',
          overall_score: 71.0, open_findings: 2,
          feedback: 'The orders table IS encrypted.',
          based_on_review_id: 'rev-1', is_original: false,
        },
      ],
    })
    const user = userEvent.setup()
    const props = mount(
      resultFixture({
        review_id: 'rev-2',
        version: 2,
        root_review_id: 'rev-1',
        based_on_review_id: 'rev-1',
        feedback: 'The orders table IS encrypted.',
        overall_score: 71.0,
        delta: {
          previous_review_id: 'rev-1',
          previous_overall_score: 62.5,
          current_overall_score: 71.0,
          change: 8.5,
          pillars: [],
          resolved_checks: ['sec_encryption_at_rest'],
          new_checks: [],
          unchanged_failures: [],
        },
      }),
    )

    const banner = await screen.findByTestId('version-banner')
    expect(banner).toHaveTextContent(/version\s*2/)
    expect(screen.getByTestId('version-feedback')).toHaveTextContent(
      'The orders table IS encrypted.',
    )

    // The delta the API already returns, rendered by the component that always
    // rendered it — no new score arithmetic anywhere in this round.
    expect(screen.getByText(/Change since the previous review/i)).toBeInTheDocument()
    expect(screen.getByText('62.5 → 71.0')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Open the previous version/i }))
    expect(props.onOpenVersion).toHaveBeenCalledWith('rev-1')
  })

  it('shows no version banner on an original review', async () => {
    mount(resultFixture())
    await screen.findByTestId('feedback-box')
    expect(screen.queryByTestId('version-banner')).toBeNull()
  })

  it('keeps the separate re-analyze action, renamed so the two do not read alike', async () => {
    const user = userEvent.setup()
    const props = mount(resultFixture())

    await screen.findByTestId('feedback-box')
    await user.click(
      screen.getByRole('button', { name: /Score a different design against this one/i }),
    )
    expect(props.onReReview).toHaveBeenCalled()
    expect(reReview).not.toHaveBeenCalled()
  })
})
