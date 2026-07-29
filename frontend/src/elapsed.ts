/**
 * Wall-clock duration formatting for the in-progress review.
 *
 * A CLOCK, deliberately — not an estimate and not a percentage. Observed latency for
 * one review has ranged from 14 seconds to 44 minutes on the same provider, so any
 * display implying a known total duration is wrong most of the time, and a progress
 * indicator that visibly disagrees with reality reads as a broken app rather than as
 * a slow one. Elapsed time is the one number that is always true.
 *
 * That range is also why the format has three tiers rather than one: `84s` is hard to
 * read at a glance and `0h 0m 14s` is silly, so the unit grows with the magnitude.
 */

/** Seconds elapsed between two epoch-millisecond instants. Never negative. */
export function elapsedSeconds(startedAt: number, nowMs: number): number {
  if (!Number.isFinite(startedAt) || !Number.isFinite(nowMs)) return 0
  return Math.max(0, Math.floor((nowMs - startedAt) / 1000))
}

/**
 * `14s`, `1m 24s`, `1h 04m`.
 *
 * Seconds are dropped past an hour: at that scale they are noise, and a run that long
 * has already told the reviewer what they need to know.
 */
export function formatElapsed(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))

  if (whole < 60) return `${whole}s`

  const minutes = Math.floor(whole / 60)
  if (minutes < 60) return `${minutes}m ${String(whole % 60).padStart(2, '0')}s`

  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`
}
