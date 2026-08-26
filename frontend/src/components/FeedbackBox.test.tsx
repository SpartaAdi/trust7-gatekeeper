import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FeedbackBox, MAX_FEEDBACK_CHARS } from './FeedbackBox'

/**
 * The follow-up box: two inputs and a POST.
 *
 * The backend half — versioning, the ingest gates, the fencing, the "feedback is a
 * pointer not evidence" rule — is built and tested elsewhere. What can go wrong
 * HERE is different and mostly about not lying to the user: posting whitespace as if
 * it were feedback, silently disabling the button, letting dictation destroy typed
 * text, or inventing a second file-validation path that disagrees with the one the
 * original upload uses.
 *
 * So these assert the wiring and the refusals, not the review.
 */

const { reReview, uploadFile } = vi.hoisted(() => ({
  reReview: vi.fn(),
  uploadFile: vi.fn(),
}))

vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {},
  reReview,
  uploadFile,
}))

/** Install or remove a Web Speech API stub before the component mounts. */
function setSpeechSupport(supported: boolean) {
  const scope = window as unknown as Record<string, unknown>
  if (!supported) {
    delete scope['SpeechRecognition']
    delete scope['webkitSpeechRecognition']
    return null
  }
  const instances: FakeRecognition[] = []
  class FakeRecognition {
    continuous = false
    interimResults = true
    lang = ''
    onresult: ((event: unknown) => void) | null = null
    onerror: (() => void) | null = null
    onend: (() => void) | null = null
    constructor() {
      instances.push(this)
    }
    start() {}
    stop() {
      this.onend?.()
    }
    /** Deliver a finalised transcript, the shape useDictation reads. */
    say(text: string) {
      this.onresult?.({
        resultIndex: 0,
        results: { length: 1, 0: { length: 1, isFinal: true, 0: { transcript: text } } },
      })
    }
  }
  scope['SpeechRecognition'] = FakeRecognition
  return instances
}

function mount(onStarted = vi.fn()) {
  render(<FeedbackBox reviewId="rev-original" onStarted={onStarted} />)
  return onStarted
}

const box = () => screen.getByTestId('feedback-box')
const submit = () => screen.getByRole('button', { name: /Re-review with this feedback/i })
const field = () =>
  screen.getByLabelText(/What this review got wrong, or what has changed/i)

beforeEach(() => {
  reReview.mockReset()
  uploadFile.mockReset()
  reReview.mockResolvedValue({
    review_id: 'rev-v2',
    status_url: '/reviews/rev-v2/status',
    result_url: '/reviews/rev-v2',
  })
  setSpeechSupport(false)
})

afterEach(() => setSpeechSupport(false))

describe('FeedbackBox — the required text', () => {
  it('will not submit with nothing typed, and says why', async () => {
    mount()
    expect(submit()).toBeDisabled()
    expect(box()).toHaveTextContent('Say what to look at again.')
    expect(reReview).not.toHaveBeenCalled()
  })

  it('treats whitespace as empty, matching the server', async () => {
    // `feedback` is strip_whitespace BEFORE min_length=1 server-side, so "   " is
    // empty to the API. Enabling the button here would post and come back 422.
    const user = userEvent.setup()
    mount()
    await user.type(field(), '    ')
    expect(submit()).toBeDisabled()
  })

  it('enables once there is something to act on', async () => {
    const user = userEvent.setup()
    mount()
    await user.type(field(), 'The orders table IS encrypted — section 4.')
    expect(submit()).toBeEnabled()
    expect(box()).not.toHaveTextContent('Say what to look at again.')
  })

  it('posts the trimmed feedback to the existing endpoint and hands back the new id', async () => {
    const user = userEvent.setup()
    const onStarted = mount()

    await user.type(field(), '  The queue has a DLQ after three attempts.  ')
    await user.click(submit())

    await waitFor(() => expect(reReview).toHaveBeenCalledTimes(1))
    expect(reReview).toHaveBeenCalledWith('rev-original', {
      feedback: 'The queue has a DLQ after three attempts.',
      documentKey: '',
      diagramKey: '',
    })
    // No attachment, so nothing was uploaded — a feedback-only round must not
    // touch /uploads at all.
    expect(uploadFile).not.toHaveBeenCalled()
    expect(onStarted).toHaveBeenCalledWith('rev-v2', expect.any(Number))
  })

  it('counts down the remaining characters against the server cap', async () => {
    const user = userEvent.setup()
    mount()
    // Derived from the constant, not written out: this counted 4000 until the
    // Open Questions view needed the cap raised, and a hardcoded pair of numbers
    // is two more things to remember on the next raise.
    expect(box()).toHaveTextContent(`${MAX_FEEDBACK_CHARS} characters left.`)
    await user.type(field(), 'abcde')
    expect(box()).toHaveTextContent(`${MAX_FEEDBACK_CHARS - 5} characters left.`)
  })
})

