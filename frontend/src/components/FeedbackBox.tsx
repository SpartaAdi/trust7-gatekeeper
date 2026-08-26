import { useState } from 'react'

import { ApiError, reReview, uploadFile } from '../api'
import { classify, isSupported, type FileKind } from '../fileKind'
import { useDictation } from '../useDictation'
import { DropZone, type StagedFile } from './DropZone'

/**
 * Follow up on a completed review: tell it what it got wrong, optionally attach a
 * revised design, and get a new VERSION rather than an overwritten one.
 *
 * Everything here is wiring. The endpoint, the versioning, the ingest gates and the
 * fencing are all built and live; this component's whole job is to collect two
 * inputs and post them.
 *
 * ## What is reused rather than rebuilt
 *
 * - `DropZone` and `fileKind` — the same staging, the same extension allowlist, the
 *   same document/diagram disambiguation as the original upload. Attaching a file
 *   here goes through `POST /uploads`, so it meets the same signature, size and type
 *   gates; there is deliberately no second, weaker validation path.
 * - `useDictation` — the Web Speech API hook the context field already uses. Browser
 *   only: no request, no dependency, and no backend involvement.
 * - The panel treatment (indigo rule on a tinted block) is the context field's, so
 *   this reads as the same kind of thing: a place where the reviewer's own words go
 *   into the review.
 *
 * ## Why voice appends rather than replaces
 *
 * Both inputs write the same string, so a reviewer can dictate a paragraph and then
 * fix a word by hand, or type a sentence and speak the rest. Dictation appends to
 * what is already there and never clears it — someone who has typed three sentences
 * and then presses the mic is adding a fourth, not discarding three.
 */

/**
 * Mirrors MAX_FEEDBACK_CHARS in backend/schema.py, which is enforced server-side.
 *
 * Exported so `OpenQuestions` measures against the SAME number rather than keeping
 * a second copy — two mirrors of one server constant is one of them going stale.
 */
export const MAX_FEEDBACK_CHARS = 16000

