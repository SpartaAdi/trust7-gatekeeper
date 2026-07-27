import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

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
  it('renders the dropzone and a disabled submit', () => {
    render(<UploadView onStarted={vi.fn()} />)

    expect(screen.getByText(/drop your solution document/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start review/i })).toBeDisabled()
    expect(screen.getByText(/add your two files/i)).toBeInTheDocument()
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

  it('requires one document and one diagram before submit is enabled', async () => {
    const user = userEvent.setup()
    render(<UploadView onStarted={vi.fn()} />)

    await user.upload(fileInput(), [sow()])
    expect(screen.getByText(/add an architecture diagram/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start review/i })).toBeDisabled()

    await user.upload(fileInput(), [diagram()])
    expect(screen.getByRole('button', { name: /start review/i })).toBeEnabled()
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
