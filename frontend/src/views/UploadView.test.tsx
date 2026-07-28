import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { submitReview, uploadFile } from '../api'
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

    await waitFor(() => expect(onStarted).toHaveBeenCalledWith('rev-doc'))
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

    await waitFor(() => expect(onStarted).toHaveBeenCalledWith('rev-dia'))
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

    await waitFor(() => expect(onStarted).toHaveBeenCalledWith('rev-9'))
    expect(uploadFile).toHaveBeenCalledTimes(2)
    expect(submitReview).toHaveBeenCalledWith(
      expect.objectContaining({
        documentKey: 'uploads/a/payments-sow.pdf',
        diagramKey: 'uploads/b/architecture.drawio',
        title: 'Q3 payments platform',
      }),
    )
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
