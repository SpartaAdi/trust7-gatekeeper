import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { submitReview, uploadFile } from '../api'
import { clearApiKey, getApiKey } from '../apiKey'
import { UploadView } from './UploadView'

vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {},
  uploadFile: vi.fn(),
  submitReview: vi.fn(),
}))

const sow = () => new File(['sow'], 'payments-sow.pdf', { type: 'application/pdf' })
const diagram = () =>
  new File(['<mxfile/>'], 'architecture.drawio', { type: 'application/xml' })

/** The dropzone's file input is visually hidden but is the labelled control. */
function fileInput(): HTMLInputElement {
  return document.querySelector('input[type="file"]') as HTMLInputElement
}

describe('UploadView', () => {
  // Call counts are asserted below, so they must not accumulate across tests.
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the dropzone and a disabled submit', () => {
    render(<UploadView onStarted={vi.fn()} />)

    expect(screen.getByText(/drop your solution document/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start review/i })).toBeDisabled()
    expect(
      screen.getByText(/add a solution document, an architecture diagram, or both/i),
    ).toBeInTheDocument()
  })

  it('auto-classifies dropped files and tags each with its detected type', async () => {
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await user.upload(fileInput(), [sow(), diagram()])

    const rows = screen.getAllByRole('listitem')
    expect(within(rows[0]!).getByText('SoW')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('Diagram')).toBeInTheDocument()
    // The diagram path is surfaced because one route costs vision tokens.
    // Scoped to the row: the intro paragraph mentions the routes too.
    expect(within(rows[1]!).getByText(/parsed directly/i)).toBeInTheDocument()
  })

  it('asks rather than guessing when the type is ambiguous', async () => {
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await user.upload(fileInput(), [new File(['x'], 'notes.md', { type: 'text/markdown' })])

    expect(screen.getByLabelText(/which is it/i)).toBeInTheDocument()
    expect(screen.getByText(/set a type for every file/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start review/i })).toBeDisabled()

    await user.selectOptions(screen.getByLabelText(/which is it/i), 'document')
    expect(screen.getByText('SoW')).toBeInTheDocument()
  })

  it('enables submit on a document alone — either input is sufficient', async () => {
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await user.upload(fileInput(), [sow()])

    expect(screen.getByRole('button', { name: /start review/i })).toBeEnabled()
    // The old copy demanded a diagram too; the backend never did.
    expect(screen.queryByText(/add an architecture diagram/i)).toBeNull()
  })

  it('enables submit on a diagram alone', async () => {
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await user.upload(fileInput(), [diagram()])

    expect(screen.getByRole('button', { name: /start review/i })).toBeEnabled()
    expect(screen.queryByText(/add a solution document/i)).toBeNull()
  })

  it('submits a document-only review with an empty diagram key', async () => {
    vi.mocked(uploadFile).mockResolvedValueOnce('uploads/a/payments-sow.pdf')
    vi.mocked(submitReview).mockResolvedValue({
      review_id: 'rev-doc',
      status_url: '',
      result_url: '',
    })
    const onStarted = vi.fn()
    const user = userEvent.setup()

    render(<UploadView onStarted={onStarted} />)
    await user.upload(fileInput(), [sow()])
    await user.click(screen.getByRole('button', { name: /start review/i }))

    await waitFor(() => expect(onStarted).toHaveBeenCalledWith('rev-doc', expect.any(Number)))
    // Only the file that exists is uploaded.
    expect(uploadFile).toHaveBeenCalledTimes(1)
    expect(submitReview).toHaveBeenCalledWith(
      expect.objectContaining({
        documentKey: 'uploads/a/payments-sow.pdf',
        diagramKey: '',
      }),
    )
  })

  it('submits a diagram-only review with an empty document key', async () => {
    vi.mocked(uploadFile).mockResolvedValueOnce('uploads/b/architecture.drawio')
    vi.mocked(submitReview).mockResolvedValue({
      review_id: 'rev-dia',
      status_url: '',
      result_url: '',
    })
    const onStarted = vi.fn()
    const user = userEvent.setup()

    render(<UploadView onStarted={onStarted} />)
    await user.upload(fileInput(), [diagram()])
    await user.click(screen.getByRole('button', { name: /start review/i }))

    await waitFor(() => expect(onStarted).toHaveBeenCalledWith('rev-dia', expect.any(Number)))
    expect(uploadFile).toHaveBeenCalledTimes(1)
    expect(submitReview).toHaveBeenCalledWith(
      expect.objectContaining({
        documentKey: '',
        diagramKey: 'uploads/b/architecture.drawio',
        // Falls back to the only filename there is.
        title: 'architecture.drawio',
      }),
    )
  })

  it('names no vendor when describing how a diagram is read', () => {
    render(<UploadView onStarted={vi.fn()} />)

    const intro = screen.getByText(/read using AI vision/i)
    expect(intro).toBeInTheDocument()

    // The bring-your-own-key control is the one deliberate exception and is
    // excluded rather than the rule being dropped: a reviewer pasting a
    // credential has to be told which provider it belongs to, or they will paste
    // an Anthropic key into a field the pipeline sends to OpenRouter. Everywhere
    // else on this view stays vendor-neutral, which is what this test protects.
    const keyField = screen.getByTestId('api-key-field')
    keyField.remove()

    expect(document.body.textContent).not.toMatch(/claude|anthropic|openrouter|kimi/i)
  })

  describe('diagram-only note', () => {
    it('appears when a diagram is present with no document', async () => {
      const user = userEvent.setup()
      render(<UploadView onStarted={vi.fn()} />)

      await user.upload(fileInput(), [diagram()])

      const note = screen.getByTestId('diagram-only-note')
      expect(note).toHaveTextContent(/architecture diagram received/i)
      expect(note).toHaveTextContent(/process, operational, and compliance/i)
    })

    it('is informational, not an error', async () => {
      const user = userEvent.setup()
      render(<UploadView onStarted={vi.fn()} />)

      await user.upload(fileInput(), [diagram()])

      const note = screen.getByTestId('diagram-only-note')
      // No alert role and no severity colour: diagram-only is valid, not degraded.
      expect(note).not.toHaveAttribute('role', 'alert')
      expect(note.className).toContain('border-minfy-navy')
      expect(note.className).not.toMatch(/sev-high|sev-medium/)
    })

    it('disappears once a document is added', async () => {
      const user = userEvent.setup()
      render(<UploadView onStarted={vi.fn()} />)

      await user.upload(fileInput(), [diagram()])
      expect(screen.getByTestId('diagram-only-note')).toBeInTheDocument()

      await user.upload(fileInput(), [sow()])
      expect(screen.queryByTestId('diagram-only-note')).toBeNull()
    })

    it('is absent for a document-only submission', async () => {
      const user = userEvent.setup()
      render(<UploadView onStarted={vi.fn()} />)

      await user.upload(fileInput(), [sow()])

      expect(screen.queryByTestId('diagram-only-note')).toBeNull()
    })
  })

  describe('no-description warning', () => {
    /**
     * Narrower than the note above, and separate from it on purpose.
     *
     * The note fires the moment a diagram arrives alone and offers a document. This
     * fires only when BOTH ways of describing the design have been declined — no SoW
     * and an empty context field — because at that point the consequence is specific:
     * encryption, IAM and DR live in prose, and there is no prose.
     */
    const warning = () => screen.queryByTestId('no-description-warning')

    it('appears for a diagram with no document and no context text', async () => {
      const user = userEvent.setup()
      render(<UploadView onStarted={vi.fn()} />)

      await user.upload(fileInput(), [diagram()])

      expect(warning()).toHaveTextContent(
        /No accompanying description provided — controls described only in prose/,
      )
      expect(warning()).toHaveTextContent(
        /\(encryption, IAM, disaster recovery, etc\.\) can't be scored from a diagram alone\./,
      )
    })

    it('disappears once context text is typed', async () => {
      const user = userEvent.setup()
      render(<UploadView onStarted={vi.fn()} />)

      await user.upload(fileInput(), [diagram()])
      expect(warning()).toBeInTheDocument()

      await user.type(
        screen.getByLabelText(/purpose|use case|what this system/i),
        'Internal claims triage for a regulated insurer; PII throughout.',
      )

      expect(warning()).toBeNull()
    })

    it('is absent when a SoW document accompanies the diagram', async () => {
      const user = userEvent.setup()
      render(<UploadView onStarted={vi.fn()} />)

      await user.upload(fileInput(), [diagram()])
      expect(warning()).toBeInTheDocument()

      await user.upload(fileInput(), [sow()])

      expect(warning()).toBeNull()
    })

    it('is absent for a SoW-only submission', async () => {
      const user = userEvent.setup()
      render(<UploadView onStarted={vi.fn()} />)

      await user.upload(fileInput(), [sow()])

      expect(warning()).toBeNull()
    })

    it('does not gate the submit — the review is still allowed to run', async () => {
      // Informational only. A diagram-only review with no context is a valid thing to
      // submit, and a warning that quietly disabled the button would be a gate.
      const user = userEvent.setup()
      render(<UploadView onStarted={vi.fn()} />)

      await user.upload(fileInput(), [diagram()])

      expect(warning()).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /start review/i })).toBeEnabled()
    })
  })

  it('rejects a second file of the same kind', async () => {
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await user.upload(fileInput(), [sow(), diagram()])
    await user.upload(fileInput(), [
      new File(['x'], 'other-sow.pdf', { type: 'application/pdf' }),
    ])

    expect(screen.getByText(/only one solution document/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start review/i })).toBeDisabled()
  })

  it('uploads both files and submits their keys with the review name', async () => {
    vi.mocked(uploadFile)
      .mockResolvedValueOnce('uploads/a/payments-sow.pdf')
      .mockResolvedValueOnce('uploads/b/architecture.drawio')
    vi.mocked(submitReview).mockResolvedValue({
      review_id: 'rev-9',
      status_url: '/reviews/rev-9/status',
      result_url: '/reviews/rev-9',
    })
    const onStarted = vi.fn()
    const user = userEvent.setup()

    render(<UploadView onStarted={onStarted} />)
    await user.upload(fileInput(), [sow(), diagram()])
    await user.type(screen.getByLabelText(/review name/i), 'Q3 payments platform')
    await user.click(screen.getByRole('button', { name: /start review/i }))

    await waitFor(() => expect(onStarted).toHaveBeenCalledWith('rev-9', expect.any(Number)))
    expect(uploadFile).toHaveBeenCalledTimes(2)
    expect(submitReview).toHaveBeenCalledWith(
      expect.objectContaining({
        documentKey: 'uploads/a/payments-sow.pdf',
        diagramKey: 'uploads/b/architecture.drawio',
        title: 'Q3 payments platform',
      }),
    )
  })

  it('reports the instant the submission began, not the instant it was accepted', async () => {
    // The elapsed clock counts from `startedAt`, so it must predate the uploads. On a
    // slow connection those take seconds, and a clock started after them would under-
    // report the wait the user actually sat through.
    let firstUploadAt = 0
    vi.mocked(uploadFile).mockImplementation(async () => {
      firstUploadAt = firstUploadAt || Date.now()
      return 'uploads/a/payments-sow.pdf'
    })
    vi.mocked(submitReview).mockResolvedValue({
      review_id: 'rev-clock',
      status_url: '',
      result_url: '',
    })
    const onStarted = vi.fn()
    const user = userEvent.setup()

    render(<UploadView onStarted={onStarted} />)
    await user.upload(fileInput(), [sow()])
    await user.click(screen.getByRole('button', { name: /start review/i }))

    await waitFor(() => expect(onStarted).toHaveBeenCalled())
    const startedAt = onStarted.mock.calls[0]![1] as number
    expect(startedAt).toBeLessThanOrEqual(firstUploadAt)
  })

  it('defaults the review name to the document filename', async () => {
    vi.mocked(uploadFile).mockResolvedValueOnce('k1').mockResolvedValueOnce('k2')
    vi.mocked(submitReview).mockResolvedValue({
      review_id: 'rev-10',
      status_url: '',
      result_url: '',
    })
    const user = userEvent.setup()

    render(<UploadView onStarted={vi.fn()} />)
    await user.upload(fileInput(), [sow(), diagram()])
    await user.click(screen.getByRole('button', { name: /start review/i }))

    await waitFor(() =>
      expect(submitReview).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'payments-sow.pdf' }),
      ),
    )
  })

  it('renders the re-review variant when a previous review is supplied', () => {
    render(<UploadView previousReviewId="rev-1" onStarted={vi.fn()} />)

    expect(
      screen.getByRole('heading', { name: /submit the revised design/i }),
    ).toBeInTheDocument()
    expect(screen.getByText('rev-1')).toBeInTheDocument()
  })
})

