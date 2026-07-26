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
      <label htmlFor={inputId} className="block text-sm font-medium text-ink">
        {label}
      </label>
      <p className="mt-1 text-xs text-ink-muted">{hint}</p>

      <div
        onDragOver={(event) => {
          event.preventDefault()
          if (!disabled) setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={[
          'mt-2 flex items-center justify-between gap-4 border border-dashed px-4 py-3.5 transition-colors',
          dragging ? 'border-minfy-orange bg-surface-sunken' : 'border-hairline',
          disabled ? 'opacity-60' : '',
        ].join(' ')}
      >
        <div className="min-w-0">
          {file ? (
            <p className="truncate text-sm text-ink" title={file.name}>
              {file.name}{' '}
              <span className="text-ink-muted tnum">
                ({formatBytes(file.size)})
              </span>
            </p>
          ) : (
            <p className="text-sm text-ink-muted">
              Drop a file here, or choose one
            </p>
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
              className="text-xs text-ink-muted underline underline-offset-2 hover:text-ink"
            >
              Remove
            </button>
          )}
          <button
            type="button"
            disabled={disabled}
            onClick={() => inputRef.current?.click()}
            className="border border-minfy-navy px-3 py-1.5 text-xs font-medium text-minfy-navy hover:bg-minfy-navy hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
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
