import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { IngestWarnings } from './IngestWarnings'
import type { IngestWarning } from '../types'

const NEAR_EMPTY: IngestWarning = {
  code: 'diagram_near_empty',
  message:
    'Almost nothing could be read from the uploaded diagram — 1 component was ' +
    'extracted from an image of 488 KB.',
  detail: 'screenshot.png: 500000 bytes, 1 components, 0 connections, 0 notes',
}

const SPARSE_DOC: IngestWarning = {
  code: 'document_sparse_text',
  message: 'Very little text could be read from this document.',
  detail: 'sow.pdf: 40 pages, 2 with text, 940 characters total, 24 per page',
}

describe('IngestWarnings', () => {
  it('renders nothing at all when there are no warnings', () => {
    // The normal case, and it must occupy no space: an empty bordered container
    // above the executive summary would read as a warning with the text missing.
    const { container } = render(<IngestWarnings warnings={[]} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('shows the message and the measurement behind it', () => {
    render(<IngestWarnings warnings={[NEAR_EMPTY]} />)

    expect(screen.getByText(/Almost nothing could be read/)).toBeInTheDocument()
    // `detail` is shown rather than hidden behind a disclosure: a warning a reviewer
    // cannot check is one they have to take on trust.
    expect(screen.getByText(/500000 bytes/)).toBeInTheDocument()
  })

  it('renders one panel per warning, keyed by code', () => {
    render(<IngestWarnings warnings={[NEAR_EMPTY, SPARSE_DOC]} />)

    expect(screen.getByTestId('warning-diagram_near_empty')).toBeInTheDocument()
    expect(screen.getByTestId('warning-document_sparse_text')).toBeInTheDocument()
  })

  it('announces as a status rather than an alert', () => {
    // The review SUCCEEDED. These are a caveat on a usable result, not a failure to
    // produce one — styling and announcing them as an error would put them in the
    // same class as "the pipeline crashed", and a reviewer who learns to dismiss one
    // will dismiss the other.
    render(<IngestWarnings warnings={[NEAR_EMPTY]} />)

    expect(screen.getByRole('status', { name: 'Extraction warnings' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('omits the detail line when there is no detail', () => {
    render(<IngestWarnings warnings={[{ ...NEAR_EMPTY, detail: '' }]} />)

    expect(screen.getByTestId('warning-diagram_near_empty')).toBeInTheDocument()
    expect(screen.queryByText(/500000/)).not.toBeInTheDocument()
  })
})
