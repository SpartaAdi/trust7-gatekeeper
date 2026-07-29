/**
 * The only module that talks to the API.
 *
 * Every call throws `ApiError` on failure — no call returns a partial or empty
 * result on error, so views can't silently render nothing.
 */

import { clearToken, getToken } from './token'
import type {
  ReviewAccepted,
  ReviewResult,
  ReviewStatus,
  ReviewSummary,
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
  return request<ReviewAccepted>(path, { method: 'POST', body })
}

export function getStatus(reviewId: string): Promise<ReviewStatus> {
  return request<ReviewStatus>(`/reviews/${encodeURIComponent(reviewId)}/status`)
}

export function getReview(reviewId: string): Promise<ReviewResult> {
  return request<ReviewResult>(`/reviews/${encodeURIComponent(reviewId)}`)
}

/** Past reviews, newest first. Read from stored data; no re-analysis. */
export function listReviews(): Promise<ReviewSummary[]> {
  return request<ReviewSummary[]>('/reviews')
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
