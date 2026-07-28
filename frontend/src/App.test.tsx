import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { listReviews } from './api'
import App from './App'
import { clearToken, setToken } from './token'

vi.mock('./api', () => ({
  ApiError: class ApiError extends Error {},
  listReviews: vi.fn(),
  uploadFile: vi.fn(),
  submitReview: vi.fn(),
  getStatus: vi.fn(),
  getReview: vi.fn(),
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
