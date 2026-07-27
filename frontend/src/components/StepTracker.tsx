export type Step = 1 | 2 | 3

const STEPS: readonly { step: Step; label: string; hint: string }[] = [
  { step: 1, label: 'Upload', hint: 'Document and diagram' },
  { step: 2, label: 'Analyzing', hint: 'Six pipeline stages' },
  { step: 3, label: 'Results', hint: 'Scores and findings' },
]

interface Props {
  current: Step
}

export function StepTracker({ current }: Props) {
  return (
    <nav aria-label="Progress" className="border-b border-hairline bg-surface-sunken">
      <ol className="mx-auto flex max-w-5xl items-center gap-2 px-6 py-3.5 sm:gap-4">
        {STEPS.map(({ step, label, hint }, index) => {
          const done = step < current
          const active = step === current
          return (
            <li key={step} className="flex min-w-0 flex-1 items-center gap-2 sm:gap-4">
              <div className="flex min-w-0 items-center gap-2.5">
                <span
                  aria-hidden="true"
                  className={[
                    'flex size-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold transition-colors duration-200',
                    done
                      ? 'bg-minfy-navy text-white'
                      : active
                        ? 'bg-minfy-orange text-white ring-4 ring-minfy-orange/15'
                        : 'border border-hairline bg-surface text-ink-faint',
                  ].join(' ')}
                >
                  {done ? (
                    <svg viewBox="0 0 12 12" className="size-3 fill-none stroke-current stroke-2">
                      <path d="M2.5 6.2 L4.8 8.5 L9.5 3.6" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  ) : (
                    step
                  )}
                </span>

                <span className="min-w-0">
                  <span
                    className={[
                      't-caption block truncate font-medium transition-colors duration-200',
                      active ? 'text-ink' : done ? 'text-ink-muted' : 'text-ink-faint',
                    ].join(' ')}
                  >
                    {label}
                  </span>
                  {/* Second line is context, not decoration — it tells a first-time
                      user what each step will ask of them. Hidden when cramped. */}
                  <span className="t-caption hidden truncate text-[11px] text-ink-faint lg:block">
                    {hint}
                  </span>
                </span>
                {active && <span className="sr-only">(current step)</span>}
              </div>

              {index < STEPS.length - 1 && (
                <span
                  aria-hidden="true"
                  className={`h-px min-w-4 flex-1 transition-colors duration-300 ${
                    done ? 'bg-minfy-navy/40' : 'bg-hairline'
                  }`}
                />
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
