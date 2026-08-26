import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { statusFixture } from '../test/fixtures'
import { AnalyzingView } from './AnalyzingView'

// `FakeApiError` is hoisted with the mocks: `vi.mock`'s factory runs before any
// module-scope class declaration would have been initialised.
const { getStatus, cancelReview, FakeApiError } = vi.hoisted(() => ({
  getStatus: vi.fn(),
  cancelReview: vi.fn(),
  FakeApiError: class FakeApiError extends Error {
    status: number
    constructor(message: string, status: number) {
      super(message)
      this.status = status
    }
  },
}))

/** A fixed submission instant, so elapsed assertions are deterministic. */
const START = Date.parse('2026-07-29T10:00:00Z')

vi.mock('../api', () => ({
  ApiError: FakeApiError,
  getStatus,
  cancelReview,
}))

describe('AnalyzingView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    cancelReview.mockResolvedValue(statusFixture({ state: 'cancelled' }))
  })

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
  // Cancellation — a deliberate stop, not a failure
  // ------------------------------------------------------------------------- #

  describe('cancelling a running review', () => {
    /** A status whose stages record where a cancel stopped the run. */
    const cancelledFixture = () =>
      statusFixture({
        state: 'cancelled',
        stages: statusFixture().stages.map((stage) =>
          stage.name === 'classify'
            ? { ...stage, state: 'cancelled' as const, detail: 'Stopped by the reviewer' }
            : stage,
        ),
      })

    it('offers a stop button while the review is running', async () => {
      getStatus.mockResolvedValue(statusFixture())

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
      )

      const button = await screen.findByRole('button', { name: /stop this review/i })
      // Placed with the progress block it acts on, and honest about the cost: the
      // step already running may still be charged.
      expect(button).toBeEnabled()
      expect(screen.getByText(/may still finish and be charged/i)).toBeInTheDocument()
    })

    it('offers no stop button once the run has settled', async () => {
      getStatus.mockResolvedValue(
        statusFixture({ state: 'error', error: 'ProviderStreamError: mid-stream' }),
      )

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
      )

      await screen.findByRole('alert')
      // Absent rather than disabled: there is nothing left to stop.
      expect(screen.queryByRole('button', { name: /stop this review/i })).toBeNull()
    })

    it('asks the server to cancel, and does not merely stop polling', async () => {
      const user = userEvent.setup()
      getStatus.mockResolvedValue(statusFixture())
      cancelReview.mockResolvedValue(cancelledFixture())

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
      )
      await user.click(await screen.findByRole('button', { name: /stop this review/i }))

      // The whole point of the backend half: a frontend that only stopped polling
      // would leave the pipeline burning calls nobody is waiting for.
      await waitFor(() => expect(cancelReview).toHaveBeenCalledWith('rev-1'))
    })

    it('stops polling for status', async () => {
      const user = userEvent.setup()
      getStatus.mockResolvedValue(statusFixture())

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
      )
      await user.click(await screen.findByRole('button', { name: /stop this review/i }))
      await waitFor(() => expect(cancelReview).toHaveBeenCalled())

      const pollsAtCancel = getStatus.mock.calls.length
      // Longer than POLL_INTERVAL_MS (1500ms) — a shorter wait would pass even with
      // the poll still armed, which is exactly the bug this test is here to catch.
      await new Promise((resolve) => setTimeout(resolve, 1800))
      expect(getStatus.mock.calls.length).toBe(pollsAtCancel)
    })

    it('shows a cancelled state that is not the error state', async () => {
      const user = userEvent.setup()
      getStatus.mockResolvedValue(statusFixture())
      cancelReview.mockResolvedValue(cancelledFixture())

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
      )
      await user.click(await screen.findByRole('button', { name: /stop this review/i }))

      expect(
        await screen.findByRole('heading', { name: /review cancelled/i }),
      ).toBeInTheDocument()
      // A status, not an alert: nothing went wrong, and an alert role would have a
      // screen reader announce a deliberate action as an emergency.
      const notice = screen.getByRole('status')
      expect(notice).toHaveTextContent(/cancelled at your request/i)
      expect(screen.queryByRole('alert')).toBeNull()
      expect(screen.queryByText(/pipeline error/i)).toBeNull()
      // And it says what became of the review, since that is the reviewer's next
      // question.
      expect(notice).toHaveTextContent(/no further steps were started/i)
      expect(notice).toHaveTextContent(/not saved/i)
    })

    it('renders the cancelled state before the server has answered', async () => {
      // The request may take a moment and the pipeline only notices at its next
      // stage boundary. The reviewer's intent is already known.
      const user = userEvent.setup()
      getStatus.mockResolvedValue(statusFixture())
      cancelReview.mockReturnValue(new Promise(() => {}))

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
      )
      await user.click(await screen.findByRole('button', { name: /stop this review/i }))

      expect(
        await screen.findByRole('heading', { name: /review cancelled/i }),
      ).toBeInTheDocument()
    })

    it('hides the progress bar, which would imply work still queued', async () => {
      const user = userEvent.setup()
      getStatus.mockResolvedValue(statusFixture())
      cancelReview.mockResolvedValue(cancelledFixture())

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
      )
      await user.click(await screen.findByRole('button', { name: /stop this review/i }))

      await screen.findByRole('status')
      expect(screen.queryByRole('progressbar')).toBeNull()
      // No stage left spinning, either.
      expect(screen.queryByLabelText('In progress')).toBeNull()
      expect(screen.getByLabelText('Stopped')).toBeInTheDocument()
    })

    it('accepts a cancellation that arrived from somewhere else', async () => {
      // A second tab, or this tab's click landing between two polls.
      getStatus.mockResolvedValue(cancelledFixture())

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
      )

      expect(
        await screen.findByRole('heading', { name: /review cancelled/i }),
      ).toBeInTheDocument()
      expect(cancelReview).not.toHaveBeenCalled()
    })

    it('never reports a cancelled review as complete', async () => {
      const user = userEvent.setup()
      const onComplete = vi.fn()
      getStatus.mockResolvedValue(statusFixture())
      cancelReview.mockResolvedValue(cancelledFixture())

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={onComplete} onStartOver={vi.fn()} />,
      )
      await user.click(await screen.findByRole('button', { name: /stop this review/i }))
      await screen.findByRole('status')

      // The results view is only ever reached through this callback, so not calling
      // it is what stops a cancelled review being displayed as a finished one.
      expect(onComplete).not.toHaveBeenCalled()
    })

    it('swallows a 409 — the run had already finished', async () => {
      const user = userEvent.setup()
      getStatus.mockResolvedValue(statusFixture())
      cancelReview.mockRejectedValue(new FakeApiError('Review is already complete.', 409))

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
      )
      await user.click(await screen.findByRole('button', { name: /stop this review/i }))
      await screen.findByRole('heading', { name: /review cancelled/i })

      // Nothing to stop and nothing to apologise for: no failure is shown.
      expect(screen.queryByText(/could not stop/i)).toBeNull()
    })

    it('says so when the cancel request itself fails', async () => {
      // The pipeline may well still be running, so claiming a stop that did not
      // happen would be the one genuinely misleading outcome here.
      const user = userEvent.setup()
      getStatus.mockResolvedValue(statusFixture())
      cancelReview.mockRejectedValue(new FakeApiError('Service unavailable.', 503))

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
      )
      await user.click(await screen.findByRole('button', { name: /stop this review/i }))

      expect(await screen.findByRole('alert')).toHaveTextContent('Service unavailable.')
      expect(screen.queryByRole('heading', { name: /review cancelled/i })).toBeNull()
      // And the button comes back, so the reviewer can try again.
      expect(screen.getByRole('button', { name: /stop this review/i })).toBeInTheDocument()
    })
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
      expect(clock.parentElement).toHaveTextContent('3 of 7 stages')
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

    it('FREEZES on a cancel, the same as on an error', async () => {
      // Item (b): the same latch, not a second copy of it. The clock advances during
      // the cancel request, so 47s can only have come from the freeze.
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      getStatus.mockResolvedValue(statusFixture())
      cancelReview.mockImplementation(async () => {
        at(START + 47_000)
        return statusFixture({ state: 'cancelled' })
      })

      renderRunning()
      at(START + 47_000)
      await user.click(await screen.findByRole('button', { name: /stop this review/i }))
      await screen.findByRole('status')

      expect(screen.getByTestId('elapsed')).toHaveTextContent('47s elapsed')

      // And it does not keep ticking afterwards.
      at(START + 300_000)
      await tick(60_000)
      expect(screen.getByTestId('elapsed')).toHaveTextContent('47s elapsed')
      expect(screen.getByTestId('elapsed')).not.toHaveTextContent('0s')
    })

    it('freezes on cancel through the same latch the error path uses', () => {
      // Structural, because the instruction was explicitly "reuse it, don't
      // duplicate it": one `freeze` definition, and the cancel handler calls it.
      const source = AnalyzingView.toString()
      expect(source.match(/function freeze\(/g) ?? []).toHaveLength(1)
    })

    it('shows the run duration on the cancelled screen', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      at(START + 9000)
      getStatus.mockResolvedValue(statusFixture())
      cancelReview.mockResolvedValue(statusFixture({ state: 'cancelled' }))

      renderRunning()
      await user.click(await screen.findByRole('button', { name: /stop this review/i }))
      await screen.findByRole('status')

      // Same "Ran for" row the failure screen uses — the progress block is hidden on
      // both, and how long a run got before being stopped is worth knowing either way.
      expect(screen.getByText('Ran for')).toBeInTheDocument()
      expect(screen.getByTestId('elapsed')).toHaveTextContent('9s elapsed')
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

  describe('a rejected upload', () => {
    const rejection =
      'This upload does not look like a solution design, so it was not reviewed ' +
      'and nothing was charged for it. It appears to be a curriculum vitae.'

    const rejectedFixture = () =>
      statusFixture({
        state: 'rejected',
        rejection,
        stages: [
          { name: 'ingest', state: 'done', detail: '', started_at: '', finished_at: '' },
          { name: 'normalize', state: 'done', detail: '', started_at: '', finished_at: '' },
          {
            name: 'screen',
            state: 'rejected',
            detail: 'Not a solution design — not reviewed',
            started_at: '',
            finished_at: '',
          },
          { name: 'classify', state: 'pending', detail: '', started_at: '', finished_at: '' },
          { name: 'evaluate', state: 'pending', detail: '', started_at: '', finished_at: '' },
          { name: 'prioritize', state: 'pending', detail: '', started_at: '', finished_at: '' },
          { name: 'remediate', state: 'pending', detail: '', started_at: '', finished_at: '' },
        ],
      })

    it('shows the reason, and does NOT present it as a pipeline error', async () => {
      // The distinction this whole state exists for. Nothing malfunctioned — the
      // gate looked at the upload and declined it — so a "Pipeline error" heading
      // would send the submitter hunting for a fault that is not there.
      getStatus.mockResolvedValue(rejectedFixture())

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
      )

      expect(
        await screen.findByRole('heading', { name: /not a solution design/i }),
      ).toBeInTheDocument()
      const notice = screen.getByRole('status')
      expect(notice).toHaveTextContent(/curriculum vitae/i)
      expect(notice).toHaveTextContent(/nothing was charged/i)
      // A status, not an alert, and never the error panel.
      expect(screen.queryByText(/pipeline error/i)).toBeNull()
      expect(screen.queryByRole('alert')).toBeNull()
    })

    it('never reports a rejected review as complete', async () => {
      // `onComplete` navigates to the results page, and there is no result to show.
      const onComplete = vi.fn()
      getStatus.mockResolvedValue(rejectedFixture())

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={onComplete} onStartOver={vi.fn()} />,
      )
      await screen.findByRole('heading', { name: /not a solution design/i })

      expect(onComplete).not.toHaveBeenCalled()
    })

    it('stops polling, and stops offering to stop the review', async () => {
      // `rejected` is terminal. A spinner and a stop button on a finished refusal
      // both imply work is still queued.
      getStatus.mockResolvedValue(rejectedFixture())

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
      )
      await screen.findByRole('heading', { name: /not a solution design/i })
      const callsAfterSettling = getStatus.mock.calls.length

      // Fake timers are local to this test — the surrounding describe runs on real
      // ones, and switching globally would change every test above it.
      vi.useFakeTimers({ shouldAdvanceTime: true })
      try {
        await act(async () => {
          vi.advanceTimersByTime(30_000)
        })
        expect(getStatus).toHaveBeenCalledTimes(callsAfterSettling)
      } finally {
        vi.useRealTimers()
      }
      expect(screen.queryByRole('button', { name: /stop this review/i })).toBeNull()
    })

    it('marks the screen stage itself as the one that refused', async () => {
      getStatus.mockResolvedValue(rejectedFixture())

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
      )

      expect(await screen.findByRole('img', { name: 'Rejected' })).toBeInTheDocument()
      expect(screen.getByText(/not a solution design — not reviewed/i)).toBeInTheDocument()
    })

    it('still shows how long the run got before it was refused', async () => {
      getStatus.mockResolvedValue(rejectedFixture())

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
      )
      await screen.findByRole('heading', { name: /not a solution design/i })

      expect(screen.getByText(/ran for/i)).toBeInTheDocument()
    })
  })

  describe('extraction warnings', () => {
    const warned = () =>
      statusFixture({
        warnings: [
          {
            code: 'diagram_near_empty',
            message:
              'Almost nothing could be read from the uploaded diagram — 1 component ' +
              'was extracted from an image of 488 KB.',
            detail: 'screenshot.png: 500000 bytes, 1 components',
          },
        ],
      })

    it('shows them WHILE the review is still running', async () => {
      // The whole reason warnings are published onto the status rather than only onto
      // the stored result: a reviewer who learns now can stop the run and upload a
      // better copy instead of paying for it and finding out at the end.
      getStatus.mockResolvedValue(warned())

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
      )

      expect(await screen.findByTestId('ingest-warnings')).toBeInTheDocument()
      expect(screen.getByText(/Almost nothing could be read/)).toBeInTheDocument()
      // Still running: the stop button is the point of showing this early.
      expect(screen.getByRole('button', { name: /stop this review/i })).toBeInTheDocument()
    })

    it('shows no warning panel on a clean run', async () => {
      getStatus.mockResolvedValue(statusFixture())

      render(
        <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
      )
      await screen.findByText('Classifying components')

      expect(screen.queryByTestId('ingest-warnings')).toBeNull()
    })
  })

})

