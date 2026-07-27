import { useState } from 'react'

import { ApiError, submitReview, uploadFile } from '../api'
import { FilePicker } from '../components/FilePicker'

const DOCUMENT_ACCEPT = '.pdf,.docx,.txt,.md,.rst,.csv,.json,.yaml,.yml'
const DIAGRAM_ACCEPT = '.drawio,.xml,.png,.jpg,.jpeg,.gif,.webp'

interface Props {
  /** Set when re-reviewing; the new review is compared against this one. */
  previousReviewId?: string
  onStarted: (reviewId: string) => void
  onCancelReReview?: () => void
}

export function UploadView({ previousReviewId, onStarted, onCancelReReview }: Props) {
  const [documentFile, setDocumentFile] = useState<File | null>(null)
  const [diagramFile, setDiagramFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const canSubmit = (documentFile !== null || diagramFile !== null) && busy === ''

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError('')

    try {
      let documentKey: string | undefined
      let diagramKey: string | undefined

      if (documentFile) {
        setBusy(`Uploading ${documentFile.name}…`)
        documentKey = await uploadFile(documentFile)
      }
      if (diagramFile) {
        setBusy(`Uploading ${diagramFile.name}…`)
        diagramKey = await uploadFile(diagramFile)
      }

      setBusy('Submitting for review…')
      const accepted = await submitReview({
        documentKey,
        diagramKey,
        title: title.trim(),
        previousReviewId,
      })
      onStarted(accepted.review_id)
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : caught instanceof Error
            ? caught.message
            : 'Something went wrong submitting the review.',
      )
      setBusy('')
    }
  }

  return (
    <div className="mx-auto max-w-2xl px-6 py-12 lg:py-16">
      <header>
        <h2 className="t-display">
          {previousReviewId ? 'Submit the revised design' : 'Submit a design for review'}
        </h2>
        <p className="t-body mt-3 max-w-prose text-ink-muted">
          {previousReviewId ? (
            <>
              This review will be scored against review{' '}
              <span className="font-mono text-xs">{previousReviewId}</span> and shown
              as a delta.
            </>
          ) : (
            <>
              Provide the solution document, the architecture diagram, or both.
              draw.io files are parsed directly; images are read with Claude vision.
            </>
          )}
        </p>
      </header>

      <form onSubmit={handleSubmit} className="mt-10 space-y-8">
        <FilePicker
          label="Solution document / SoW"
          hint="PDF, DOCX, or plain text. Optional if a diagram is supplied."
          accept={DOCUMENT_ACCEPT}
          file={documentFile}
          onChange={setDocumentFile}
          disabled={busy !== ''}
        />

        <FilePicker
          label="Architecture diagram"
          hint="draw.io (.drawio, .xml) parsed directly — no model call — or PNG/JPG/GIF/WebP read with Claude vision."
          accept={DIAGRAM_ACCEPT}
          file={diagramFile}
          onChange={setDiagramFile}
          disabled={busy !== ''}
        />

        <div>
          <label htmlFor="title" className="t-heading block">
            Title <span className="t-caption font-normal text-ink-muted">(optional)</span>
          </label>
          <input
            id="title"
            type="text"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            disabled={busy !== ''}
            placeholder="Derived from the filename if left blank"
            className="t-body mt-2 w-full border border-hairline bg-surface px-3 py-2 transition-colors duration-150 placeholder:text-ink-faint hover:border-ink-faint focus:border-minfy-orange disabled:opacity-60"
          />
        </div>

        {error && (
          <div
            role="alert"
            className="animate-enter flex gap-3 border-l-2 border-sev-high bg-surface-sunken px-4 py-3.5"
          >
            <svg viewBox="0 0 16 16" aria-hidden="true" className="mt-0.5 size-4 shrink-0 fill-sev-high">
              <path d="M8 1.5 L14.5 13.5 L1.5 13.5 Z" />
            </svg>
            <div className="min-w-0">
              <p className="t-heading text-sev-high">Submission failed</p>
              <p className="t-caption mt-1 break-words text-ink-muted">{error}</p>
            </div>
          </div>
        )}

        <div className="flex items-center gap-4 border-t border-hairline pt-6">
          <button
            type="submit"
            disabled={!canSubmit}
            className="t-body inline-flex items-center gap-2 bg-minfy-orange px-5 py-2.5 font-semibold text-white transition-colors duration-150 hover:bg-minfy-navy disabled:cursor-not-allowed disabled:bg-hairline disabled:text-ink-faint"
          >
            {busy !== '' && (
              <span
                aria-hidden="true"
                className="size-3 animate-spin rounded-full border-2 border-white/40 border-t-white"
              />
            )}
            {busy === '' ? 'Start review' : busy}
          </button>

          {!canSubmit && busy === '' && (
            <p className="t-caption text-ink-faint">
              Add a document or a diagram to continue.
            </p>
          )}

          {previousReviewId && onCancelReReview && busy === '' && (
            <button
              type="button"
              onClick={onCancelReReview}
              className="t-caption ml-auto text-ink-muted underline underline-offset-2 transition-colors hover:text-ink"
            >
              Back to results
            </button>
          )}
        </div>
      </form>
    </div>
  )
}
