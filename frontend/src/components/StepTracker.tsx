export type Step = 1 | 2 | 3

const STEPS: readonly { step: Step; label: string }[] = [
  { step: 1, label: 'Upload' },
  { step: 2, label: 'Analyzing' },
  { step: 3, label: 'Results' },
]

interface Props {
  current: Step
}

export function StepTracker({ current }: Props) {
  return (
    <nav aria-label="Progress" className="border-b border-hairline">
      <ol className="mx-auto flex max-w-5xl items-center gap-3 px-6 py-4">
        {STEPS.map(({ step, label }, index) => {
          const done = step < current
          const active = step === current
          return (
            <li key={step} className="flex flex-1 items-center gap-3">
              <div className="flex items-center gap-2.5">
                <span
                  aria-hidden="true"
                  className={[
                    'flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold',
                    done
                      ? 'bg-minfy-navy text-white'
                      : active
                        ? 'bg-minfy-orange text-white'
                        : 'border border-hairline text-ink-muted',
                  ].join(' ')}
                >
                  {done ? '✓' : step}
                </span>
                <span
                  className={[
                    'text-sm whitespace-nowrap',
                    active
                      ? 'font-semibold text-ink'
                      : done
                        ? 'text-ink'
                        : 'text-ink-muted',
                  ].join(' ')}
                >
                  <span className="text-ink-muted">Step {step}</span>{' '}
                  {label}
                </span>
              </div>
              {index < STEPS.length - 1 && (
                <span
                  aria-hidden="true"
                  className={`h-px flex-1 ${done ? 'bg-minfy-navy' : 'bg-hairline'}`}
                />
              )}
              {active && <span className="sr-only">(current step)</span>}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
