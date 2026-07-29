import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  OPENROUTER_KEY_HEADER,
  getSharedReview,
  getStatus,
  readShareParams,
  shareUrl,
  submitReview,
} from './api'
import { clearApiKey, setApiKey } from './apiKey'
import { setToken } from './token'

const KEY = 'sk-or-v1-user-supplied-key-a1b2c3d4e5f6a7b8c9d0'

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function headersOfLastCall(mock: ReturnType<typeof vi.fn>): Headers {
  const [, init] = mock.mock.calls.at(-1) as [string, RequestInit]
  return new Headers(init.headers as HeadersInit)
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  setToken('demo-token')
  fetchMock = vi.fn().mockResolvedValue(
    jsonResponse({ review_id: 'rev-1', status_url: '/s', result_url: '/r' }),
  )
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  clearApiKey()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('the reviewer-supplied OpenRouter key on the wire', () => {

  it('sends no key header when the reviewer supplied none', async () => {
    await submitReview({ documentKey: 'uploads/x/sow.md' })

    expect(headersOfLastCall(fetchMock).has(OPENROUTER_KEY_HEADER)).toBe(false)
  })

  it('sends the key on the header, not in the body', async () => {
    setApiKey(KEY)

    await submitReview({ documentKey: 'uploads/x/sow.md' })

    const [, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit]
    expect(headersOfLastCall(fetchMock).get(OPENROUTER_KEY_HEADER)).toBe(KEY)
    // The body is what a 422 echoes back, so the key must not be in it.
    expect(String(init.body)).not.toContain(KEY)
  })

  it('sends it on a re-review too, which also spends tokens', async () => {
    setApiKey(KEY)

    await submitReview({ documentKey: 'uploads/x/sow.md', previousReviewId: 'rev-0' })

    const [url] = fetchMock.mock.calls.at(-1) as [string, RequestInit]
    expect(url).toContain('/reanalyze')
    expect(headersOfLastCall(fetchMock).get(OPENROUTER_KEY_HEADER)).toBe(KEY)
  })

  /**
   * Polling runs every 1.5s for the length of a review. Attaching the credential
   * to it would put the key on the wire hundreds of times per review, for calls
   * that make no model requests and so have nothing to bill.
   */
  it('does not attach the key to status polling', async () => {
    setApiKey(KEY)
    fetchMock.mockResolvedValue(jsonResponse({ review_id: 'rev-1', state: 'running' }))

    await getStatus('rev-1')

    expect(headersOfLastCall(fetchMock).has(OPENROUTER_KEY_HEADER)).toBe(false)
  })
})

describe('share link URLs', () => {
  it('round-trips: a URL built from a link parses back to the same parameters', () => {
    const link = {
      review_id: 'rev-1',
      token: 'a'.repeat(32),
      path: '/shared/rev-1',
      expires_note: 'note',
    }

    const url = shareUrl(link)

    expect(readShareParams(new URL(url).search)).toEqual({
      reviewId: 'rev-1',
      token: 'a'.repeat(32),
    })
  })

  it('encodes ids and tokens rather than pasting them raw into the URL', () => {
    const url = shareUrl({
      review_id: 'a b&c',
      token: 'x/y?z',
      path: '',
      expires_note: '',
    })

    expect(url).not.toContain('a b&c')
    expect(readShareParams(new URL(url).search)).toEqual({
      reviewId: 'a b&c',
      token: 'x/y?z',
    })
  })

  it('reads nothing from a normal page load', () => {
    expect(readShareParams('')).toBeNull()
  })

  it('needs both parameters — half a link is not a link', () => {
    expect(readShareParams('?share=rev-1')).toBeNull()
    expect(readShareParams('?t=abc')).toBeNull()
  })

  it('sends the token as the query parameter the server reads', async () => {
    await getSharedReview('rev-1', 'tok-123')

    const [url] = fetchMock.mock.calls.at(-1) as [string, RequestInit]
    expect(url).toContain('/shared/rev-1')
    expect(url).toContain('t=tok-123')
  })
})
