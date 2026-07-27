import { useMemo, useState } from 'react'

import { ApiError, submitReview, uploadFile } from '../api'
import { DropZone, type StagedFile } from '../components/DropZone'
import { classify, isSupported, type FileKind } from '../fileKind'

interface Props {
  /** Set when re-reviewing; the new review is compared against this one. */
  previousReviewId?: string
  onStarted: (reviewId: string) => void
  onCancel?: () => void
}

export function UploadView({ previousReviewId, onStarted, onCancel }: Props) {
  const [staged, setStaged] = useState<StagedFile[]>([])
  const [name, setName] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const documents = staged.filter((s) => s.kind === 'document')
  const diagrams = staged.filter((s) => s.kind === 'diagram')
  const unresolved = staged.filter((s) => s.kind === 'unknown')

  const blocker = useMemo(() => {
    if (unresolved.length > 0) return 'Set a type for every file above.'
    if (documents.length === 0 && diagrams.length === 0) return 'Add your two files.'
    if (documents.length === 0) return 'Add a solution document.'
    if (diagrams.length === 0) return 'Add an architecture diagram.'
    if (documents.length > 1) return 'Only one solution document per review.'
    if (diagrams.length > 1) return 'Only one diagram per review.'
    return ''
  }, [documents.length, diagrams.length, unresolved.length])

  const canSubmit = blocker === '' && busy === ''
  // Falls back to the SoW filename, which is what the reviewer would have typed.
  const effectiveName = name.trim() || documents[0]?.file.name || ''

  function addFiles(incoming: File[]) {
    setError('')
    const rejected = incoming.filter((file) => !isSupported(file.name))
    if (rejected.length > 0) {
      setError(
        `Not a supported file type: ${rejected.map((f) => f.name).join(', ')}.`,
      )
    }
    const accepted = incoming.filter((file) => isSupported(file.name))
    setStaged((current) => [
      ...current,
      ...accepted.map((file) => ({
        id: `${file.name}-${file.size}-${current.length}-${Math.random()}`,
        file,
        kind: classify(file.name),
        autoDetected: true,
      })),
    ])
  }

  function reclassify(id: string, kind: FileKind) {
    setStaged((current) =>
      current.map((s) => (s.id === id ? { ...s, kind, autoDetected: false } : s)),
    )
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError('')

    const documentFile = documents[0]?.file
    const diagramFile = diagrams[0]?.file
    if (!documentFile || !diagramFile) return

    try {
      setBusy(`Uploading ${documentFile.name}…`)
      const documentKey = await uploadFile(documentFile)
      setBusy(`Uploading ${diagramFile.name}…`)
      const diagramKey = await uploadFile(diagramFile)

      setBusy('Submitting for review…')
      const accepted = await submitReview({
        documentKey,
        diagramKey,
        title: effectiveName,
        previousReviewId,
      })
      onStarted(accepted.review_id)
    } catch (caught) {
      setError(
        caught instanceof ApiError || caught instanceof Error
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
              A review needs one solution document and one architecture diagram.
              draw.io files are parsed directly; images are read with Claude vision.
            </>
          )}
        </p>
      </header>

      <form onSubmit={handleSubmit} className="mt-10 space-y-8">
        <DropZone
          files={staged}
          onAdd={addFiles}
          onRemove={(id) => setStaged((c) => c.filter((s) => s.id !== id))}
          onReclassify={reclassify}
          disabled={busy !== ''}
        />

        <div>
          <label htmlFor="review-name" className="t-heading block">
            Review name{' '}
            <span className="t-caption font-normal text-ink-muted">(optional)</span>
          </label>
          <input
            id="review-name"
            type="text"
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={busy !== ''}
            placeholder={documents[0]?.file.name ?? 'Defaults to the document filename'}
            className="t-body mt-2 w-full border border-hairline bg-surface px-3 py-2 transition-colors duration-150 placeholder:text-ink-faint hover:border-ink-faint focus:border-minfy-orange disabled:opacity-60"
          />
          {name.trim() === '' && documents[0] && (
            <p className="t-caption mt-1.5 text-ink-faint">
              Will be saved as “{documents[0].file.name}”.
            </p>
          )}
        </div>

        {error && (
          <div
            role="alert"
            className="animate-enter flex gap-3 border-l-2 border-sev-high bg-surface-sunken px-4 py-3.5"
          >
            <svg
              viewBox="0 0 16 16"
              aria-hidden="true"
              className="mt-0.5 size-4 shrink-0 fill-sev-high"
            >
              <path d="M8 1.5 L14.5 13.5 L1.5 13.5 Z" />
            </svg>
            <div className="min-w-0">
              <p className="t-heading text-sev-high">Submission failed</p>
              <p className="t-caption mt-1 break-words text-ink-muted">{error}</p>
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-4 border-t border-hairline pt-6">
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

          {blocker !== '' && busy === '' && (
            <p className="t-caption text-ink-faint">{blocker}</p>
          )}

          {onCancel && busy === '' && (
            <button
              type="button"
              onClick={onCancel}
              className="t-caption ml-auto text-ink-muted underline underline-offset-2 transition-colors hover:text-ink"
            >
              {previousReviewId ? 'Back to results' : 'Back to history'}
            </button>
          )}
        </div>
      </form>
    </div>
  )
}