export function FeedbackBox({
  reviewId,
  onStarted,
}: {
  /** Any member of the chain — the server builds the round on the latest version. */
  reviewId: string
  /**
   * `startedAt` is the instant the submission began, not the instant the round was
   * accepted: an attachment is uploaded first and that can take seconds. The
   * elapsed clock is only honest if it counts from here.
   */
  onStarted: (newReviewId: string, startedAt: number) => void
}) {
  const [feedback, setFeedback] = useState('')
  const [staged, setStaged] = useState<StagedFile[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const { supported, listening, toggle } = useDictation((spoken) => {
    setFeedback((current) =>
      ((current ? `${current.trimEnd()} ` : '') + spoken.trim()).slice(
        0,
        MAX_FEEDBACK_CHARS,
      ),
    )
  })

  const documents = staged.filter((s) => s.kind === 'document')
  const diagrams = staged.filter((s) => s.kind === 'diagram')
  const unresolved = staged.filter((s) => s.kind === 'unknown')

  // Trimmed, matching the server: `feedback` is `strip_whitespace` before
  // `min_length=1`, so a field holding three spaces is empty to the API and must
  // look empty here too rather than posting and coming back 422.
  const said = feedback.trim()

  const blocker =
    said === ''
      ? 'Say what to look at again.'
      : unresolved.length > 0
        ? 'Set a type for every file above.'
        : documents.length > 1
          ? 'Only one solution document per round.'
          : diagrams.length > 1
            ? 'Only one diagram per round.'
            : ''

  function addFiles(incoming: File[]) {
    setError('')
    const rejected = incoming.filter((file) => !isSupported(file.name))
    if (rejected.length > 0) {
      setError(`Not a supported file type: ${rejected.map((f) => f.name).join(', ')}.`)
    }
    setStaged((current) => [
      ...current,
      ...incoming
        .filter((file) => isSupported(file.name))
        .map((file) => ({
          id: `${file.name}-${file.size}-${current.length}-${Math.random()}`,
          file,
          kind: classify(file.name),
          autoDetected: true,
        })),
    ])
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError('')
    if (blocker !== '' || busy !== '') return

    const startedAt = Date.now()
    const documentFile = documents[0]?.file
    const diagramFile = diagrams[0]?.file

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

      setBusy('Starting the follow-up…')
      const accepted = await reReview(reviewId, {
        feedback: said,
        documentKey,
        diagramKey,
      })
      onStarted(accepted.review_id, startedAt)
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : 'The follow-up could not be started.',
      )
      setBusy('')
    }
  }

  const remaining = MAX_FEEDBACK_CHARS - feedback.length

  return (
    <form
      onSubmit={handleSubmit}
      data-testid="feedback-box"
      className="mt-8 border-l-2 border-minfy-indigo bg-pastel-sky px-5 py-4.5"
    >
      <h3 className="t-heading">
        Follow up on this review{' '}
        <span className="t-caption font-normal text-ink-muted">
          — correct it, or submit a revision
        </span>
      </h3>
      <p className="t-caption mt-1 max-w-prose text-ink-muted">
        Tell it what it read wrong, or what has changed since. This is re-evaluated
        as a new version — the review above is kept and stays open at its own link.{' '}
        <span className="font-semibold">Type it or say it.</span>
      </p>

      <div className="mt-2 flex items-start gap-2">
        <textarea
          id="review-feedback"
          value={feedback}
          onChange={(event) =>
            setFeedback(event.target.value.slice(0, MAX_FEEDBACK_CHARS))
          }
          disabled={busy !== ''}
          rows={4}
          maxLength={MAX_FEEDBACK_CHARS}
          aria-label="What this review got wrong, or what has changed"
          placeholder="The orders table is encrypted with a customer-managed key — see section 4 of the SoW. And the queue now has a dead-letter queue after three attempts."
          /*
            `border-ink-faint`, not `border-hairline`. The audit pass over this surface
            measured it: hairline is 1.38:1 against the sky fill outside the field and
            1.52:1 against the white inside it, so the only boundary of the primary
            input on this panel was invisible by WCAG 1.4.11's 3:1 measure. ink-faint is
            5.23:1. Same class of miss as the version chips.
          */
          className="t-body min-w-0 flex-1 resize-y border border-ink-faint bg-surface px-3 py-2 transition-colors duration-150 placeholder:text-ink-faint hover:border-ink focus:border-minfy-indigo disabled:opacity-60"
        />

        {/* Same control as the context field's, including the hover popover, so the
            mic means one thing in this app rather than two similar things. */}
        {supported && (
          <span className="group relative shrink-0">
            <button
              type="button"
              onClick={toggle}
              disabled={busy !== ''}
              aria-pressed={listening}
              aria-label={listening ? 'Stop dictating' : 'Speak your feedback'}
              /* Follows the state, like the accessible name already did. A static
                 "Speak your feedback" tip over a stop square contradicts the glyph. */
              title={listening ? 'Stop dictating' : 'Speak your feedback'}
              /*
                The listening state used to be carried by fill alone — indigo #1420be
                to navy #1b263b, two dark blues that are hard to tell apart side by
                side and impossible to tell apart from memory. It now also gains a ring
                and a filled dot, so the state survives a reader who cannot separate
                those two hues and one who never saw the other state.
              */
              className={`relative flex size-12 items-center justify-center transition-colors duration-150 disabled:opacity-60 ${
                listening
                  ? 'bg-minfy-navy text-white ring-2 ring-minfy-navy ring-offset-2 ring-offset-pastel-sky'
                  : 'bg-minfy-indigo text-white hover:bg-minfy-blue'
              }`}
            >
              {/*
                A stop square while listening, the mic otherwise. The glyph is the
                second, non-colour carrier of the state and it also says what the next
                press DOES, which is what the accessible name already says — the two
                now agree. `minfy-yellow` was the other candidate for a recording dot
                and is deliberately not used: index.css reserves it for the logo mark
                and forbids it as a status colour.
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
            <span
              aria-hidden="true"
              data-testid="feedback-mic-tooltip"
              className="t-caption pointer-events-none absolute right-0 top-full z-10 mt-1.5 w-max max-w-[13rem] bg-minfy-navy px-2.5 py-1.5 text-right leading-snug text-white opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100"
            >
              {listening ? 'Stop dictating' : 'Speak your feedback'}
            </span>
          </span>
        )}
      </div>

      {/*
        Two nodes, and the split is the point. Both statuses shared one `aria-live`
        region, so the character count changed inside it on every keystroke and a screen
        reader announced "3994 characters left" after every letter typed — the live
        region was firing on the one thing nobody needs told.

        The dictation status keeps the live region (it changes on a deliberate press, and
        it is the state a blind user cannot otherwise perceive). The counter is now plain
        text: it is visible, it is reachable, and it is not news.
      */}
      <div className="mt-2 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p
          className="t-caption font-medium text-ink"
          aria-live="polite"
          data-testid="feedback-dictation-status"
        >
          {listening ? 'Please speak now.' : ''}
        </p>
        <p className="t-caption tnum ml-auto text-ink-muted">{remaining} characters left.</p>
      </div>

      {/*
        Optional, and behind a disclosure because most rounds are words alone —
        that is the case the endpoint was built to support. An always-open drop
        zone would imply a file is expected.
      */}
      <details className="mt-4 border-t border-ink/10 pt-3.5" data-testid="feedback-attachment">
        {/* `font-medium` and a hairline above it: the disclosure sits between the
            counter row and the submit button, and at plain caption weight on a tinted
            fill it read as a third line of fine print rather than as a control. */}
        <summary className="t-caption cursor-pointer font-medium text-minfy-indigo underline underline-offset-2 transition-colors hover:text-minfy-blue">
          Attach a revised document or diagram (optional)
        </summary>
        <p className="t-caption mt-2.5 max-w-prose text-ink-muted">
          A new attachment REPLACES the design this review was scored against; the
          old one is carried through only as context for what changed. Same file
          types and same checks as the original upload.
        </p>
        <div className="mt-2">
          <DropZone
            files={staged}
            onAdd={addFiles}
            onRemove={(id) => setStaged((c) => c.filter((s) => s.id !== id))}
            onReclassify={(id, kind: FileKind) =>
              setStaged((c) =>
                c.map((s) => (s.id === id ? { ...s, kind, autoDetected: false } : s)),
              )
            }
            disabled={busy !== ''}
          />
        </div>
      </details>

      {error !== '' && (
        <p role="alert" className="t-caption mt-3 text-sev-high">
          {error}
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
        <button
          type="submit"
          disabled={blocker !== '' || busy !== ''}
          className="t-body bg-minfy-indigo px-5 py-2.5 font-semibold text-white transition-colors duration-150 hover:bg-minfy-blue disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy !== '' ? busy : 'Re-review with this feedback'}
        </button>
        {/* The reason the button is off, in the same place every time. Silent
            disabling is the failure mode this avoids. */}
        {busy === '' && blocker !== '' && (
          <p className="t-caption text-ink-muted">{blocker}</p>
        )}
      </div>
    </form>
  )
}
