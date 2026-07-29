import { describe, expect, it } from 'vitest'

import { elapsedSeconds, formatElapsed } from './elapsed'

describe('elapsedSeconds', () => {
  it('counts whole seconds from the submission instant', () => {
    const start = Date.parse('2026-07-29T10:00:00Z')

    expect(elapsedSeconds(start, start)).toBe(0)
    expect(elapsedSeconds(start, start + 999)).toBe(0)
    expect(elapsedSeconds(start, start + 1000)).toBe(1)
    expect(elapsedSeconds(start, start + 84_000)).toBe(84)
  })

  it('never goes negative when the clock disagrees with itself', () => {
    // A user's system clock can step backwards mid-review (NTP, sleep/resume). A
    // negative duration would render as "-3s elapsed", which reads as a bug.
    const start = Date.parse('2026-07-29T10:00:00Z')

    expect(elapsedSeconds(start, start - 5000)).toBe(0)
  })

  it('treats an unusable instant as zero rather than NaN', () => {
    expect(elapsedSeconds(Number.NaN, Date.now())).toBe(0)
    expect(elapsedSeconds(Date.now(), Number.NaN)).toBe(0)
  })
})

describe('formatElapsed', () => {
  it('shows bare seconds under a minute', () => {
    expect(formatElapsed(0)).toBe('0s')
    // The fastest run observed in testing.
    expect(formatElapsed(14)).toBe('14s')
    expect(formatElapsed(59)).toBe('59s')
  })

  it('shows minutes and zero-padded seconds up to an hour', () => {
    expect(formatElapsed(60)).toBe('1m 00s')
    expect(formatElapsed(84)).toBe('1m 24s')
    expect(formatElapsed(3599)).toBe('59m 59s')
  })

  it('drops seconds past an hour, where they are noise', () => {
    expect(formatElapsed(3600)).toBe('1h 00m')
    expect(formatElapsed(3600 + 4 * 60 + 30)).toBe('1h 04m')
  })

  it('handles the 44-minute run that motivated this being a clock', () => {
    expect(formatElapsed(44 * 60)).toBe('44m 00s')
  })

  it('pads so the width does not jump as the value changes', () => {
    // Paired with `tnum` at the call site: "1m 04s" and "1m 24s" must occupy the
    // same width, or the row twitches once a second.
    expect(formatElapsed(64)).toHaveLength(formatElapsed(84).length)
    expect(formatElapsed(3600 + 9 * 60)).toHaveLength(formatElapsed(3600 + 59 * 60).length)
  })

  it('never renders a negative or fractional duration', () => {
    expect(formatElapsed(-10)).toBe('0s')
    expect(formatElapsed(1.9)).toBe('1s')
  })

  it('is a duration only — it implies no total and no remainder', () => {
    // The whole point: a countdown or a percentage would be wrong most of the time,
    // given latency has ranged from 14s to 44 minutes on one provider.
    for (const seconds of [5, 90, 2640]) {
      const rendered = formatElapsed(seconds)
      for (const word of ['remaining', 'left', '%', 'of', 'ETA', '/']) {
        expect(rendered).not.toContain(word)
      }
    }
  })
})
