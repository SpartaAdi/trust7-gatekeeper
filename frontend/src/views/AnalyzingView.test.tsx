import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { statusFixture } from '../test/fixtures'
import { AnalyzingView } from './AnalyzingView'

const { getStatus } = vi.hoisted(() => ({ getStatus: vi.fn() }))

vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {},
  getStatus,
}))

describe('AnalyzingView', () => {
  it('renders a row per pipeline stage from the status response', async () => {
    getStatus.mockResolvedValue(statusFixture())

    render(
      <AnalyzingView reviewId="rev-1" onComplete={vi.fn()} onStartOver={vi.fn()} />,
    )

    expect(await screen.findByText('Classifying components')).toBeInTheDocument()
    expect(screen.getByText('Evaluating against rubric')).toBeInTheDocument()
    expect(screen.getByText('Generating remediation')).toBeInTheDocument()
    // Detail text comes straight from the backend, not from a timer.
    expect(screen.getByText('4 components from drawio')).toBeInTheDocument()
  })

  it('advances to results when the status reports complete', async () => {
    const onComplete = vi.fn()
    getStatus.mockResolvedValue(
      statusFixture({
        state: 'complete',
        stages: statusFixture().stages.map((stage) => ({ ...stage, state: 'done' })),
      }),
    )

    render(
      <AnalyzingView reviewId="rev-1" onComplete={onComplete} onStartOver={vi.fn()} />,
    )

    await waitFor(() => expect(onComplete).toHaveBeenCalled())
  })

  it('surfaces a pipeline error instead of polling silently', async () => {
    getStatus.mockResolvedValue(
      statusFixture({ state: 'error', error: 'ModelRefusal: declined' }),
    )

    render(
      <AnalyzingView reviewId="rev-1" onComplete={vi.fn()} onStartOver={vi.fn()} />,
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('ModelRefusal: declined')
  })
})
