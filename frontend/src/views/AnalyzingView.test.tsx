import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { statusFixture } from '../test/fixtures'
import { AnalyzingView } from './AnalyzingView'

const { getStatus } = vi.hoisted(() => ({ getStatus: vi.fn() }))

/** A fixed submission instant, so elapsed assertions are deterministic. */
const START = Date.parse('2026-07-29T10:00:00Z')

vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {},
  getStatus,
}))

describe('AnalyzingView', () => {
  it('renders a row per pipeline stage from the status response', async () => {
    getStatus.mockResolvedValue(statusFixture())

    render(
      <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
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
      <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={onComplete} onStartOver={vi.fn()} />,
    )

    await waitFor(() => expect(onComplete).toHaveBeenCalled())
  })

  it('promises no completion time, anywhere', async () => {
    // There used to be an "about N min remaining" estimate beside the clock. It is
    // gone on purpose: one review has taken anything from 14 seconds to 44 minutes on
    // the same provider, so any figure claiming to know when this ends is wrong most
    // of the time. Asserted over the whole screen, not just the old element, so the
    // wording cannot creep back in somewhere else.
    getStatus.mockResolvedValue(statusFixture())

    const { container } = render(
      <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
    )

    const progress = await screen.findByRole('progressbar')
    expect(progress).toBeInTheDocument()
    expect(screen.queryByTestId('eta')).not.toBeInTheDocument()
    expect(container.textContent).not.toMatch(/remaining|estimat|left\b|about \d/i)
  })

  it('promises no completion time on the failure screen either', async () => {
    getStatus.mockResolvedValue(
      statusFixture({ state: 'error', error: 'ModelRefusal: declined' }),
    )

    const { container } = render(
      <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
    )

    await screen.findByRole('alert')
    expect(screen.queryByTestId('eta')).not.toBeInTheDocument()
    expect(container.textContent).not.toMatch(/remaining|estimat|left\b|about \d/i)
  })

  it('surfaces a pipeline error instead of polling silently', async () => {
    getStatus.mockResolvedValue(
      statusFixture({ state: 'error', error: 'ModelRefusal: declined' }),
    )

    render(
      <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('ModelRefusal: declined')
  })

  // ------------------------------------------------------------------------- #
  // Elapsed clock — a clock, and it must stop
  //
  // Time is controlled two ways, and both are needed. `Date.now` is stubbed because
  // that is what the component reads for its value; fake timers drive the 1s
  // interval. `shouldAdvanceTime: true` is required or testing-library's async
  // helpers never resolve, and it does not disturb the value because the stub, not
  // the fake clock, is what the component reads.
  // ------------------------------------------------------------------------- #

  describe('elapsed clock', () => {
    let now = START

    function at(instant: number) {
      now = instant
    }

    /** Advance the interval, then let the tree settle. */
    async function tick(ms = 1000) {
      await act(async () => {
        vi.advanceTimersByTime(ms)
      })
    }

    beforeEach(() => {
      now = START
      vi.useFakeTimers({ shouldAdvanceTime: true })
      vi.spyOn(Date, 'now').mockImplementation(() => now)
    })

    afterEach(() => {
      vi.restoreAllMocks()
      vi.useRealTimers()
    })

    function renderRunning() {
      return render(
        <AnalyzingView
          reviewId="rev-1"
          startedAt={START}
          onComplete={vi.fn()}
          onStartOver={vi.fn()}
        />,
      )
    }

    it('counts up from the submission instant, beside the stage tracker', async () => {
      at(START + 84_000)
      getStatus.mockResolvedValue(statusFixture())

      renderRunning()

      const clock = await screen.findByTestId('elapsed')
      expect(clock).toHaveTextContent('1m 24s elapsed')
      // Same row as the stage count, so it reads as "how long so far" alongside
      // "what stage it is on" rather than as a separate widget.
      expect(clock.parentElement).toHaveTextContent('2 of 6 stages')
    })

    it('ticks while the review is running', async () => {
      getStatus.mockResolvedValue(statusFixture())
      renderRunning()
      expect(await screen.findByTestId('elapsed')).toHaveTextContent('0s elapsed')

      at(START + 3000)
      await tick()

      expect(screen.getByTestId('elapsed')).toHaveTextContent('3s elapsed')
    })

    it('FREEZES when the review completes, and does not keep ticking', async () => {
      // The clock moves during the request that reports completion, so 30s is a value
      // only the terminal read can produce — the last tick before it saw 0s.
      getStatus.mockImplementation(async () => {
        at(START + 30_000)
        return statusFixture({
          state: 'complete',
          stages: statusFixture().stages.map((stage) => ({ ...stage, state: 'done' })),
        })
      })
      // The real App navigates away on complete; keeping the view mounted is what
      // makes the interval's post-terminal behaviour observable at all.
      renderRunning()

      expect(await screen.findByTestId('elapsed')).toHaveTextContent('30s elapsed')

      at(START + 40_000)
      await tick(10_000)

      expect(screen.getByTestId('elapsed')).toHaveTextContent('30s elapsed')
    })

    it('FREEZES on an error rather than resetting to zero', async () => {
      // Again the clock advances during the failing request, so 2m 05s can only have
      // come from the freeze and not from the tick that preceded it.
      getStatus.mockImplementation(async () => {
        at(START + 125_000)
        return statusFixture({ state: 'error', error: 'ProviderStreamError: mid-stream' })
      })

      renderRunning()
      await screen.findByRole('alert')
      expect(screen.getByTestId('elapsed')).toHaveTextContent('2m 05s elapsed')

      at(START + 200_000)
      await tick(75_000)

      // The elapsed time up to a failure is itself information.
      expect(screen.getByTestId('elapsed')).toHaveTextContent('2m 05s elapsed')
      expect(screen.getByTestId('elapsed')).not.toHaveTextContent('0s')
    })

    it('is still shown on the failure screen, where the progress block is hidden', async () => {
      at(START + 9000)
      getStatus.mockResolvedValue(statusFixture({ state: 'error', error: 'boom' }))

      renderRunning()
      await screen.findByRole('alert')

      expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
      expect(screen.getByTestId('elapsed')).toHaveTextContent('9s elapsed')
      expect(screen.getByText('Ran for')).toBeInTheDocument()
    })

    it('freezes at the true duration, not at the last tick before it', async () => {
      getStatus.mockResolvedValue(statusFixture())
      renderRunning()
      await screen.findByTestId('elapsed')

      at(START + 2000)
      await tick()
      expect(screen.getByTestId('elapsed')).toHaveTextContent('2s elapsed')

      // Time passes *during* the request that reports the failure, as it does in
      // reality. The run therefore ends at 3.8s, well after the 2s tick — freezing at
      // that tick instead of at the moment of the terminal read would under-report the
      // duration by nearly two seconds.
      getStatus.mockImplementation(async () => {
        at(START + 3800)
        return statusFixture({ state: 'error', error: 'boom' })
      })
      // Far enough for the next poll (due 500ms from here), deliberately short of the
      // next tick (due at 1000ms) — otherwise a tick could land after the terminal
      // read and stand in for the freeze, hiding its absence.
      await act(async () => {
        vi.advanceTimersByTime(600)
      })

      await waitFor(() =>
        expect(screen.getByTestId('elapsed')).toHaveTextContent('3s elapsed'),
      )
    })

    it('leaves no interval running once the review has settled', async () => {
      // The latch means a stray tick could no longer be *seen*, which is exactly why
      // this needs asserting separately: an interval firing forever on a finished
      // screen is a leak whether or not it changes a pixel.
      getStatus.mockResolvedValue(
        statusFixture({ state: 'error', error: 'ProviderStreamError: mid-stream' }),
      )

      renderRunning()
      await screen.findByRole('alert')
      // Let the teardown effect flush; it can land a beat after the render it follows.
      await tick(0)

      // The poll loop stops on a terminal state too, so nothing at all should remain.
      expect(vi.getTimerCount()).toBe(0)
    })

    it('clears its interval on unmount', async () => {
      const cleared: number[] = []
      const realClear = window.clearInterval
      vi.spyOn(window, 'clearInterval').mockImplementation((id) => {
        cleared.push(id as number)
        return realClear(id)
      })
      getStatus.mockResolvedValue(statusFixture())

      const { unmount } = renderRunning()
      await screen.findByTestId('elapsed')

      unmount()

      // An interval still firing would set state on an unmounted tree.
      expect(cleared.length).toBeGreaterThan(0)
    })
  })
})
