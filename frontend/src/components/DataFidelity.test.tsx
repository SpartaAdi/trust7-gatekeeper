import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { DataFidelity } from './DataFidelity'
import type { DataFidelity as Fidelity } from '../types'

const EMPTY: Fidelity = { structural: null, ocr_proxy: null, grounding: null }

const HEALTHY_STRUCTURAL = {
  parsed_elements: 19,
  total_elements: 19,
  percent: 100,
  dropped: [],
}

const LOW_STRUCTURAL = {
  parsed_elements: 2,
  total_elements: 11,
  percent: 18.2,
  dropped: ['9 shapes sharing an id with an earlier shape'],
}

const OCR_HEALTHY = {
  available: true,
  unavailable_reason: '',
  is_estimate: true,
  ocr_tokens: 20,
  matched_tokens: 20,
  percent: 100,
  sample_unmatched: [],
}

const OCR_LOW = {
  available: true,
  unavailable_reason: '',
  is_estimate: true,
  ocr_tokens: 24,
  matched_tokens: 9,
  percent: 37.5,
  sample_unmatched: ['cloudfront', 'dynamodb', 'secretsmanager'],
}

describe('DataFidelity', () => {
  it('renders nothing when no metric applies', () => {
    const { container } = render(<DataFidelity fidelity={EMPTY} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing for a review stored before fidelity existed', () => {
    const { container } = render(<DataFidelity />)

    expect(container).toBeEmptyDOMElement()
  })

  describe('structural coverage — the exact one', () => {
    it('shows the percentage and says it is measured, not estimated', () => {
      render(<DataFidelity fidelity={{ ...EMPTY, structural: HEALTHY_STRUCTURAL }} />)

      expect(screen.getByText(/Diagram structure read: 100% of elements/)).toBeInTheDocument()
      expect(screen.getByText(/measured, not estimated/)).toBeInTheDocument()
      expect(screen.getByText(/19 of 19 diagram elements parsed/)).toBeInTheDocument()
    })

    it('is neutral toned at or above the threshold', () => {
      render(<DataFidelity fidelity={{ ...EMPTY, structural: HEALTHY_STRUCTURAL }} />)

      expect(screen.getByTestId('fidelity-structural')).toHaveAttribute('data-tone', 'neutral')
    })

    it('turns to caution and recommends a hand check below 95%', () => {
      render(<DataFidelity fidelity={{ ...EMPTY, structural: LOW_STRUCTURAL }} />)

      const panel = screen.getByTestId('fidelity-structural')
      expect(panel).toHaveAttribute('data-tone', 'caution')
      expect(panel).toHaveTextContent(/checked by hand/)
      // And it says WHY coverage is low, or an accurate number reads as a bug.
      expect(panel).toHaveTextContent(/sharing an id with an earlier shape/)
    })

    it('never labels the exact figure as an estimate', () => {
      // It DOES contain the word, in "measured, not estimated" — which is the point.
      // What it must never carry is the estimate LABEL the OCR proxy wears.
      render(<DataFidelity fidelity={{ ...EMPTY, structural: HEALTHY_STRUCTURAL }} />)

      const panel = screen.getByTestId('fidelity-structural')
      expect(panel).not.toHaveTextContent(/\(estimated\)/)
      expect(panel).not.toHaveTextContent(/is an estimate/)
      expect(panel).not.toHaveTextContent(/estimated proxy/)
      expect(panel).toHaveTextContent(/measured, not estimated/)
    })
  })

  describe('OCR proxy — the estimated one', () => {
    it('says "estimated" in the heading, the body AND the detail line', () => {
      // Repeated deliberately: this is the number most likely to be quoted out of
      // context as though it were measured.
      render(<DataFidelity fidelity={{ ...EMPTY, ocr_proxy: OCR_LOW }} />)

      const panel = screen.getByTestId('fidelity-ocr')
      expect(panel).toHaveTextContent(/~37.5% \(estimated\)/)
      expect(panel).toHaveTextContent(/estimate, not a measurement/)
      expect(panel).toHaveTextContent(/estimated proxy/)
    })

    it('says a low figure means the readers disagree, not which is right', () => {
      render(<DataFidelity fidelity={{ ...EMPTY, ocr_proxy: OCR_LOW }} />)

      const panel = screen.getByTestId('fidelity-ocr')
      expect(panel).toHaveTextContent(/no ground truth/)
      expect(panel).toHaveTextContent(/not which one is right/)
      // The unmatched sample, with the caveat that it may be OCR noise.
      expect(panel).toHaveTextContent(/cloudfront/)
      expect(panel).toHaveTextContent(/may be OCR noise/)
    })

    it('fires NO automated recommendation, however low the figure', () => {
      // The change this test exists for. The caution tone and the "check by hand"
      // wording ARE the automated recommendation as the reviewer experiences it, so
      // removing the trigger means removing both — not just the backend predicate.
      render(<DataFidelity fidelity={{ ...EMPTY, ocr_proxy: OCR_LOW }} />)

      const panel = screen.getByTestId('fidelity-ocr')
      expect(panel).toHaveAttribute('data-tone', 'neutral')
      expect(panel).not.toHaveTextContent(/recommended/)
      expect(panel).not.toHaveTextContent(/should be checked/)
      // The figure itself is still there, still labelled an estimate.
      expect(panel).toHaveTextContent(/~37.5% \(estimated\)/)
      expect(panel).toHaveTextContent(/worth a look, not a verdict/)
    })

    it('is neutral at every figure from 0 to 100', () => {
      // Parametrised by hand rather than left to the one case above: the tone must be
      // unconditional, and a `percent < X` that happens to be false for one fixture
      // would pass a single-case test.
      for (const percent of [0, 8.3, 37.5, 94.9, 95, 100]) {
        const { unmount } = render(
          <DataFidelity fidelity={{ ...EMPTY, ocr_proxy: { ...OCR_LOW, percent } }} />,
        )
        expect(screen.getByTestId('fidelity-ocr')).toHaveAttribute('data-tone', 'neutral')
        unmount()
      }
    })

    it('keeps the estimate label at a healthy figure too', () => {
      render(<DataFidelity fidelity={{ ...EMPTY, ocr_proxy: OCR_HEALTHY }} />)

      const panel = screen.getByTestId('fidelity-ocr')
      expect(panel).toHaveAttribute('data-tone', 'neutral')
      expect(panel).toHaveTextContent(/\(estimated\)/)
    })

    it('reports an unavailable engine as NOT MEASURED rather than as zero', () => {
      // A 0% would read as "the vision model missed everything" — a claim about the
      // model, when the truth is a claim about our tooling.
      render(
        <DataFidelity
          fidelity={{
            ...EMPTY,
            ocr_proxy: {
              ...OCR_HEALTHY,
              available: false,
              percent: 0,
              unavailable_reason: 'No OCR engine installed.',
            },
          }}
        />,
      )

      const panel = screen.getByTestId('fidelity-ocr-unavailable')
      expect(panel).toHaveTextContent(/not measured/)
      expect(panel).toHaveTextContent(/this is not a score of zero/)
      expect(panel).toHaveTextContent(/No OCR engine installed/)
      // The 0 must not be rendered as a coverage figure anywhere.
      expect(screen.queryByTestId('fidelity-ocr')).toBeNull()
      expect(panel).not.toHaveTextContent(/0%/)
    })
  })

  describe('grounding filter — the count', () => {
    it('reads "N ungrounded claims caught and removed"', () => {
      render(
        <DataFidelity
          fidelity={{
            ...EMPTY,
            grounding: { checked: 5, removed: 2, incomplete: 1, removed_for: ['queue', 'db'] },
          }}
        />,
      )

      expect(screen.getByText(/2 ungrounded claims caught and removed/)).toBeInTheDocument()
      expect(screen.getByTestId('fidelity-grounding')).toHaveTextContent(/queue, db/)
    })

    it('states plainly that it is not a confidence figure for what remains', () => {
      // The whole point. "2 of 5 removed" must never be presented as "60% grounded".
      render(
        <DataFidelity
          fidelity={{ ...EMPTY, grounding: { checked: 5, removed: 2, incomplete: 0, removed_for: [] } }}
        />,
      )

      const panel = screen.getByTestId('fidelity-grounding')
      expect(panel).toHaveTextContent(/not a confidence figure/)
      expect(panel).not.toHaveTextContent(/%/)
    })

    it('uses the singular for one claim', () => {
      render(
        <DataFidelity
          fidelity={{ ...EMPTY, grounding: { checked: 2, removed: 1, incomplete: 0, removed_for: ['db'] } }}
        />,
      )

      expect(screen.getByText(/1 ungrounded claim caught and removed/)).toBeInTheDocument()
    })

    it('is hidden when the filter caught nothing', () => {
      // A panel reading "0 ungrounded claims caught" on every clean review is noise,
      // and noise is what teaches people to stop reading these panels.
      render(
        <DataFidelity
          fidelity={{ ...EMPTY, grounding: { checked: 3, removed: 0, incomplete: 0, removed_for: [] } }}
        />,
      )

      expect(screen.queryByTestId('fidelity-grounding')).toBeNull()
    })

    it('never carries the caution tone — a caught claim is the filter working', () => {
      render(
        <DataFidelity
          fidelity={{ ...EMPTY, grounding: { checked: 20, removed: 19, incomplete: 0, removed_for: [] } }}
        />,
      )

      expect(screen.getByTestId('fidelity-grounding')).toHaveAttribute('data-tone', 'neutral')
    })
  })

  describe('only the exact figure may recommend a review', () => {
    it('a low OCR proxy alone raises no caution panel anywhere', () => {
      render(<DataFidelity fidelity={{ ...EMPTY, ocr_proxy: OCR_LOW }} />)

      expect(screen.queryByTestId('fidelity-ocr')).toHaveAttribute('data-tone', 'neutral')
      expect(
        screen.getByTestId('data-fidelity').querySelectorAll('[data-tone="caution"]'),
      ).toHaveLength(0)
    })

    it('a low structural figure still raises one', () => {
      // The other half. This change narrowed what can pull the lever; it must not
      // have disabled the lever.
      render(<DataFidelity fidelity={{ ...EMPTY, structural: LOW_STRUCTURAL }} />)

      expect(
        screen.getByTestId('data-fidelity').querySelectorAll('[data-tone="caution"]'),
      ).toHaveLength(1)
      expect(screen.getByTestId('fidelity-structural')).toHaveTextContent(/checked by hand/)
    })

    it('with both low, exactly one caution panel appears — the structural one', () => {
      render(
        <DataFidelity
          fidelity={{ ...EMPTY, structural: LOW_STRUCTURAL, ocr_proxy: OCR_LOW }}
        />,
      )

      const cautions = screen
        .getByTestId('data-fidelity')
        .querySelectorAll('[data-tone="caution"]')
      expect(cautions).toHaveLength(1)
      expect(cautions[0]).toHaveAttribute('data-testid', 'fidelity-structural')
    })
  })

  describe('the three are never blended', () => {
    it('renders three separate panels, with no combined figure', () => {
      render(
        <DataFidelity
          fidelity={{
            structural: LOW_STRUCTURAL,
            ocr_proxy: OCR_LOW,
            grounding: { checked: 5, removed: 2, incomplete: 0, removed_for: [] },
          }}
        />,
      )

      expect(screen.getByTestId('fidelity-structural')).toBeInTheDocument()
      expect(screen.getByTestId('fidelity-ocr')).toBeInTheDocument()
      expect(screen.getByTestId('fidelity-grounding')).toBeInTheDocument()

      // No wording that would imply a single blended score over the three.
      const section = screen.getByTestId('data-fidelity')
      expect(section).not.toHaveTextContent(/overall (accuracy|fidelity|coverage)/i)
      expect(section).not.toHaveTextContent(/combined/i)
    })

    it('announces as a status, matching the IngestWarnings pattern', () => {
      render(<DataFidelity fidelity={{ ...EMPTY, structural: LOW_STRUCTURAL }} />)

      expect(screen.getByRole('status', { name: 'Data fidelity' })).toBeInTheDocument()
      // A caveat on a usable result, never an alert.
      expect(screen.queryByRole('alert')).toBeNull()
    })
  })
})
