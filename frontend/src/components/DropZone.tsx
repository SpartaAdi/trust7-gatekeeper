import { useId, useRef, useState } from 'react'

import { KIND_LABEL, diagramRoute, type FileKind } from '../fileKind'

export interface StagedFile {
  id: string
  file: File
  kind: FileKind
  /** True when `kind` came from the extension rather than the user. */
  autoDetected: boolean
}

interface Props {
  files: StagedFile[]
  onAdd: (files: File[]) => void
  onRemove: (id: string) => void
  onReclassify: (id: string, kind: FileKind) => void
  disabled?: boolean
}

const ACCEPT =
  '.pdf,.docx,.txt,.rst,.md,.markdown,.csv,.json,.yaml,.yml,.drawio,.xml,.png,.jpg,.jpeg,.gif,.webp'

export function DropZone({ files, onAdd, onRemove, onReclassify, disabled }: Props) {
  const inputId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  function handleDrop(event: React.DragEvent) {
    event.preventDefault()
    setDragging(false)
    if (disabled) return
    onAdd(Array.from(event.dataTransfer.files))
  }

  return (
    <div>
      <div
        onDragOver={(event) => {
          event.preventDefault()
          if (!disabled) setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={[
          'flex flex-col items-center justify-center border border-dashed px-6 py-10 text-center transition-colors duration-150',
          dragging
            ? 'border-minfy-orange bg-minfy-orange/5'
            : 'border-hairline hover:border-ink-faint',
          disabled ? 'opacity-60' : '',
        ].join(' ')}
      >
        <svg
          viewBox="0 0 24 24"
          aria-hidden="true"
          className="size-7 fill-none stroke-ink-faint stroke-[1.5]"
        >
          <path
            d="M12 16V4m0 0L8 8m4-4 4 4M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>

        <p className="t-body mt-3 font-medium">
          Drop your solution document and architecture diagram here
        </p>
        <p className="t-caption mt-1 max-w-md text-ink-muted">
          Both at once is fine — each file is sorted by type. PDF, DOCX, or text
          for the SoW; draw.io or an image for the diagram.
        </p>

        <label htmlFor={inputId} className="mt-4">
          <span
            className={`t-caption inline-block border border-minfy-navy px-3.5 py-1.5 font-medium text-minfy-navy transition-colors duration-150 ${
              disabled
                ? 'cursor-not-allowed opacity-50'
                : 'cursor-pointer hover:bg-minfy-navy hover:text-white'
            }`}
          >
            Choose files
          </span>
        </label>
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          multiple
          accept={ACCEPT}
          disabled={disabled}
          onChange={(event) => {
            onAdd(Array.from(event.target.files ?? []))
            // Reset so re-picking the same file still fires a change event.
            if (inputRef.current) inputRef.current.value = ''
          }}
          className="sr-only"
        />
      </div>

      {files.length > 0 && (
        <ul className="mt-4 divide-y divide-hairline border-y border-hairline">
          {files.map((staged) => (
            <StagedRow
              key={staged.id}
              staged={staged}
              onRemove={onRemove}
              onReclassify={onReclassify}
              disabled={disabled}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

function StagedRow({
  staged,
  onRemove,
  onReclassify,
  disabled,
}: {
  staged: StagedFile
  onRemove: (id: string) => void
  onReclassify: (id: string, kind: FileKind) => void
  disabled?: boolean
}) {
  const selectId = useId()

  return (
    <li className="animate-enter flex items-center gap-3 py-3">
      <span className="min-w-0 flex-1">
        <span className="t-body block truncate font-medium" title={staged.file.name}>
          {staged.file.name}
        </span>
        <span className="t-caption text-ink-faint">
          {formatBytes(staged.file.size)}
          {staged.kind === 'diagram' && (
            <> · {diagramRoute(staged.file.name)}</>
          )}
        </span>
      </span>

      {staged.kind === 'unknown' ? (
        // Not a guess we can make safely: sending a SoW down the diagram path
        // produces a confident, wrong review.
        <span className="flex shrink-0 items-center gap-2">
          <label htmlFor={selectId} className="t-caption text-sev-medium">
            Which is it?
          </label>
          <select
            id={selectId}
            disabled={disabled}
            defaultValue=""
            onChange={(event) =>
              onReclassify(staged.id, event.target.value as FileKind)
            }
            className="t-caption border border-sev-medium bg-surface px-2 py-1"
          >
            <option value="" disabled>
              Choose…
            </option>
            <option value="document">SoW</option>
            <option value="diagram">Diagram</option>
          </select>
        </span>
      ) : (
        <span
          className={`t-eyebrow shrink-0 border px-2 py-0.5 text-[10px] tracking-wider ${
            staged.kind === 'document'
              ? 'border-minfy-navy/30 bg-minfy-navy/5 text-minfy-navy'
              : 'border-minfy-orange/40 bg-minfy-orange/8 text-minfy-orange'
          }`}
        >
          {KIND_LABEL[staged.kind]}
          {!staged.autoDetected && <span className="normal-case"> (set)</span>}
        </span>
      )}

      <button
        type="button"
        disabled={disabled}
        onClick={() => onRemove(staged.id)}
        aria-label={`Remove ${staged.file.name}`}
        className="t-caption shrink-0 text-ink-muted underline underline-offset-2 transition-colors hover:text-ink disabled:opacity-50"
      >
        Remove
      </button>
    </li>
  )
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