describe('FeedbackBox — dictation', () => {
  it('renders no microphone when the browser has no Web Speech API', () => {
    setSpeechSupport(false)
    mount()
    // A mic that cannot listen is worse than none: the user presses it and learns
    // nothing about why nothing happened.
    expect(screen.queryByRole('button', { name: /Speak your feedback/i })).toBeNull()
  })

  it('dictates into the same field the keyboard writes to', async () => {
    const instances = setSpeechSupport(true)!
    const user = userEvent.setup()
    mount()

    await user.click(screen.getByRole('button', { name: /Speak your feedback/i }))
    instances[0]!.say('the residency constraint is contractual')

    await waitFor(() =>
      expect(field()).toHaveValue('the residency constraint is contractual'),
    )
  })

  it('appends to typed text rather than replacing it', async () => {
    // Someone who has typed two sentences and then presses the mic is adding a
    // third, not discarding two.
    const instances = setSpeechSupport(true)!
    const user = userEvent.setup()
    mount()

    await user.type(field(), 'Encryption is specified.')
    await user.click(screen.getByRole('button', { name: /Speak your feedback/i }))
    instances[0]!.say('And the DLQ exists.')

    await waitFor(() =>
      expect(field()).toHaveValue('Encryption is specified. And the DLQ exists.'),
    )
  })

  it('stays editable after dictating, so a misheard word can be fixed', async () => {
    const instances = setSpeechSupport(true)!
    const user = userEvent.setup()
    mount()

    await user.click(screen.getByRole('button', { name: /Speak your feedback/i }))
    instances[0]!.say('the cue has a DLQ')
    await waitFor(() => expect(field()).toHaveValue('the cue has a DLQ'))

    await user.clear(field())
    await user.type(field(), 'the queue has a DLQ')
    expect(field()).toHaveValue('the queue has a DLQ')
  })

  it('reports that it is listening, and is a toggle', async () => {
    setSpeechSupport(true)
    const user = userEvent.setup()
    mount()

    const mic = screen.getByRole('button', { name: /Speak your feedback/i })
    expect(mic).toHaveAttribute('aria-pressed', 'false')

    await user.click(mic)
    expect(screen.getByRole('button', { name: /Stop dictating/i })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    // Matches UploadView's mic, which has said this since 972817f.
    expect(box()).toHaveTextContent('Please speak now.')

    await user.click(screen.getByRole('button', { name: /Stop dictating/i }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Speak your feedback/i })).toHaveAttribute(
        'aria-pressed',
        'false',
      ),
    )
  })

  it('dictated text alone is enough to submit', async () => {
    const instances = setSpeechSupport(true)!
    const user = userEvent.setup()
    mount()

    expect(submit()).toBeDisabled()
    await user.click(screen.getByRole('button', { name: /Speak your feedback/i }))
    instances[0]!.say('Please re-check the encryption finding.')

    await waitFor(() => expect(submit()).toBeEnabled())
    await user.click(submit())
    await waitFor(() =>
      expect(reReview).toHaveBeenCalledWith(
        'rev-original',
        expect.objectContaining({ feedback: 'Please re-check the encryption finding.' }),
      ),
    )
  })
})

