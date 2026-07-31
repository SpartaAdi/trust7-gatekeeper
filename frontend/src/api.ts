/**
 * The only module that talks to the API.
 *
 * Every call throws `ApiError` on failure — no call returns a partial or empty
 * result on error, so views can't silently render nothing.
 */

import { getApiKey } from './apiKey'
import { clearToken, getToken } from './token'
import type {
  ReviewAccepted,
  ReviewResult,
  ReviewStatus,
  ReviewSummary,
  ReviewVersions,
  ShareLink,
  SharedReview,
  UploadTicket,
} from './types'

const BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'
).replace(/\/+$/, '')

/** Header carrying the shared demo token. Mirrors config.DEMO_TOKEN_HEADER. */
export const TOKEN_HEADER = 'X-Demo-Token'

/**
 * The token header, or nothing if we have no token yet.
 *
 * Every request goes through this, so there is no path that forgets it.
 */
function authHeaders(): Record<string, string> {
  const token = getToken()
  return token ? { [TOKEN_HEADER]: token } : {}
}

/**
 * A 401 means the token is missing, wrong, or the server has none configured.
 * Dropping it here is what makes the app re-prompt instead of looping on a
 * credential that will never work.
 */
function forgetTokenOn401(status: number): void {
  if (status === 401) clearToken()
}

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** FastAPI's `detail` is a string for HTTPException and an array for validation errors. */
function detailToMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const parts = detail
      .map((entry) =>
        typeof entry === 'object' && entry !== null && 'msg' in entry
          ? String((entry as { msg: unknown }).msg)
          : null,
      )
      .filter((part): part is string => part !== null)
    if (parts.length > 0) return parts.join('; ')
  }
  return fallback
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders(),
        ...init?.headers,
      },
    })
  } catch (cause) {
    throw new ApiError(
      `Cannot reach the API at ${BASE_URL}. Is the backend running?`,
      0,
    )
  }

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const body: unknown = await response.json()
      if (typeof body === 'object' && body !== null && 'detail' in body) {
        message = detailToMessage((body as { detail: unknown }).detail, message)
      }
    } catch {
      // Non-JSON error body; the status line is the best message available.
    }
    forgetTokenOn401(response.status)
    throw new ApiError(message, response.status)
  }

  return (await response.json()) as T
}

/** Upload a file and return the key to reference it by. */
export async function uploadFile(file: File): Promise<string> {
  const body = new FormData()
  body.append('file', file)

  let response: Response
  try {
    // No Content-Type header: the browser sets it, including the multipart
    // boundary, which cannot be written by hand.
    response = await fetch(`${BASE_URL}/uploads`, {
      method: 'POST',
      body,
      headers: authHeaders(),
    })
  } catch {
    throw new ApiError(`Upload of ${file.name} failed: the request never completed.`, 0)
  }

  if (!response.ok) {
    let message = `Upload of ${file.name} failed (${response.status}).`
    try {
      const payload: unknown = await response.json()
      if (typeof payload === 'object' && payload !== null && 'detail' in payload) {
        message = detailToMessage((payload as { detail: unknown }).detail, message)
      }
    } catch {
      // Non-JSON error body; the status line is the best message available.
    }
    forgetTokenOn401(response.status)
    throw new ApiError(message, response.status)
  }

  const ticket = (await response.json()) as UploadTicket
  return ticket.key
}

export interface SubmitOptions {
  documentKey?: string
  diagramKey?: string
  title?: string
  /** Optional free text, offered only for a diagram-only submission. */
  context?: string
  /** Set to compare against an earlier review; routes to /reanalyze. */
  previousReviewId?: string
}

/**
 * Header carrying the reviewer's own OpenRouter key. Mirrors
 * `routes.OPENROUTER_KEY_HEADER`.
 *
 * A header and not a body field, matching the server: FastAPI's 422 handler
 * echoes a rejected body back, so a key sent in the body would come back in a
 * response the moment any neighbouring field was malformed.
 */
export const OPENROUTER_KEY_HEADER = 'X-OpenRouter-Key'

export async function submitReview(options: SubmitOptions): Promise<ReviewAccepted> {
  const body = JSON.stringify({
    document_key: options.documentKey ?? '',
    diagram_key: options.diagramKey ?? '',
    title: options.title ?? '',
    context: options.context ?? '',
  })
  const path = options.previousReviewId
    ? `/reviews/${encodeURIComponent(options.previousReviewId)}/reanalyze`
    : '/reviews'
  // Only this call sends it. The key buys model calls, and submitting a review
  // is the only thing that makes any — attaching it to polling or downloads
  // would put the credential on the wire repeatedly for no reason.
  const apiKey = getApiKey()
  return request<ReviewAccepted>(path, {
    method: 'POST',
    body,
    headers: apiKey ? { [OPENROUTER_KEY_HEADER]: apiKey } : {},
  })
}

export interface ReReviewOptions {
  /** Required. Free text: what the previous version got wrong, or what changed. */
  feedback: string
  /** Optional new SoW / solution document, already through POST /uploads. */
  documentKey?: string
  /** Optional new diagram or screenshot, already through POST /uploads. */
  diagramKey?: string
}

/**
 * Follow up on a review with feedback, optionally with a new attachment.
 *
 * Distinct from `submitReview({ previousReviewId })`, which posts to `/reanalyze`
 * and produces an unrelated review carrying a delta. This appends a VERSION to an
 * existing review's chain and can run on feedback alone.
 *
 * `reviewId` may be any member of the chain — the server builds the round on the
 * latest version, which is what stops repeated follow-ups on the original id from
 * producing competing v2s.
 *
 * Returns 202 with the NEW version's id; the round runs in the background and is
 * polled through `getStatus` exactly like a first review.
 */
