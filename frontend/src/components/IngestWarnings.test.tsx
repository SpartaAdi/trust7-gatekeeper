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

const MINOR_GAPS: IngestWarning = {
  code: 'vision_minor_gaps',
  message:
    'The diagram was read with high confidence overall, but one detail was ' +
    'unclear: the text label beneath the middle Route 53 shield icon. That is a ' +
    'bounded gap in an otherwise legible diagram, not a reason to doubt the ' +
    'components and connections below.',
  detail: 'architecture.png: model-reported confidence=high, 22 components extracted',
}

const LOW_CONFIDENCE: IngestWarning = {
  code: 'vision_low_confidence',
  message: 'The diagram was read with low confidence.',
  detail: 'blurry.png: model-reported confidence=low, 3 components extracted',
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

  it('does not head a minor-gaps warning with the unreadable-design title', () => {
    // The title was one hardcoded string for every code, which was true until this
    // one existed. "Only part of this design could be read" above "read with high
    // confidence overall" is the same contradiction the backend split removed — the
    // panel would have disagreed with its own body.
    render(<IngestWarnings warnings={[MINOR_GAPS]} />)

    expect(screen.queryByText(/Only part of this design could be read/)).not.toBeInTheDocument()
    expect(screen.getByText(/A few details in the diagram were unclear/)).toBeInTheDocument()
    expect(screen.getByText(/read with high confidence overall/)).toBeInTheDocument()
  })

  it('renders a minor-gaps warning as neutral, not caution', () => {
    // CaveatPanel defines `caution` as "wrong enough that a human should look". A
    // named sub-label on an otherwise legible diagram is not that, and a caution
    // banner spent here is one ignored on a real problem.
    render(<IngestWarnings warnings={[MINOR_GAPS]} />)

    expect(screen.getByTestId('warning-vision_minor_gaps')).toHaveAttribute(
      'data-tone',
      'neutral',
    )
  })

  it('still renders a genuine low-confidence warning as caution', () => {
    // The inverse. Only the code separates these two, so the default path must be
    // untouched by the new mapping.
    render(<IngestWarnings warnings={[LOW_CONFIDENCE]} />)

    const panel = screen.getByTestId('warning-vision_low_confidence')
    expect(panel).toHaveAttribute('data-tone', 'caution')
    expect(screen.getByText(/Only part of this design could be read/)).toBeInTheDocument()
  })

  it('renders both vision codes side by side without either swallowing the other', () => {
    render(<IngestWarnings warnings={[LOW_CONFIDENCE, MINOR_GAPS]} />)

    expect(screen.getByTestId('warning-vision_low_confidence')).toBeInTheDocument()
    expect(screen.getByTestId('warning-vision_minor_gaps')).toBeInTheDocument()
  })
})
