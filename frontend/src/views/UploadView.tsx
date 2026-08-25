import { useMemo, useState } from 'react'

import { ApiError, submitReview, uploadFile } from '../api'
import { getApiKey, maskApiKey, setApiKey } from '../apiKey'
import { DropZone, type StagedFile } from '../components/DropZone'
import { classify, isSupported, type FileKind } from '../fileKind'
import { useDictation } from '../useDictation'

/** Mirrors MAX_CONTEXT_CHARS in backend/schema.py, which truncates server-side. */
const MAX_CONTEXT_CHARS = 1000

interface Props {
  /** Set when re-reviewing; the new review is compared against this one. */
  previousReviewId?: string
  /**
   * `startedAt` is the instant the submission began, not the instant the review was
   * accepted — the uploads happen first and can take seconds on a slow connection.
   * The elapsed clock is only honest if it counts from here.
   */
  onStarted: (reviewId: string, startedAt: number) => void
  onCancel?: () => void
}

export function UploadView({ previousReviewId, onStarted, onCancel }: Props) {
  const [staged, setStaged] = useState<StagedFile[]>([])
  const [name, setName] = useState('')
  const [context, setContext] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const documents = staged.filter((s) => s.kind === 'document')
  const diagrams = staged.filter((s) => s.kind === 'diagram')
  const unresolved = staged.filter((s) => s.kind === 'unknown')

  /**
   * Either input is sufficient, matching the backend: `normalize.ingest` only
   * rejects a submission when the document *and* the diagram are both empty. The
   * UI used to demand both, which refused submissions the API would have accepted.
   */
  const blocker = useMemo(() => {
    if (unresolved.length > 0) return 'Set a type for every file above.'
    if (documents.length === 0 && diagrams.length === 0) {
      return 'Add a solution document, an architecture diagram, or both.'
    }
    if (documents.length > 1) return 'Only one solution document per review.'
    if (diagrams.length > 1) return 'Only one diagram per review.'
    return ''
  }, [documents.length, diagrams.length, unresolved.length])

  const canSubmit = blocker === '' && busy === ''
  // Whichever file was supplied; a diagram-only review still needs a name.
  const primaryFile = documents[0]?.file ?? diagrams[0]?.file
  const effectiveName = name.trim() || primaryFile?.name || ''

  // A diagram alone is a valid review, but it cannot show process or governance
  // material, so the note below says what a document would add.
  const diagramOnly = diagrams.length > 0 && documents.length === 0

  /**
   * A diagram, and nothing written down anywhere — no SoW, and the context field
   * left empty.
   *
   * The note above already offers a document when a diagram arrives alone. This is
   * the narrower case where BOTH offers have been declined, and it is worth saying
   * separately because the consequence is specific rather than general: a whole class
   * of control lives in prose and nowhere else, so those checks are being submitted
   * with nothing to read. Purely derived from state that already exists — no new
   * field, nothing sent to the API, nothing that can reach the scoring arithmetic.
   */
  const noDescriptionAtAll = diagramOnly && context.trim() === ''

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
    // One is enough; only an empty submission is refused.
    if (!documentFile && !diagramFile) return

    const startedAt = Date.now()

    try {
      let documentKey = ''
      let diagramKey = ''
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
        title: effectiveName,
        // Only sent when the field was actually offered. A document-bearing
        // submission sends '' here exactly as it did before this field existed.
        context: diagramOnly ? context.trim() : '',
        previousReviewId,
      })
      onStarted(accepted.review_id, startedAt)
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
              A review needs a solution document, an architecture diagram, or
              both. draw.io files are parsed directly; images are read using AI
              vision.
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

        {/*
          Informational, not a warning: diagram-only is fully supported, so this
          uses the navy accent and no severity colour. It says what a document
          would add rather than implying anything is missing.
        */}
        {diagramOnly && (
          <p
            className="animate-enter t-caption border-l-2 border-minfy-navy bg-pastel-sky px-4 py-3 text-ink-muted"
            data-testid="diagram-only-note"
          >
            <span className="font-medium text-ink">
              Architecture diagram received.
            </span>{' '}
            Adding a solution document (SoW) as well will let the review also
            assess process, operational, and compliance aspects that aren’t
            visible in a diagram alone.
          </p>
        )}

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
            placeholder={primaryFile?.name ?? 'Defaults to the uploaded filename'}
            className="t-body mt-2 w-full border border-hairline bg-surface px-3 py-2 transition-colors duration-150 placeholder:text-ink-faint hover:border-ink-faint focus:border-minfy-indigo disabled:opacity-60"
          />
          {name.trim() === '' && primaryFile && (
            <p className="t-caption mt-1.5 text-ink-faint">
              Will be saved as “{primaryFile.name}”.
            </p>
          )}
        </div>

        {/*
          Offered only for a diagram-only submission. When a SoW is attached it
          already carries purpose and use case, and asking twice would invite
          contradicting answers for the review to reconcile.
        */}
        {diagramOnly && (
          <ContextField
            value={context}
            onChange={setContext}
            disabled={busy !== ''}
          />
        )}

        <ApiKeyField disabled={busy !== ''} />

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

        {/*
          Here, and not beside the drop zone, for two reasons. Its condition depends
          on the context field, which renders above this point — a warning that the
          field is empty has to come after the reader has met the field, or it
          describes something they have not seen. And it is a statement about what the
          run that is about to start will not be able to do, so it belongs at the
          moment of committing to it.

          In UploadView rather than as a results-page banner deliberately: here it is
          still actionable — attach a document, or type a sentence — and on the results
          page it would only be an explanation for work already paid for.

          Informational. It does not touch `canSubmit`, and a diagram-only review with
          no context remains a perfectly valid thing to run.
        */}
        {noDescriptionAtAll && (
          /*
            A plain notice rather than a CaveatPanel, matching its sibling above.
            CaveatPanel splits a title from an elaborating body, and this copy is one
            sentence that opens with its own title — routed through that component it
            would print "No accompanying description provided" twice. It still borrows
            the caution tone's colours, which are already in the contrast audit.
          */
          <p
            className="animate-enter t-caption border-l-2 border-sev-medium bg-surface-sunken px-4 py-3 text-ink-muted"
            data-testid="no-description-warning"
          >
            {"No accompanying description provided — controls described only in " +
              "prose (encryption, IAM, disaster recovery, etc.) can't be scored " +
              "from a diagram alone."}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-4 border-t border-hairline pt-6">
          <button
            type="submit"
            disabled={!canSubmit}
            className="t-body inline-flex items-center gap-2 bg-minfy-indigo px-5 py-2.5 font-semibold text-white transition-colors duration-150 hover:bg-minfy-blue disabled:cursor-not-allowed disabled:bg-hairline disabled:text-ink-faint"
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


/**
 * Optional free text, with dictation.
 *
 * The mic is absent rather than disabled where the Web Speech API is missing: a
 * button that cannot listen teaches the user nothing when it fails, and Firefox and
 * older Safari have no implementation at all.
 *
 * Dictated text is appended rather than replacing the field, so someone can type,
 * dictate, and type again without losing what came before.
 */
/**
 * Optional: spend your own OpenRouter credit instead of the server's.
 *
 * Collapsed by default. It is a power-user escape hatch, not part of the normal
 * path — the server's key is what the demo runs on, and putting a credential
 * field in front of every reviewer invites them to think one is required.
 *
 * `type="password"` and `autoComplete="off"`: the browser must not offer to save
 * this, and it must not be readable over a shoulder or in a screen share, which
 * is a realistic way to leak it during a demo.
 */
function ApiKeyField({ disabled }: { disabled: boolean }) {
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState(getApiKey())

  const update = (next: string) => {
    setValue(next)
    setApiKey(next)
  }

  return (
    <div className="border-t border-hairline pt-6" data-testid="api-key-field">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="t-caption flex items-center gap-1.5 text-ink-muted underline underline-offset-2 transition-colors hover:text-ink"
      >
        {open ? 'Hide' : 'Use my own OpenRouter key'}
        {!open && value !== '' && (
          <span className="font-mono text-ink-faint">({maskApiKey(value)})</span>
        )}
      </button>

      {open && (
        <div className="animate-enter mt-3">
          <label htmlFor="openrouter-key" className="t-heading block">
            OpenRouter API key{' '}
            <span className="t-caption font-normal text-ink-muted">(optional)</span>
          </label>
          <p className="t-caption mt-1 max-w-prose text-ink-muted">
            This review's model calls are billed to your key instead of the
            server's. Leave it empty to use the server's.
          </p>
          <input
            id="openrouter-key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={value}
            onChange={(event) => update(event.target.value)}
            disabled={disabled}
            placeholder="sk-or-v1-…"
            className="t-body mt-2 w-full border border-hairline bg-surface px-3 py-2 font-mono transition-colors duration-150 placeholder:text-ink-faint hover:border-ink-faint focus:border-minfy-indigo disabled:opacity-60"
          />
          <p className="t-caption mt-1.5 text-ink-faint">
            Held in this tab's memory only — never saved to the server or to your
            browser's storage, so a refresh clears it.{' '}
            {value !== '' && (
              <button
                type="button"
                onClick={() => update('')}
                className="underline underline-offset-2 transition-colors hover:text-ink"
              >
                Clear now
              </button>
            )}
          </p>
        </div>
      )}
    </div>
  )
}

function ContextField({
  value,
  onChange,
  disabled,
}: {
  value: string
  onChange: (next: string) => void
  disabled: boolean
}) {
  const { supported, listening, toggle } = useDictation((spoken) => {
    onChange(
      (value ? `${value.trimEnd()} ` : '') + spoken.trim(),
    )
  })

  const remaining = MAX_CONTEXT_CHARS - value.length

  return (
    // Tinted panel with an indigo rule, the same treatment the results view gives
    // the delta summary. This field was being missed entirely: on a diagram-only
    // submission it is the only place intent can come from, so it earns emphasis
    // that a plain label under a file list does not carry.
    <div
      data-testid="context-field"
      className="border-l-2 border-minfy-indigo bg-pastel-sky px-5 py-4.5"
    >
      <label htmlFor="review-context" className="t-heading block">
        Add more context — purpose and use case{' '}
        <span className="t-caption font-normal text-ink-muted">(optional)</span>
      </label>
      <p className="t-caption mt-1 max-w-prose text-ink-muted">
        A diagram shows structure, not intent. Anything here is read as part of the
        design, alongside it. <span className="font-semibold">Type it or say it</span> —
        the microphone dictates straight into the box.
      </p>

      <div className="mt-2 flex items-start gap-2">
        <textarea
          id="review-context"
          value={value}
          onChange={(event) => onChange(event.target.value.slice(0, MAX_CONTEXT_CHARS))}
          disabled={disabled}
          rows={4}
          maxLength={MAX_CONTEXT_CHARS}
          placeholder="What the system does, who uses it, and any constraints it has to meet — data residency, an audit obligation, a migration deadline."
          className="t-body min-w-0 flex-1 resize-y border border-hairline bg-surface px-3 py-2 transition-colors duration-150 placeholder:text-ink-faint hover:border-ink-faint focus:border-minfy-indigo disabled:opacity-60"
        />

        {/*
          Filled indigo at rest rather than a hairline outline, and larger, because
          at 40px with a grey border it read as a decorative box rather than a
          control. `group` drives the hover popover below it; `title` stays so the
          same wording reaches a touch device holding the button, which has no
          hover state to show the popover with.
        */}
        {supported && (
          <span className="group relative shrink-0">
            <button
              type="button"
              onClick={toggle}
              disabled={disabled}
              aria-pressed={listening}
              aria-label={
                listening
                  ? 'Stop dictating'
                  : 'Speak out your purpose and use case'
              }
              /* Follows the state, like the accessible name already did. A static
                 "Speak out your purpose" tip over a stop square contradicts the
                 glyph — the same fix FeedbackBox's mic already carries. */
              title={listening ? 'Stop dictating' : 'Speak out your purpose and use case'}
              /*
                Navy while listening, not a severity red: "recording" is a state, not
                a finding, and sev-high means one specific thing everywhere else in
                this app.

                But navy alone was the whole signal, and indigo #1420be to navy
                #1b263b is two dark blues — hard to separate side by side and
                impossible from memory. So the state now also gains a ring and, below,
                a different glyph. Colour is one of three carriers rather than the
                only one. This is the same finding, and the same remedy, as the mic in
                FeedbackBox.
              */
              className={`relative flex size-12 items-center justify-center transition-colors duration-150 disabled:opacity-60 ${
                listening
                  ? 'bg-minfy-navy text-white ring-2 ring-minfy-navy ring-offset-2 ring-offset-pastel-sky'
                  : 'bg-minfy-indigo text-white hover:bg-minfy-blue'
              }`}
            >
              {/*
                A stop square while listening, the mic otherwise. The glyph is the
                second, non-colour carrier of the state, and it says what the next
                press DOES — which is what the accessible name says, so the two agree.
              */}
              {listening ? (
                <svg viewBox="0 0 16 16" aria-hidden="true" className="size-4 fill-current">
                  <rect x="3" y="3" width="10" height="10" rx="1" />
                </svg>
              ) : (
                <svg viewBox="0 0 16 16" aria-hidden="true" className="size-5 fill-current">
                  <path d="M8 1.5a2 2 0 0 1 2 2v4a2 2 0 0 1-4 0v-4a2 2 0 0 1 2-2Z" />
                  <path d="M4 7a.75.75 0 0 1 1.5 0 2.5 2.5 0 0 0 5 0A.75.75 0 0 1 12 7a4 4 0 0 1-3.25 3.93v1.32h1.75a.75.75 0 0 1 0 1.5h-5a.75.75 0 0 1 0-1.5h1.75v-1.32A4 4 0 0 1 4 7Z" />
                </svg>
              )}
            </button>
            {/* Visual only: the button's aria-label already carries this wording,
                so announcing it again would read the same sentence twice. */}
            <span
              aria-hidden="true"
              data-testid="mic-tooltip"
              className="pointer-events-none absolute right-0 top-full z-10 mt-1.5 w-max max-w-[13rem] bg-minfy-navy px-2.5 py-1.5 text-right text-[0.75rem] leading-snug text-white opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100"
            >
              {listening ? 'Stop dictating' : 'Speak out your purpose and use case'}
            </span>
          </span>
        )}
      </div>

      {/*
        Two nodes, and the split is the point — the same correction FeedbackBox
        already carries. These shared ONE `aria-live` region, so `remaining` changed
        inside it on every keystroke and a screen reader announced "3994 characters
        left" after every letter typed. The live region was firing on the one thing
        nobody needs told, and the dictation state — the thing a blind user cannot
        otherwise perceive — was buried in the same noise.

        The dictation status keeps the live region: it changes on a deliberate press.
        The counter is now plain text. It is visible, it is reachable, it is not news.
      */}
      <div className="mt-1.5 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p
          className="t-caption font-medium text-ink"
          aria-live="polite"
          data-testid="upload-dictation-status"
        >
          {listening ? 'Listening — speak, then press stop when you are done.' : ''}
        </p>
        <p className="t-caption tnum ml-auto text-[0.75rem] text-ink-muted">
          {remaining} characters left.
        </p>
      </div>
    </div>
  )
}
