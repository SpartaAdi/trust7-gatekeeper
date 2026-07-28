import { describe, expect, it } from 'vitest'

import {
  TOTAL_WEIGHT_FOR_TEST,
  WEIGHTS_FOR_TEST,
  estimateRemainingSeconds,
  formatRemaining,
} from './eta'
import { STAGE_NAMES, type StageName, type StageProgress } from './types'

const T0 = Date.parse('2026-07-27T10:00:00Z')
const at = (offsetSeconds: number) => new Date(T0 + offsetSeconds * 1000).toISOString()

function stage(
  name: StageName,
  state: StageProgress['state'],
  startedOffset?: number,
  finishedOffset?: number,
): StageProgress {
  return {
    name,
    state,
    detail: '',
    started_at: startedOffset === undefined ? '' : at(startedOffset),
    finished_at: finishedOffset === undefined ? '' : at(finishedOffset),
  }
}

describe('estimateRemainingSeconds', () => {
  it('has no estimate before any stage is reported', () => {
    expect(estimateRemainingSeconds([], T0)).toBeNull()
  })

  it('has no estimate once every stage is done', () => {
    const stages = STAGE_NAMES.map((name) => stage(name, 'done', 0, 5))
    expect(estimateRemainingSeconds(stages, T0 + 60_000)).toBeNull()
  })

  it('estimates from the assumed rate before any stage has finished', () => {
    // Nothing measured yet, so the whole weight is priced at the fallback rate.
    const stages = STAGE_NAMES.map((name, index) =>
      index === 0 ? stage(name, 'running', 0) : stage(name, 'pending'),
    )
    const remaining = estimateRemainingSeconds(stages, T0)

    expect(remaining).toBe(Math.round(TOTAL_WEIGHT_FOR_TEST * 4.2))
  })

  it('calibrates the rate against how long this run actually took', () => {
    // ingest (weight 1) took 10s, so this run is priced at 10s per weight unit —
    // more than double the fallback. The estimate must follow the measurement,
    // not the assumption, which is the whole point of calibrating.
    const stages: StageProgress[] = [
      stage('ingest', 'done', 0, 10),
      stage('normalize', 'pending'),
      stage('classify', 'pending'),
      stage('evaluate', 'pending'),
      stage('prioritize', 'pending'),
      stage('remediate', 'pending'),
    ]
    const outstanding = TOTAL_WEIGHT_FOR_TEST - WEIGHTS_FOR_TEST.ingest

    expect(estimateRemainingSeconds(stages, T0 + 10_000)).toBe(outstanding * 10)
  })

  it('counts the running stage down as it runs', () => {
    const stages: StageProgress[] = [
      stage('ingest', 'done', 0, 10),
      stage('normalize', 'running', 10),
      stage('classify', 'pending'),
      stage('evaluate', 'pending'),
      stage('prioritize', 'pending'),
      stage('remediate', 'pending'),
    ]

    const atStart = estimateRemainingSeconds(stages, T0 + 10_000)
    const thirtySecondsLater = estimateRemainingSeconds(stages, T0 + 40_000)

    expect(atStart).not.toBeNull()
    expect(thirtySecondsLater).toBe(atStart! - 30)
  })

  it('never counts below the floor, however long a stage overruns', () => {
    const stages: StageProgress[] = [
      stage('ingest', 'done', 0, 1),
      stage('normalize', 'done', 1, 2),
      stage('classify', 'done', 2, 3),
      stage('evaluate', 'done', 3, 4),
      stage('prioritize', 'done', 4, 5),
      // Still running an hour past a one-second-per-unit projection.
      stage('remediate', 'running', 5),
    ]

    expect(estimateRemainingSeconds(stages, T0 + 3_600_000)).toBe(10)
  })

  it('ignores done stages whose timestamps are unusable', () => {
    // `normalize` in the real status payload can arrive done with empty
    // timestamps. Calibration must skip it rather than treat it as instant,
    // which would drag the estimated rate towards zero.
    const withBlank: StageProgress[] = [
      stage('ingest', 'done', 0, 10),
      stage('normalize', 'done'),
      stage('classify', 'pending'),
      stage('evaluate', 'pending'),
      stage('prioritize', 'pending'),
      stage('remediate', 'pending'),
    ]
    const outstanding =
      TOTAL_WEIGHT_FOR_TEST - WEIGHTS_FOR_TEST.ingest - WEIGHTS_FOR_TEST.normalize

    expect(estimateRemainingSeconds(withBlank, T0 + 10_000)).toBe(outstanding * 10)
  })

  it('weights the stages rather than counting them', () => {
    // Two runs, same "one of six done", same elapsed time — but different stages
    // outstanding. A count-based estimate would give these the same answer.
    const evaluateOutstanding: StageProgress[] = [
      stage('ingest', 'done', 0, 10),
      stage('evaluate', 'pending'),
    ]
    const normalizeOutstanding: StageProgress[] = [
      stage('ingest', 'done', 0, 10),
      stage('normalize', 'pending'),
    ]

    const heavy = estimateRemainingSeconds(evaluateOutstanding, T0 + 10_000)
    const light = estimateRemainingSeconds(normalizeOutstanding, T0 + 10_000)

    expect(heavy).toBe(WEIGHTS_FOR_TEST.evaluate * 10)
    expect(light).toBe(WEIGHTS_FOR_TEST.normalize * 10)
    expect(heavy!).toBeGreaterThan(light!)
  })

  it('treats an unknown stage name as light rather than crashing', () => {
    const stages = [
      stage('ingest', 'done', 0, 10),
      { ...stage('ingest', 'pending'), name: 'summarize' as StageName },
    ]

    expect(estimateRemainingSeconds(stages, T0 + 10_000)).toBe(10)
  })
})

describe('formatRemaining', () => {
  it('steps in five-second increments below a minute', () => {
    expect(formatRemaining(11)).toBe('about 15s remaining')
    expect(formatRemaining(45)).toBe('about 45s remaining')
  })

  it('never rounds down to zero', () => {
    expect(formatRemaining(1)).toBe('about 5s remaining')
  })

  it('rounds to the half minute in the minutes range', () => {
    expect(formatRemaining(60)).toBe('about 1 min remaining')
    expect(formatRemaining(105)).toBe('about 2 min remaining')
    expect(formatRemaining(100)).toBe('about 1.5 min remaining')
  })

  it('rounds to whole minutes once past ten', () => {
    expect(formatRemaining(52 * 60 + 20)).toBe('about 52 min remaining')
  })

  it('says "about" at every scale, because the estimate is an approximation', () => {
    for (const seconds of [8, 59, 61, 400, 4000]) {
      expect(formatRemaining(seconds)).toContain('about')
    }
  })
})