/** Install or remove a Web Speech API stub before the view mounts. */
function setSpeechSupport(supported: boolean) {
  const scope = window as unknown as Record<string, unknown>
  if (!supported) {
    delete scope['SpeechRecognition']
    delete scope['webkitSpeechRecognition']
    return null
  }
  const instances: Record<string, unknown>[] = []
  class FakeRecognition {
    continuous = false
    interimResults = true
    lang = ''
    onresult: ((event: unknown) => void) | null = null
    onerror: (() => void) | null = null
    onend: (() => void) | null = null
    started = false
    stopped = false
    constructor() {
      instances.push(this as unknown as Record<string, unknown>)
    }
    start() {
      this.started = true
    }
    stop() {
      this.stopped = true
      this.onend?.()
    }
  }
  scope['SpeechRecognition'] = FakeRecognition
  return instances
}

// --------------------------------------------------------------------------- #
// Optional context field — diagram-only, with dictation
// --------------------------------------------------------------------------- #

describe('context field', () => {
  // A sibling of the describe above, so it does not inherit that block's
  // beforeEach — without this, `submitReview.mock.calls[0]` reads the previous
  // test's call and an assertion about THIS test passes or fails on stale data.
  beforeEach(() => {
    vi.clearAllMocks()
  })

  async function stageDiagramOnly(user: ReturnType<typeof userEvent.setup>) {
    await user.upload(fileInput(), diagram())
  }

  it('is offered when the upload is diagram-only', async () => {
    setSpeechSupport(false)
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await stageDiagramOnly(user)

    expect(
      screen.getByLabelText(/add more context — purpose and use case/i),
    ).toBeInTheDocument()
  })

  it('is NOT offered when a solution document is also attached', async () => {
    setSpeechSupport(false)
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await user.upload(fileInput(), [diagram(), sow()])

    // The SoW already carries purpose and use case.
    expect(
      screen.queryByLabelText(/add more context/i),
    ).not.toBeInTheDocument()
  })

  it('sends the typed context with a diagram-only submission', async () => {
    setSpeechSupport(false)
    vi.mocked(uploadFile).mockResolvedValue('uploads/x/design.drawio')
    vi.mocked(submitReview).mockResolvedValue({
      review_id: 'rev-9', status_url: '', result_url: '',
    })
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await stageDiagramOnly(user)
    await user.type(
      screen.getByLabelText(/add more context/i),
      'Internal claims portal used by 40 staff.',
    )
    await user.click(screen.getByRole('button', { name: /start review/i }))

    await waitFor(() => expect(submitReview).toHaveBeenCalled())
    expect(vi.mocked(submitReview).mock.calls[0]?.[0]).toMatchObject({
      context: 'Internal claims portal used by 40 staff.',
    })
  })

  it('sends an empty context when a document is attached', async () => {
    setSpeechSupport(false)
    vi.mocked(uploadFile).mockResolvedValue('uploads/x/f')
    vi.mocked(submitReview).mockResolvedValue({
      review_id: 'rev-9', status_url: '', result_url: '',
    })
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await user.upload(fileInput(), sow())
    await user.click(screen.getByRole('button', { name: /start review/i }))

    await waitFor(() => expect(submitReview).toHaveBeenCalled())
    // The existing path is unchanged: '' exactly as before the field existed.
    expect(vi.mocked(submitReview).mock.calls[0]?.[0]).toMatchObject({ context: '' })
  })

  it('caps what can be typed at the backend limit', async () => {
    setSpeechSupport(false)
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await stageDiagramOnly(user)
    const field = screen.getByLabelText(/add more context/i) as HTMLTextAreaElement

    // maxLength is the browser's stop; the slice is ours, for a paste.
    expect(field.maxLength).toBe(1000)
  })

  it('shows the mic when the browser supports speech recognition', async () => {
    setSpeechSupport(true)
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await stageDiagramOnly(user)

    expect(screen.getByRole('button', { name: /speak out your purpose and use case/i })).toBeInTheDocument()
  })

  it('hides the mic entirely when the browser does not support it', async () => {
    // Firefox and older Safari have no implementation. A button that cannot listen
    // teaches the user nothing when it fails.
    setSpeechSupport(false)
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await stageDiagramOnly(user)

    expect(screen.queryByRole('button', { name: /dictate/i })).not.toBeInTheDocument()
    // The field itself is still offered.
    expect(screen.getByLabelText(/add more context/i)).toBeInTheDocument()
  })

  it('appends dictated text rather than replacing what was typed', async () => {
    const instances = setSpeechSupport(true)!
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await stageDiagramOnly(user)
    const field = screen.getByLabelText(/add more context/i)
    await user.type(field, 'Claims portal.')
    await user.click(screen.getByRole('button', { name: /speak out your purpose and use case/i }))

    const recogniser = instances[0]!
    ;(recogniser['onresult'] as (event: unknown) => void)({
      resultIndex: 0,
      results: Object.assign([{ 0: { transcript: 'Used by 40 staff.' }, isFinal: true }], {
        length: 1,
      }),
    })

    await waitFor(() =>
      expect(field).toHaveValue('Claims portal. Used by 40 staff.'),
    )
  })

  it('drops interim results, which rewrite themselves mid-utterance', async () => {
    const instances = setSpeechSupport(true)!
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await stageDiagramOnly(user)
    await user.click(screen.getByRole('button', { name: /speak out your purpose and use case/i }))

    const recogniser = instances[0]!
    ;(recogniser['onresult'] as (event: unknown) => void)({
      resultIndex: 0,
      results: Object.assign([{ 0: { transcript: 'half a thou' }, isFinal: false }], {
        length: 1,
      }),
    })

    expect(screen.getByLabelText(/add more context/i)).toHaveValue('')
  })

  it('asks for continuous, final-only recognition', async () => {
    const instances = setSpeechSupport(true)!
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await stageDiagramOnly(user)
    await user.click(screen.getByRole('button', { name: /speak out your purpose and use case/i }))

    expect(instances[0]).toMatchObject({
      continuous: true,
      interimResults: false,
      started: true,
    })
  })

  it('returns the mic to its resting state when recognition ends', async () => {
    // Covers a permission denial too: both onerror and onend clear the recogniser,
    // so the button cannot stick in a listening state with nothing behind it.
    const instances = setSpeechSupport(true)!
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await stageDiagramOnly(user)
    await user.click(screen.getByRole('button', { name: /speak out your purpose and use case/i }))
    expect(screen.getByRole('button', { name: /stop dictating/i })).toBeInTheDocument()

    ;(instances[0]!['onerror'] as () => void)()

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /speak out your purpose and use case/i })).toBeInTheDocument(),
    )
  })
})