describe('AnalyzingView — duration expectation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getStatus.mockResolvedValue(statusFixture())
  })

  it('states a typical range next to the clock, as static text', async () => {
    render(
      <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
    )

    const note = await screen.findByTestId('duration-note')
    expect(note).toHaveTextContent(
      /typically 1–10 min; can occasionally run longer depending on provider load/i,
    )
  })

  /**
   * The range is an expectation, not a prediction. Observed latency has spanned
   * 14s to 44 minutes on the same provider, so a figure that counted down or
   * narrowed as the run proceeded would be wrong most of the time — the same
   * reason the ETA was removed and must not come back.
   */
  it('does not change as the run proceeds, and promises no completion time', async () => {
    const { rerender } = render(
      <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
    )
    const before = (await screen.findByTestId('duration-note')).textContent

    rerender(
      <AnalyzingView
        reviewId="rev-1"
        startedAt={START - 300_000}
        onComplete={vi.fn()}
        onStartOver={vi.fn()}
      />,
    )

    expect(screen.getByTestId('duration-note').textContent).toBe(before)
    expect(screen.getByTestId('duration-note').textContent).not.toMatch(
      /remaining|left\b|estimat|eta/i,
    )
  })

  // ------------------------------------------------------------------------- #
  // Live token / cost counter
  // ------------------------------------------------------------------------- #

  it('shows nothing at all before the first stage reports usage', async () => {
    // Zero is not "spent nothing", it is "nothing has finished yet". Rendering
    // "0 tokens · ~$0.0000" would make a claim the status has not made.
    getStatus.mockResolvedValue(statusFixture())

    render(
      <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
    )

    await screen.findByText('Classifying components')
    expect(screen.queryByTestId('token-counter')).not.toBeInTheDocument()
    expect(screen.queryByTestId('cost-disclosure')).not.toBeInTheDocument()
  })

  it('shows the running token total and cost once usage is reported', async () => {
    getStatus.mockResolvedValue(
      statusFixture({
        token_usage: { input_tokens: 120_000, output_tokens: 30_000, cache_read_input_tokens: 90_000 },
        estimated_cost_usd: 0.18,
      }),
    )

    render(
      <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
    )

    const counter = await screen.findByTestId('token-counter')
    // 120,000 + 30,000. The cached 90,000 is a SUBSET of the input, so counting it
    // again would report 240,000 for a run that used 150,000.
    expect(counter).toHaveTextContent('150,000 tokens')
    expect(counter).toHaveTextContent('$0.18')
  })

  it('never presents the cost as a precise figure', async () => {
    // The backend prices every call at the dearest of the three locked providers,
    // so the number is a ceiling. Presenting it bare would read as a bill.
    getStatus.mockResolvedValue(
      statusFixture({
        token_usage: { input_tokens: 100, output_tokens: 100 },
        estimated_cost_usd: 0.0004,
      }),
    )

    render(
      <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
    )

    expect(await screen.findByTestId('token-counter')).toHaveTextContent('at most')
    expect(screen.getByTestId('cost-disclosure')).toHaveTextContent(/estimated at list pricing/i)
  })

  it('does not add a second live region beside the elapsed clock', async () => {
    // `ElapsedClock` is already polite and ticks every second. A second live region
    // on the same line would have a screen reader interrupt itself twice a second
    // and neither figure would be readable.
    getStatus.mockResolvedValue(
      statusFixture({
        token_usage: { input_tokens: 1000, output_tokens: 500 },
        estimated_cost_usd: 0.01,
      }),
    )

    render(
      <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
    )

    const counter = await screen.findByTestId('token-counter')
    expect(counter).not.toHaveAttribute('aria-live')
    expect(screen.getByTestId('elapsed')).toHaveAttribute('aria-live', 'polite')
  })

  it('does not claim to review every check the frameworks contain', () => {
    // 45 is correct and is every check in THIS rubric. Unqualified, though,
    // "all 45 checks" reads as every check the two frameworks hold — a claim the
    // tool cannot support. AWS's Well-Architected set is larger, and TRUST-7 is a
    // maturity model with no discrete check count at all.
    getStatus.mockResolvedValue(statusFixture())

    render(
      <AnalyzingView reviewId="rev-1" startedAt={START} onComplete={vi.fn()} onStartOver={vi.fn()} />,
    )

    expect(screen.getByText(/45 rubric checks/)).toBeInTheDocument()
    expect(screen.queryByText(/all 45 checks\b/)).not.toBeInTheDocument()
  })
})