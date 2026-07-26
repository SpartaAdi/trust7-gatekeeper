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
    <div className="mx-auto max-w-2xl px-6 py-12">
      <header>
        <h2 className="text-2xl font-semibold tracking-tight">
          {previousReviewId ? 'Submit the revised design' : 'Submit a design for review'}
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-ink-muted">
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
          <label htmlFor="title" className="block text-sm font-medium text-ink">
            Title <span className="font-normal text-ink-muted">(optional)</span>
          </label>
          <input
            id="title"
            type="text"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            disabled={busy !== ''}
            placeholder="Derived from the filename if left blank"
            className="mt-2 w-full border border-hairline px-3 py-2 text-sm outline-none placeholder:text-ink-muted focus:border-minfy-orange disabled:opacity-60"
          />
        </div>

        {error && (
          <div
            role="alert"
            className="border-l-2 border-sev-high bg-surface-sunken px-4 py-3 text-sm text-ink"
          >
            <p className="font-medium text-sev-high">Submission failed</p>
            <p className="mt-1 text-ink-muted">{error}</p>
          </div>
        )}

        <div className="flex items-center gap-4 border-t border-hairline pt-6">
          <button
            type="submit"
            disabled={!canSubmit}
            className="bg-minfy-orange px-5 py-2.5 text-sm font-semibold text-white hover:bg-minfy-navy disabled:cursor-not-allowed disabled:bg-hairline disabled:text-ink-muted"
          >
            {busy === '' ? 'Start review' : busy}
          </button>

          {!canSubmit && busy === '' && (
            <p className="text-xs text-ink-muted">
              Add a document or a diagram to continue.
            </p>
          )}

          {previousReviewId && onCancelReReview && busy === '' && (
            <button
              type="button"
              onClick={onCancelReReview}
              className="ml-auto text-sm text-ink-muted underline underline-offset-2 hover:text-ink"
            >
              Back to results
            </button>
          )}
        </div>
      </form>
    </div>
  )
}
