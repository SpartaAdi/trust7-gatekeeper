/**
 * Model-written text, rendered as a list when it was written as one.
 *
 * The prompts now ask for bullets where scanning beats prose — the assessment,
 * and any remediation with distinct steps. Both fields are plain strings that
 * were previously dropped into a single `<p>`, where newlines collapse and a
 * leading "- " shows up as a literal dash in a run-on sentence.
 *
 * Detection rather than a schema change, deliberately. `summary` and
 * `remediation` are strings on every review already stored, and turning either
 * into an array would need a migration for data on a disk that does not survive
 * a restart anyway. Text that is not bullet-shaped renders exactly as it did
 * before, so an older review is unaffected and a model that answers in prose is
 * not mangled into a one-item list.
 *
 * Markers are stripped, never rendered — the list marker is the browser's job.
 */

const BULLET = /^\s*[-•*•]\s+/
const ORDINAL = /^\s*\d+[.)]\s+/

/** At least two marked lines: one is a sentence that happens to start with a dash. */
const MIN_ITEMS = 2

interface Parsed {
  kind: 'bullet' | 'ordered' | 'prose'
  items: string[]
}

export function parseStructured(text: string): Parsed {
  const lines = text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '')

  const bulleted = lines.filter((line) => BULLET.test(line))
  const ordered = lines.filter((line) => ORDINAL.test(line))

  // Whichever marker the model actually used, and only when every non-empty line
  // carries it — a paragraph followed by two bullets is prose with a list in it,
  // and splitting that would silently drop the paragraph.
  if (bulleted.length >= MIN_ITEMS && bulleted.length === lines.length) {
    return { kind: 'bullet', items: lines.map((line) => line.replace(BULLET, '')) }
  }
  if (ordered.length >= MIN_ITEMS && ordered.length === lines.length) {
    return { kind: 'ordered', items: lines.map((line) => line.replace(ORDINAL, '')) }
  }
  return { kind: 'prose', items: [text] }
}

export function StructuredText({
  text,
  className = '',
  listClassName = '',
}: {
  text: string
  className?: string
  listClassName?: string
}) {
  const parsed = parseStructured(text)

  if (parsed.kind === 'prose') {
    return <p className={className}>{parsed.items[0]}</p>
  }

  const List = parsed.kind === 'ordered' ? 'ol' : 'ul'
  return (
    <List
      className={`${className} ${listClassName} ${
        parsed.kind === 'ordered' ? 'list-decimal' : 'list-disc'
      } space-y-1 pl-5`.trim()}
    >
      {parsed.items.map((item, index) => (
        <li key={index}>{item}</li>
      ))}
    </List>
  )
}