describe('UploadView — the reviewer’s own OpenRouter key', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearApiKey()
  })

  /**
   * The counterpart to the vendor-neutrality test above. Softening this label to
   * a generic "API key" would let a reviewer paste an Anthropic key, which the
   * live pipeline never reads — the request would go to OpenRouter and 401 with
   * nothing on screen explaining why.
   */
  it('names the provider, because the wrong vendor’s key silently fails', async () => {
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    const toggle = screen.getByRole('button', { name: /use my own openrouter key/i })
    await user.click(toggle)

    expect(screen.getByTestId('api-key-field').textContent).toMatch(/openrouter/i)
    expect(screen.getByLabelText(/openrouter api key/i)).toHaveAttribute(
      'placeholder',
      expect.stringContaining('sk-or-'),
    )
  })

  it('is collapsed by default, so a credential field is not the default path', () => {
    render(<UploadView onStarted={vi.fn()} />)

    expect(
      screen.getByRole('button', { name: /use my own openrouter key/i }),
    ).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByLabelText(/openrouter api key/i)).not.toBeInTheDocument()
  })

  it('stores what is typed, so the next submission bills to it', async () => {
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: /use my own openrouter key/i }))
    await user.type(screen.getByLabelText(/openrouter api key/i), 'sk-or-v1-typed-key')

    expect(getApiKey()).toBe('sk-or-v1-typed-key')
  })

  it('masks the key as a password field rather than showing it on screen', async () => {
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: /use my own openrouter key/i }))
    const field = screen.getByLabelText(/openrouter api key/i)

    expect(field).toHaveAttribute('type', 'password')
    expect(field).toHaveAttribute('autoComplete', 'off')
  })

  it('says plainly that a refresh clears it, because it does', async () => {
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: /use my own openrouter key/i }))

    expect(screen.getByText(/never saved to the server or to your browser/i)).toBeInTheDocument()
    expect(screen.getByText(/a refresh clears it/i)).toBeInTheDocument()
  })

  it('clears on request, and shows only a masked hint when collapsed', async () => {
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: /use my own openrouter key/i }))
    await user.type(
      screen.getByLabelText(/openrouter api key/i),
      'sk-or-v1-abcdefghijklmnop',
    )
    await user.click(screen.getByRole('button', { name: /hide/i }))

    // Collapsed: a recognisable fragment, never the whole credential.
    expect(screen.getByText(/sk-or-…mnop/)).toBeInTheDocument()
    expect(screen.queryByText(/abcdefghijkl/)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /use my own openrouter key/i }))
    await user.click(screen.getByRole('button', { name: /clear now/i }))

    expect(getApiKey()).toBe('')
  })
})