export function reReview(
  reviewId: string,
  options: ReReviewOptions,
): Promise<ReviewAccepted> {
  const apiKey = getApiKey()
  return request<ReviewAccepted>(
    `/reviews/${encodeURIComponent(reviewId)}/re-review`,
    {
      method: 'POST',
      body: JSON.stringify({
        feedback: options.feedback,
        document_key: options.documentKey ?? '',
        diagram_key: options.diagramKey ?? '',
      }),
      // Same reasoning as submitReview: this is a call that spends model tokens,
      // and those are the only calls the reviewer's own key rides on.
      headers: apiKey ? { [OPENROUTER_KEY_HEADER]: apiKey } : {},
    },
  )
}

/** Every version of a review, oldest first. Answers from any member of the chain. */
export function getReviewVersions(reviewId: string): Promise<ReviewVersions> {
  return request<ReviewVersions>(
    `/reviews/${encodeURIComponent(reviewId)}/versions`,
  )
}

export function getStatus(reviewId: string): Promise<ReviewStatus> {
  return request<ReviewStatus>(`/reviews/${encodeURIComponent(reviewId)}/status`)
}

/**
 * Stop a running review.
 *
 * The server registers the cancellation and closes the connection to whatever call
 * is on the wire; it answers with the updated status, so the caller does not have to
 * poll once more to learn the click landed. A 409 means the review had already
 * finished — the click and the last poll crossed — which the caller treats as
 * "nothing to stop" rather than as a failure.
 */
export function cancelReview(reviewId: string): Promise<ReviewStatus> {
  return request<ReviewStatus>(`/reviews/${encodeURIComponent(reviewId)}/cancel`, {
    method: 'POST',
  })
}

export function getReview(reviewId: string): Promise<ReviewResult> {
  return request<ReviewResult>(`/reviews/${encodeURIComponent(reviewId)}`)
}

/** Past reviews, newest first. Read from stored data; no re-analysis. */
export function listReviews(): Promise<ReviewSummary[]> {
  return request<ReviewSummary[]>('/reviews')
}

/**
 * Mint the read-only share token for a completed review.
 *
 * Gated, unlike reading a shared review: issuing a link needs the demo token, so
 * a recipient cannot mint links for other reviews. Deterministic server-side, so
 * calling this twice hands back the same link.
 */
export function createShareLink(reviewId: string): Promise<ShareLink> {
  return request<ShareLink>(`/reviews/${encodeURIComponent(reviewId)}/share`)
}

/**
 * Read a shared review. This is the one call a recipient makes, and they have no
 * demo token — the `t` parameter is what authorises it. The server answers 404
 * for a wrong token, an unknown review, and a review whose file is gone after a
 * restart, all with the same message.
 */
export function getSharedReview(reviewId: string, token: string): Promise<SharedReview> {
  return request<SharedReview>(
    `/shared/${encodeURIComponent(reviewId)}?t=${encodeURIComponent(token)}`,
  )
}

/** The full URL to hand someone. Read back by `readShareParams` on load. */
export function shareUrl(link: ShareLink): string {
  const url = new URL(window.location.href)
  url.hash = ''
  url.search = `?share=${encodeURIComponent(link.review_id)}&t=${encodeURIComponent(link.token)}`
  return url.toString()
}

/**
 * The share parameters in the current URL, if this page load is a shared link.
 *
 * Query parameters rather than a path segment: the Vercel config rewrites every
 * path to index.html for the SPA, so a path would work but adds a routing
 * concept this app does not otherwise have — there is no router here, only a
 * phase in `App`.
 */
export function readShareParams(
  search: string = window.location.search,
): { reviewId: string; token: string } | null {
  const params = new URLSearchParams(search)
  const reviewId = params.get('share') ?? ''
  const token = params.get('t') ?? ''
  return reviewId && token ? { reviewId, token } : null
}

export interface ReportDownload {
  blob: Blob
  filename: string
}

/**
 * Fetch the PDF report as a blob.
 *
 * Deliberately not a plain `<a href>`: a link cannot report a failure, and a
 * dead download button that does nothing is exactly the silent failure this
 * module exists to prevent. The cost is buffering the file in memory, which is
 * fine for a report measured in tens of kilobytes.
 */
export async function downloadReport(reviewId: string): Promise<ReportDownload> {
  const path = `/reviews/${encodeURIComponent(reviewId)}/report.pdf`

  let response: Response
  try {
    response = await fetch(`${BASE_URL}${path}`, { headers: authHeaders() })
  } catch {
    throw new ApiError(
      `Cannot reach the API at ${BASE_URL}. Is the backend running?`,
      0,
    )
  }

  if (!response.ok) {
    let message = `Could not generate the report (${response.status}).`
    try {
      const body: unknown = await response.json()
      if (typeof body === 'object' && body !== null && 'detail' in body) {
        message = detailToMessage((body as { detail: unknown }).detail, message)
      }
    } catch {
      // A non-JSON error body; the status line is the best available message.
    }
    forgetTokenOn401(response.status)
    throw new ApiError(message, response.status)
  }

  return {
    blob: await response.blob(),
    filename: filenameFromDisposition(response.headers.get('Content-Disposition')),
  }
}

/** Read the server's chosen filename, falling back to a sane default. */
function filenameFromDisposition(header: string | null): string {
  const match = header?.match(/filename="([^"]+)"/)
  return match?.[1] ?? 'trust7-review.pdf'
}
