/**
 * The only module that talks to the API.
 *
 * Every call throws `ApiError` on failure — no call returns a partial or empty
 * result on error, so views can't silently render nothing.
 */

import type {
  ReviewAccepted,
  ReviewResult,
  ReviewStatus,
  UploadTicket,
} from './types'

const BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'
).replace(/\/+$/, '')

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
      headers: { 'Content-Type': 'application/json', ...init?.headers },
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
    throw new ApiError(message, response.status)
  }

  return (await response.json()) as T
}

/**
 * Content type for a file, used for BOTH the presign request and the PUT.
 *
 * The presigned URL is signed over the content type, so the two must match
 * exactly or S3 rejects the upload with a signature error. Browsers report an
 * empty type for `.drawio`, hence the extension fallback.
 */
export function contentTypeFor(file: File): string {
  if (file.type) return file.type
  const extension = file.name.toLowerCase().split('.').pop() ?? ''
  const byExtension: Record<string, string> = {
    drawio: 'application/xml',
    xml: 'application/xml',
    md: 'text/markdown',
    txt: 'text/plain',
    json: 'application/json',
    yaml: 'application/yaml',
    yml: 'application/yaml',
  }
  return byExtension[extension] ?? 'application/octet-stream'
}

/** Presign, then PUT straight to S3. Returns the object key to submit. */
export async function uploadFile(file: File): Promise<string> {
  const contentType = contentTypeFor(file)

  const ticket = await request<UploadTicket>('/uploads', {
    method: 'POST',
    body: JSON.stringify({ filename: file.name, content_type: contentType }),
  })

  let response: Response
  try {
    response = await fetch(ticket.upload_url, {
      method: 'PUT',
      headers: { 'Content-Type': contentType },
      body: file,
    })
  } catch {
    throw new ApiError(`Upload of ${file.name} failed: the request never completed.`, 0)
  }
  if (!response.ok) {
    throw new ApiError(
      `Upload of ${file.name} was rejected by storage (${response.status}).`,
      response.status,
    )
  }

  return ticket.key
}

export interface SubmitOptions {
  documentKey?: string
  diagramKey?: string
  title?: string
  /** Set to compare against an earlier review; routes to /reanalyze. */
  previousReviewId?: string
}

export async function submitReview(options: SubmitOptions): Promise<ReviewAccepted> {
  const body = JSON.stringify({
    document_key: options.documentKey ?? '',
    diagram_key: options.diagramKey ?? '',
    title: options.title ?? '',
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