/**
 * On a diagram-only submission this field is the only place intent can come
 * from, and live feedback was that both it and the mic were being missed.
 */
describe('UploadView — context and dictation discoverability', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  async function stageDiagram() {
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)
    await user.upload(fileInput(), [diagram()])
    return user
  }

  it('gives the context field its own tinted panel, not a bare label', async () => {
    await stageDiagram()

    const field = screen.getByTestId('context-field')
    expect(field.className).toContain('bg-pastel-sky')
    expect(field.className).toContain('border-minfy-indigo')
  })

  it('tells the reader they can speak it as well as type it', async () => {
    await stageDiagram()

    expect(screen.getByTestId('context-field')).toHaveTextContent(/type it or say it/i)
  })

  it('labels the mic with the spoken prompt, for both hover and assistive tech', async () => {
    setSpeechSupport(true)
    await stageDiagram()

    const mic = screen.getByRole('button', {
      name: /speak out your purpose and use case/i,
    })
    expect(mic).toHaveAttribute('title', 'Speak out your purpose and use case')
    expect(screen.getByTestId('mic-tooltip')).toHaveTextContent(
      'Speak out your purpose and use case',
    )
  })

  it('hides the visual tooltip from assistive tech, so it is not read twice', async () => {
    setSpeechSupport(true)
    await stageDiagram()

    expect(screen.getByTestId('mic-tooltip')).toHaveAttribute('aria-hidden', 'true')
  })

  it('uses no severity colour for the recording state', async () => {
    setSpeechSupport(true)
    const user = await stageDiagram()

    const mic = screen.getByRole('button', {
      name: /speak out your purpose and use case/i,
    })
    expect(mic.className).toContain('bg-minfy-indigo')

    await user.click(mic)

    // Recording is a state, not a finding: sev-high means one thing everywhere.
    const listening = screen.getByRole('button', { name: /stop dictating/i })
    expect(listening.className).toContain('bg-minfy-navy')
    expect(listening.className).not.toMatch(/sev-/)
  })
})
