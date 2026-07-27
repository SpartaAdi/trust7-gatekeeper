/**
 * Which slot a dropped file belongs in.
 *
 * `unknown` is a first-class outcome, not a failure: guessing wrong sends a
 * design document down the diagram path, where it is parsed as XML or handed to
 * vision, and the review comes back nonsense. When the extension does not settle
 * it, the UI asks.
 */
export type FileKind = 'document' | 'diagram' | 'unknown'

/** Unambiguously a written document. */
const DOCUMENT_EXTENSIONS = new Set(['pdf', 'docx', 'txt', 'rst'])

/** Unambiguously a diagram: draw.io XML, or an image for the vision path. */
const DIAGRAM_EXTENSIONS = new Set([
  'drawio',
  'xml',
  'png',
  'jpg',
  'jpeg',
  'gif',
  'webp',
])

/**
 * Accepted by the backend but genuinely either — a `.md` file is usually a SoW,
 * and `.json`/`.yaml` could be a document or an exported diagram. Ask rather
 * than assume.
 */
const AMBIGUOUS_EXTENSIONS = new Set(['md', 'markdown', 'json', 'yaml', 'yml', 'csv'])

export function extensionOf(filename: string): string {
  const parts = filename.toLowerCase().split('.')
  return parts.length > 1 ? (parts.pop() ?? '') : ''
}

export function classify(filename: string): FileKind {
  const extension = extensionOf(filename)
  if (DOCUMENT_EXTENSIONS.has(extension)) return 'document'
  if (DIAGRAM_EXTENSIONS.has(extension)) return 'diagram'
  return 'unknown'
}

/** True when the backend would reject the file outright. */
export function isSupported(filename: string): boolean {
  const extension = extensionOf(filename)
  return (
    DOCUMENT_EXTENSIONS.has(extension) ||
    DIAGRAM_EXTENSIONS.has(extension) ||
    AMBIGUOUS_EXTENSIONS.has(extension)
  )
}

export const KIND_LABEL: Record<Exclude<FileKind, 'unknown'>, string> = {
  document: 'SoW',
  diagram: 'Diagram',
}

/** How the diagram will be read, which is worth surfacing: one path costs tokens. */
export function diagramRoute(filename: string): string {
  const extension = extensionOf(filename)
  if (extension === 'drawio' || extension === 'xml') return 'parsed directly'
  return 'read with vision'
}
