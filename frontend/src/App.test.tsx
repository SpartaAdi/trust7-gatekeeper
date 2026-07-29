import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getSharedReview, listReviews, readShareParams } from './api'
import App from './App'
import { clearToken, setToken } from './token'

vi.mock('./api', () => ({
  ApiError: class ApiError extends Error {},
  listReviews: vi.fn(),
  uploadFile: vi.fn(),
  submitReview: vi.fn(),
  getStatus: vi.fn(),
  getReview: vi.fn(),
  createShareLink: vi.fn(),
  shareUrl: vi.fn(),
  getSharedReview: vi.fn(),
  // Defaults to "this is not a shared link", which is every test below except
  // the share-entry ones.
  readShareParams: vi.fn(() => null),
}))

describe('App', () => {
  // The demo gate stands in front of everything, so these tests start past it.
  // Mocks are cleared too: the gate test asserts nothing was fetched, which is
  // only meaningful if earlier tests' calls are not still counted.
  beforeEach(() => {
    vi.clearAllMocks()
    setToken('demo-token')
  })

  it('renders without throwing', () => {
    vi.mocked(listReviews).mockResolvedValue([])
    expect(() => render(<App />)).not.toThrow()
  })

  it('opens on the review history, not the upload form', async () => {
    vi.mocked(listReviews).mockResolvedValue([])
    render(<App />)

    expect(
      await screen.findByRole('heading', { name: /^reviews$/i }),
    ).toBeInTheDocument()
  })

  it('hides the step tracker on the landing page', async () => {
    vi.mocked(listReviews).mockResolvedValue([])
    render(<App />)
    await screen.findByRole('heading', { name: /^reviews$/i })

    // The tracker describes the review flow; there is no step on the landing page.
    expect(screen.queryByRole('navigation', { name: /progress/i })).toBeNull()
  })

  it('shows the step tracker once a new review starts', async () => {
    vi.mocked(listReviews).mockResolvedValue([])
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('button', { name: /new review/i }))

    const tracker = screen.getByRole('navigation', { name: /progress/i })
    expect(tracker).toHaveTextContent('Upload')
    expect(tracker).toHaveTextContent('Analyzing')
    expect(tracker).toHaveTextContent('Results')
    expect(
      screen.getByRole('heading', { name: /submit a design for review/i }),
    ).toBeInTheDocument()
  })

  describe('demo gate', () => {
    it('asks for the token before showing anything else', async () => {
      clearToken()
      vi.mocked(listReviews).mockResolvedValue([])

      render(<App />)

      expect(screen.getByLabelText(/access token/i)).toBeInTheDocument()
      expect(screen.queryByRole('heading', { name: /^reviews$/i })).toBeNull()
      // Nothing should be fetched before we have a token to send.
      expect(listReviews).not.toHaveBeenCalled()
    })

    it('shows the app once the token is entered', async () => {
      clearToken()
      vi.mocked(listReviews).mockResolvedValue([])
      const user = userEvent.setup()
      render(<App />)

      await user.type(screen.getByLabelText(/access token/i), 'demo-token')
      await user.click(screen.getByRole('button', { name: /continue/i }))

      expect(
        await screen.findByRole('heading', { name: /^reviews$/i }),
      ).toBeInTheDocument()
    })

    it('returns to the gate when a request is rejected', async () => {
      vi.mocked(listReviews).mockResolvedValue([])
      render(<App />)
      await screen.findByRole('heading', { name: /^reviews$/i })

      // api.ts drops the token on a 401; this is what the app must do about it.
      clearToken()

      expect(await screen.findByLabelText(/access token/i)).toBeInTheDocument()
      expect(await screen.findByRole('alert')).toHaveTextContent(/not accepted/i)
    })
  })
})

/**
 * A share link is a separate entry point, not a phase of the review flow. The
 * decision has to happen before the demo gate, because the whole point is that
 * the recipient does not have a token.
 */
describe('App — opened from a share link', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(readShareParams).mockReturnValue(null)
  })

  it('renders the shared review instead of the gate, with no token present', async () => {
    clearToken()
    vi.mocked(readShareParams).mockReturnValue({ reviewId: 'rev-1', token: 'tok' })
    vi.mocked(getSharedReview).mockResolvedValue({
      review_id: 'rev-1',
      title: 'Payments platform',
      created_at: '2026-07-29T10:00:00Z',
      overall_score: 61.5,
      frameworks: ['AWS Well-Architected'],
      pillars: [],
      open_findings: 3,
      high_severity_open: 1,
      component_count: 4,
      delta: null,
      expires_note: 'This link stops working when the server restarts.',
    })

    render(<App />)

    expect(screen.queryByLabelText(/access token/i)).not.toBeInTheDocument()
    expect(await screen.findByText('Payments platform')).toBeInTheDocument()
    expect(listReviews).not.toHaveBeenCalled()
  })

  it('offers no way into the rest of the app from a shared review', async () => {
    clearToken()
    vi.mocked(readShareParams).mockReturnValue({ reviewId: 'rev-1', token: 'tok' })
    vi.mocked(getSharedReview).mockResolvedValue({
      review_id: 'rev-1',
      title: 'Payments platform',
      created_at: '2026-07-29T10:00:00Z',
      overall_score: 61.5,
      frameworks: ['AWS Well-Architected'],
      pillars: [],
      open_findings: 0,
      high_severity_open: 0,
      component_count: 1,
      delta: null,
      expires_note: 'This link stops working when the server restarts.',
    })

    render(<App />)
    await screen.findByText('Payments platform')

    expect(screen.queryByRole('button', { name: /all reviews/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /re-review/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: /sections/i })).not.toBeInTheDocument()
  })

  it('takes the normal gated path when the URL carries no share parameters', () => {
    clearToken()
    vi.mocked(readShareParams).mockReturnValue(null)

    render(<App />)

    expect(screen.getByLabelText(/access token/i)).toBeInTheDocument()
  })
})
