import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import App from './App'

describe('App', () => {
  it('renders without throwing', () => {
    expect(() => render(<App />)).not.toThrow()
  })

  it('shows the step tracker with all three steps', () => {
    render(<App />)

    const tracker = screen.getByRole('navigation', { name: /progress/i })
    expect(tracker).toBeInTheDocument()
    expect(tracker).toHaveTextContent('Upload')
    expect(tracker).toHaveTextContent('Analyzing')
    expect(tracker).toHaveTextContent('Results')
  })

  it('starts on the upload step', () => {
    render(<App />)
    expect(
      screen.getByRole('heading', { name: /submit a design for review/i }),
    ).toBeInTheDocument()
  })
})
