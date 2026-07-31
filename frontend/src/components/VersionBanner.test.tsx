import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { VersionBanner } from './VersionBanner'
import { resultFixture } from '../test/fixtures'
import type { ReviewVersion } from '../types'

/**
 * "This is version N, here is what you said, here is where the previous one is."
 *
 * The failure this guards against is a follow-up round looking exactly like an
 * original: same layout, different score, no visible reason. The score delta does
 * not carry that on its own — it says the number moved, not that it moved because
 * of something the reviewer typed.
 */

const { getReviewVersions } = vi.hoisted(() => ({ getReviewVersions: vi.fn() }))
vi.mock('../api', () => ({ getReviewVersions }))

function version(over: Partial<ReviewVersion> = {}): ReviewVersion {
  return {
    review_id: 'rev-1',
    version: 1,
    created_at: '2026-07-31T10:00:00Z',
    overall_score: 62.5,
    open_findings: 4,
    feedback: '',
    based_on_review_id: '',
    is_original: true,
    ...over,
  }
}

const CHAIN = [
  version(),
  version({
    review_id: 'rev-2',
    version: 2,
    overall_score: 71.0,
    feedback: 'The orders table IS encrypted.',
    based_on_review_id: 'rev-1',
    is_original: false,
  }),
]

beforeEach(() => {
  getReviewVersions.mockReset()
  getReviewVersions.mockResolvedValue({
    root_review_id: 'rev-1',
    latest_review_id: 'rev-2',
    versions: CHAIN,
  })
})

function mount(over: Record<string, unknown> = {}, onOpenVersion = vi.fn()) {
  render(
    <VersionBanner result={resultFixture(over)} onOpenVersion={onOpenVersion} />,
  )
  return onOpenVersion
}

describe('VersionBanner', () => {
  it('renders nothing on an original review', () => {
    const { container } = render(
      <VersionBanner result={resultFixture()} onOpenVersion={vi.fn()} />,
    )
    // A "version 1 of 1" banner on every first review is noise, and noise is what
    // teaches people to stop reading banners.
    expect(container).toBeEmptyDOMElement()
    expect(getReviewVersions).not.toHaveBeenCalled()
  })

  it('renders nothing when the field is absent, as on an older stored review', () => {
    const { version: _dropped, ...older } = resultFixture({ version: 1 })
    const { container } = render(
      <VersionBanner result={older} onOpenVersion={vi.fn()} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('names the version and its place in the chain', async () => {
    mount({ review_id: 'rev-2', version: 2, based_on_review_id: 'rev-1' })

    const banner = screen.getByTestId('version-banner')
    expect(banner).toHaveAttribute('data-version', '2')
    expect(banner).toHaveTextContent(/Follow-up review — version\s*2/)
    await waitFor(() => expect(banner).toHaveTextContent(/of\s*2/))
  })

  it('quotes the feedback that produced this version', async () => {
    // The input that produced the new score, and the only place the round's
    // feedback is visible after the fact.
    mount({
      review_id: 'rev-2',
      version: 2,
      based_on_review_id: 'rev-1',
      feedback: 'The orders table IS encrypted — see section 4 of the SoW.',
    })
    expect(screen.getByTestId('version-feedback')).toHaveTextContent(
      'The orders table IS encrypted — see section 4 of the SoW.',
    )
  })

  it('links back to the version this round was built on', async () => {
    const user = userEvent.setup()
    const onOpen = mount({
      review_id: 'rev-2',
      version: 2,
      based_on_review_id: 'rev-1',
    })

    await user.click(screen.getByRole('button', { name: /Open the previous version/i }))
    expect(onOpen).toHaveBeenCalledWith('rev-1')
  })

  it('lists every version with its score, and marks the current one', async () => {
    const user = userEvent.setup()
    const onOpen = mount({
      review_id: 'rev-2',
      version: 2,
      based_on_review_id: 'rev-1',
    })

    const chain = await screen.findByTestId('version-chain')
    expect(chain).toHaveTextContent('v1')
    expect(chain).toHaveTextContent('62.5')
    expect(chain).toHaveTextContent('v2')
    expect(chain).toHaveTextContent('71.0')

    const current = screen.getByRole('button', { name: /v2/ })
    expect(current).toBeDisabled()
    expect(current).toHaveAttribute('aria-current', 'page')

    await user.click(screen.getByRole('button', { name: /v1/ }))
    expect(onOpen).toHaveBeenCalledWith('rev-1')
  })

  it('degrades quietly when the chain cannot be fetched', async () => {
    // Context for a review that has already loaded and rendered. Failing to fetch
    // it must not put an error in front of someone reading their findings.
    getReviewVersions.mockRejectedValue(new Error('offline'))
    mount({
      review_id: 'rev-2',
      version: 2,
      based_on_review_id: 'rev-1',
      feedback: 'Re-check encryption.',
    })

    await waitFor(() => expect(getReviewVersions).toHaveBeenCalled())
    expect(screen.queryByRole('alert')).toBeNull()
    // Falls back to what the result itself knows: the number, and one link back.
    expect(screen.getByTestId('version-banner')).toHaveTextContent(/version\s*2/)
    expect(
      screen.getByRole('button', { name: /Open the previous version/i }),
    ).toBeInTheDocument()
    expect(screen.queryByTestId('version-chain')).toBeNull()
  })

  it('asks the chain endpoint once, from the version being viewed', async () => {
    mount({ review_id: 'rev-2', version: 2, based_on_review_id: 'rev-1' })
    await waitFor(() => expect(getReviewVersions).toHaveBeenCalledTimes(1))
    // Any member answers; the id in hand is the one to ask with.
    expect(getReviewVersions).toHaveBeenCalledWith('rev-2')
  })
})
