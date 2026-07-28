/**
 * The Minfy mark, drawn as inline SVG.
 *
 * IT IS A REDRAW, NOT THE SUPPLIED ASSET. The logo was provided as an image in
 * conversation and never landed on disk in this environment, so there was no file
 * to embed. The mark is pure geometry — a blue block with a yellow square in the
 * top-right notch — which redraws exactly; the "minfy" wordmark is a custom
 * typeface and is deliberately NOT reproduced, because hand-drawing letterforms
 * would produce an imitation of the brand rather than the brand. The product name
 * sits beside the mark as type instead, which is the normal co-branding shape.
 *
 * To swap in the real asset: drop the SVG (or PNG) into the repo and replace this
 * component's body. Nothing else references the geometry.
 *
 * Inline rather than a file so it inherits `currentColor` for nothing and costs no
 * extra request; the two brand colours are theme tokens, not literals, so the mark
 * cannot drift from the palette.
 */

interface Props {
  /** Rendered size in px. The mark is square. */
  size?: number
  /**
   * `onDark` swaps the blue block for white. Brand blue on the navy header bar is
   * near-invisible, and an illegible logo is worse than a monochrome one.
   */
  tone?: 'brand' | 'onDark'
  className?: string
}

export function MinfyMark({ size = 28, tone = 'brand', className }: Props) {
  const block = tone === 'onDark' ? '#ffffff' : 'var(--color-minfy-blue)'

  return (
    <svg
      viewBox="0 0 100 100"
      width={size}
      height={size}
      className={className}
      role="img"
      aria-label="Minfy"
    >
      {/*
        The blue block: the full square with the top-right corner notched out.
        Drawn as one path so the notch is a hole in the shape rather than three
        rectangles that could drift apart.
      */}
      <path d="M0 0 H46 V50 H100 V100 H0 Z" fill={block} />
      {/* The yellow square sitting in the notch, inset so white reads around it. */}
      <rect x="54" y="0" width="46" height="42" fill="var(--color-minfy-yellow)" />
    </svg>
  )
}
