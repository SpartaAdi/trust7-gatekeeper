/**
 * Estimated time remaining for an in-flight review.
 *
 * A review is six stages of wildly unequal length — `ingest` is a file read,
 * `evaluate` is 45 rubric checks across two frameworks and dominates the run — so
 * "stages done / stages total" is a poor clock even though it is a fine progress
 * bar. The estimate below therefore weights the stages, and calibrates those
 * weights against how long *this* run's finished stages actually took.
 *
 * Why calibrate rather than hardcode seconds: latency varies by an order of
 * magnitude with provider routing, prompt size, and whether Render cold-started.
 * A fixed "about 3 minutes" would be wrong most of the time and would not move.
 * Scaling a shape by one measured constant is the smallest thing that adapts.
 *
 * The estimate is deliberately conservative and honest about not knowing:
 * `null` means "not enough information yet", and the caller shows nothing rather
 * than a made-up number.
 */

import { STAGE_NAMES, type StageName, type StageProgress } from './types'

/**
 * Relative cost of each stage. Units are arbitrary — only the ratios matter,
 * because the scale factor is measured per run.
 *
 * Shape, not measurement: `evaluate` is by far the largest because it asks for
 * the most output tokens (64,000 against 16,000–32,000 elsewhere), `remediate` is
 * next, and the two I/O stages are near-instant. Adjust these if real runs show a
 * different distribution; nothing else needs to change.
 */
const STAGE_WEIGHT: Record<StageName, number> = {
  ingest: 1,
  normalize: 1,
  classify: 6,
  evaluate: 22,
  prioritize: 5,
  remediate: 8,
}

/**
 * Fallback seconds-per-weight-unit, used only before any stage has finished.
 *
 * Derived from the observed shape of a real run: ~43 weight units over roughly
 * three minutes. It is replaced by the measured value as soon as the first stage
 * completes, so it governs the first few seconds of the screen and nothing more.
 */
const ASSUMED_SECONDS_PER_UNIT = 4.2

/** Never claim less than this; "1s remaining" that then sits there reads as broken. */
const FLOOR_SECONDS = 10

function weightOf(name: string): number {
  return STAGE_WEIGHT[name as StageName] ?? 1
}

/**
 * Seconds of work left, or `null` when there is nothing worth showing —
 * no stages reported yet, or every stage already finished.
 *
 * `nowMs` is passed in rather than read from the clock so this is a pure
 * function: the caller ticks, and the tests do not have to fake time.
 */
export function estimateRemainingSeconds(
  stages: readonly StageProgress[],
  nowMs: number,
): number | null {
  if (stages.length === 0) return null

  let doneWeight = 0
  let doneSeconds = 0
  let remainingWeight = 0
  // How long the currently running stage has already been running, which is
  // work already spent and must come off the estimate — otherwise the number
  // stalls instead of counting down between stage transitions.
  let inFlightSeconds = 0

  for (const stage of stages) {
    const weight = weightOf(stage.name)

    if (stage.state === 'done') {
      const measured = spanSeconds(stage.started_at, stage.finished_at)
      // A stage with unusable timestamps still counts toward progress; it just
      // contributes nothing to the calibration.
      if (measured !== null) {
        doneWeight += weight
        doneSeconds += measured
      }
      continue
    }

    remainingWeight += weight
    if (stage.state === 'running') {
      const elapsed = spanSeconds(stage.started_at, new Date(nowMs).toISOString())
      if (elapsed !== null) inFlightSeconds = elapsed
    }
  }

  if (remainingWeight === 0) return null

  const secondsPerUnit =
    doneWeight > 0 && doneSeconds > 0
      ? doneSeconds / doneWeight
      : ASSUMED_SECONDS_PER_UNIT

  const remaining = remainingWeight * secondsPerUnit - inFlightSeconds
  return Math.max(FLOOR_SECONDS, Math.round(remaining))
}

/**
 * Human phrasing, rounded to a granularity the estimate can actually support.
 *
 * Anything above a minute is rounded to the half-minute and read as "about",
 * because a to-the-second countdown on a projection this rough is a false
 * precision the user would (rightly) catch us out on.
 */
export function formatRemaining(seconds: number): string {
  if (seconds < 60) {
    // Below a minute, round up to 5s so the label steps rather than flickers.
    return `about ${Math.max(5, Math.ceil(seconds / 5) * 5)}s remaining`
  }
  const minutes = seconds / 60
  if (minutes < 10) {
    const halves = Math.round(minutes * 2) / 2
    const shown = halves < 0.5 ? 0.5 : halves
    return `about ${Number.isInteger(shown) ? shown : shown.toFixed(1)} min remaining`
  }
  return `about ${Math.round(minutes)} min remaining`
}

/** Elapsed seconds between two ISO timestamps, or `null` if either is unusable. */
function spanSeconds(fromIso: string, toIso: string): number | null {
  if (!fromIso || !toIso) return null
  const from = Date.parse(fromIso)
  const to = Date.parse(toIso)
  if (Number.isNaN(from) || Number.isNaN(to)) return null
  return Math.max(0, (to - from) / 1000)
}

/** Exported for the tests, so the weight shape is asserted rather than assumed. */
export const WEIGHTS_FOR_TEST: Readonly<Record<StageName, number>> = STAGE_WEIGHT
export const TOTAL_WEIGHT_FOR_TEST = STAGE_NAMES.reduce(
  (total, name) => total + STAGE_WEIGHT[name],
  0,
)