describe('FeedbackBox — the optional attachment', () => {
  const file = (name: string, type = 'text/plain') =>
    new File(['# revised design\n'], name, { type })

  async function attach(user: ReturnType<typeof userEvent.setup>, ...files: File[]) {
    await user.click(screen.getByText(/Attach a revised document or diagram/i))
    const input = box().querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(input, files)
  }

  /**
   * Drop, rather than the file input, for anything the input's `accept` list
   * excludes: testing-library's `upload` honours `accept` and silently discards a
   * non-matching file, so the rejection path is only reachable the way a real user
   * reaches it — by dragging a file the picker would never have offered.
   */
  async function drop(user: ReturnType<typeof userEvent.setup>, ...files: File[]) {
    await user.click(screen.getByText(/Attach a revised document or diagram/i))
    const zone = box().querySelector('input[type="file"]')!.closest('div')!
      .parentElement!.firstElementChild as HTMLElement
    fireEvent.drop(zone, { dataTransfer: { files, types: ['Files'] } })
  }

  it('is closed by default — most rounds are words alone', () => {
    mount()
    const details = screen.getByTestId('feedback-attachment') as HTMLDetailsElement
    expect(details.open).toBe(false)
  })

  it('uploads through the shared path and posts the returned key', async () => {
    // The same POST /uploads the original submission uses, which is what puts this
    // through the same extension, size and content-signature gates. There is
    // deliberately no second validation path here.
    const user = userEvent.setup()
    uploadFile.mockResolvedValue('uploads/abc/revised.txt')
    mount()

    await user.type(field(), 'Here is the revised SoW.')
    await attach(user, file('revised.txt'))
    await user.click(submit())

    await waitFor(() => expect(uploadFile).toHaveBeenCalledTimes(1))
    expect(uploadFile.mock.calls[0]![0].name).toBe('revised.txt')
    expect(reReview).toHaveBeenCalledWith('rev-original', {
      feedback: 'Here is the revised SoW.',
      documentKey: 'uploads/abc/revised.txt',
      diagramKey: '',
    })
  })

  it('routes a diagram to diagram_key, not document_key', async () => {
    const user = userEvent.setup()
    uploadFile.mockResolvedValue('uploads/def/v2.drawio')
    mount()

    await user.type(field(), 'New diagram attached.')
    await attach(user, file('v2.drawio', 'application/xml'))
    await user.click(submit())

    await waitFor(() =>
      expect(reReview).toHaveBeenCalledWith('rev-original', {
        feedback: 'New diagram attached.',
        documentKey: '',
        diagramKey: 'uploads/def/v2.drawio',
      }),
    )
  })

  it('refuses an unsupported file type with the same allowlist as the upload step', async () => {
    const user = userEvent.setup()
    mount()

    await user.type(field(), 'See attached.')
    await drop(user, file('payload.exe', 'application/octet-stream'))

    expect(box()).toHaveTextContent(/Not a supported file type: payload\.exe/)
    // Refused before it was staged, so submitting sends no key for it.
    await user.click(submit())
    await waitFor(() => expect(reReview).toHaveBeenCalled())
    expect(uploadFile).not.toHaveBeenCalled()
  })

  it('asks which slot an ambiguous extension belongs in, as the upload step does', async () => {
    // `.md` is usually a SoW and could be an exported diagram, so fileKind returns
    // `unknown` and the UI asks rather than guessing. Guessing wrong sends a
    // document down the vision path and the review comes back nonsense.
    const user = userEvent.setup()
    mount()

    await user.type(field(), 'Attached the revised SoW.')
    await attach(user, file('revised.md', 'text/markdown'))

    expect(submit()).toBeDisabled()
    expect(box()).toHaveTextContent('Set a type for every file above.')
  })

  it('blocks a second document rather than silently picking one', async () => {
    const user = userEvent.setup()
    mount()

    await user.type(field(), 'Two SoWs by mistake.')
    await attach(user, file('a.txt'), file('b.txt'))

    expect(submit()).toBeDisabled()
    expect(box()).toHaveTextContent('Only one solution document per round.')
  })
})

describe('FeedbackBox — failure', () => {
  it('shows the server’s own message and lets the user try again', async () => {
    const user = userEvent.setup()
    const { ApiError } = (await import('../api')) as unknown as {
      ApiError: new (m: string, s: number) => Error
    }
    reReview.mockRejectedValueOnce(
      new ApiError('Review was created before follow-ups were supported.', 409),
    )
    const onStarted = mount()

    await user.type(field(), 'Re-check this.')
    await user.click(submit())

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Review was created before follow-ups were supported.',
    )
    expect(onStarted).not.toHaveBeenCalled()
    // Not stuck in the busy state — the button says what it does again.
    expect(submit()).toBeEnabled()
  })

  it('does not report a round as started when the attachment upload fails', async () => {
    const user = userEvent.setup()
    uploadFile.mockRejectedValueOnce(new Error('network'))
    const onStarted = mount()

    await user.type(field(), 'With a file.')
    await user.click(screen.getByText(/Attach a revised document or diagram/i))
    const input = box().querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(input, new File(['x'], 'revised.txt', { type: 'text/plain' }))
    await user.click(submit())

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    // The round never started, so nothing must be polled for it.
    expect(reReview).not.toHaveBeenCalled()
    expect(onStarted).not.toHaveBeenCalled()
  })

  it('cannot be submitted twice while a round is starting', async () => {
    const user = userEvent.setup()
    let release: (value: unknown) => void = () => {}
    reReview.mockReturnValueOnce(new Promise((resolve) => (release = resolve)))
    mount()

    await user.type(field(), 'Re-check this.')
    await user.click(submit())

    const busy = screen.getByRole('button', { name: /Starting the follow-up/i })
    expect(busy).toBeDisabled()
    await user.click(busy)
    expect(reReview).toHaveBeenCalledTimes(1)

    release({ review_id: 'rev-v2', status_url: '', result_url: '' })
  })
})
