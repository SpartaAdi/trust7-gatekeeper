import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { listReviews } from './api'
import App from './App'

vi.mock('./api', () => ({
  ApiError: class ApiError extends Error {},
  listReviews: vi.fn(),
  uploadFile: vi.fn(),
  submitReview: vi.fn(),
  getStatus: vi.fn(),
  getReview: vi.fn(),
}))

describe('App', () => {
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
})
