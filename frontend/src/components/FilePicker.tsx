import { useId, useRef, useState } from 'react'

interface Props {
  label: string
  hint: string
  accept: string
  file: File | null
  onChange: (file: File | null) => void
  disabled?: boolean
}

/** A labelled drop target that is also a plain file input for keyboard users. */
export function FilePicker({ label, hint, accept, file, onChange, disabled }: Props) {
  const inputId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  function handleDrop(event: React.DragEvent) {
    event.preventDefault()
    setDragging(false)
    if (disabled) return
    const dropped = event.dataTransfer.files[0]
    if (dropped) onChange(dropped)
  }

  return (
    <div>
      <label htmlFor={inputId} className="t-heading block">
        {label}
      </label>
      <p className="t-caption mt-1 max-w-prose text-ink-muted">{hint}</p>

      <div
        onDragOver={(event) => {
          event.preventDefault()
          if (!disabled) setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={[
          'mt-2.5 flex items-center justify-between gap-4 border border-dashed px-4 py-4 transition-colors duration-150',
          dragging
            ? 'border-minfy-orange bg-minfy-orange/5'
            : file
              ? 'border-solid border-hairline bg-surface-sunken'
              : 'border-hairline hover:border-ink-faint',
          disabled ? 'opacity-60' : '',
        ].join(' ')}
      >
        <div className="min-w-0">
          {file ? (
            <p className="t-body flex items-center gap-2 truncate" title={file.name}>
              <svg viewBox="0 0 16 16" aria-hidden="true" className="size-3.5 shrink-0 fill-verdict-pass">
                <path d="M8 1 A7 7 0 1 1 8 15 A7 7 0 1 1 8 1 Z M6.9 10.8 L11.8 5.9 L10.9 5 L6.9 9 L5.1 7.2 L4.2 8.1 Z" />
              </svg>
              <span className="truncate font-medium">{file.name}</span>
              <span className="tnum shrink-0 text-ink-muted">
                {formatBytes(file.size)}
              </span>
            </p>
          ) : (
            <p className="t-body text-ink-faint">Drop a file here, or choose one</p>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-3">
          {file && !disabled && (
            <button
              type="button"
              onClick={() => {
                onChange(null)
                if (inputRef.current) inputRef.current.value = ''
              }}
              className="t-caption text-ink-muted underline underline-offset-2 transition-colors hover:text-ink"
            >
              Remove
            </button>
          )}
          <button
            type="button"
            disabled={disabled}
            onClick={() => inputRef.current?.click()}
            className="t-caption border border-minfy-navy px-3 py-1.5 font-medium text-minfy-navy transition-colors duration-150 hover:bg-minfy-navy hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            Choose file
          </button>
        </div>
      </div>

      <input
        ref={inputRef}
        id={inputId}
        type="file"
        accept={accept}
        disabled={disabled}
        onChange={(event) => onChange(event.target.files?.[0] ?? null)}
        className="sr-only"
      />
    </div>
  )
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
