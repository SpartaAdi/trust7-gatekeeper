import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { StructuredText, parseStructured } from './StructuredText'

describe('parseStructured', () => {
  it('reads prose as prose', () => {
    const text = 'Enable SSE-KMS on the orders table and re-encrypt existing snapshots.'

    expect(parseStructured(text)).toEqual({ kind: 'prose', items: [text] })
  })

  it('reads dash bullets as a list, stripping the markers', () => {
    const parsed = parseStructured('- First thing.\n- Second thing.\n- Third thing.')

    expect(parsed.kind).toBe('bullet')
    expect(parsed.items).toEqual(['First thing.', 'Second thing.', 'Third thing.'])
  })

  it('reads numbered steps as an ordered list', () => {
    const parsed = parseStructured('1. Create the key.\n2. Attach the policy.')

    expect(parsed.kind).toBe('ordered')
    expect(parsed.items).toEqual(['Create the key.', 'Attach the policy.'])
  })

  /**
   * One line beginning with a dash is a sentence, not a list. Splitting on a
   * single marker would turn "— see the runbook" into a one-item bullet.
   */
  it('needs at least two marked lines', () => {
    expect(parseStructured('- Only one line here.').kind).toBe('prose')
  })

  /**
   * A paragraph followed by bullets is prose containing a list. Splitting it
   * would silently drop the paragraph, which is worse than not splitting.
   */
  it('leaves a mixed block alone rather than dropping the paragraph', () => {
    const mixed = 'The store is unencrypted.\n- Enable SSE-KMS.\n- Rotate the key.'

    const parsed = parseStructured(mixed)

    expect(parsed.kind).toBe('prose')
    expect(parsed.items[0]).toContain('The store is unencrypted.')
  })

  it('ignores blank lines between items', () => {
    expect(parseStructured('- One.\n\n- Two.\n').items).toEqual(['One.', 'Two.'])
  })

  it('handles an empty string without inventing an item', () => {
    expect(parseStructured('')).toEqual({ kind: 'prose', items: [''] })
  })
})

describe('StructuredText', () => {
  it('renders prose as a paragraph, exactly as before', () => {
    const { container } = render(<StructuredText text="One sentence of prose." />)

    expect(container.querySelector('p')).toHaveTextContent('One sentence of prose.')
    expect(container.querySelector('ul')).toBeNull()
  })

  it('renders bullets as a real list, so screen readers announce the count', () => {
    render(<StructuredText text={"- Alpha.\n- Beta."} />)

    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('Alpha.')
  })

  it('renders numbered steps as an ordered list', () => {
    const { container } = render(<StructuredText text={"1. First.\n2. Second."} />)

    expect(container.querySelector('ol')).not.toBeNull()
    expect(container.querySelector('ul')).toBeNull()
  })

  it('never shows the raw marker', () => {
    const { container } = render(<StructuredText text={"- Alpha.\n- Beta."} />)

    expect(container.textContent).not.toContain('- ')
  })

  it('keeps the caller’s typography class on either shape', () => {
    const { container: prose } = render(
      <StructuredText text="Prose." className="t-body" />,
    )
    const { container: list } = render(
      <StructuredText text={"- A.\n- B."} className="t-body" />,
    )

    expect(prose.querySelector('p')!.className).toContain('t-body')
    expect(list.querySelector('ul')!.className).toContain('t-body')
  })
})
