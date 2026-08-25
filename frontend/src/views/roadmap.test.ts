import { describe, expect, it } from 'vitest'

import type { Finding } from '../types'
import cases from '../../../fixtures/roadmap_cases.json'
import { PHASE_ORDER, groupByPhase, phaseFor, type Phase } from './roadmap'

/**
 * The cases live in a JSON file shared with backend/tests/test_roadmap.py, because
 * the rule is implemented twice — TypeScript for the results view, Python for the
 * PDF — and two copies of one rule drift unless something pins them together.
 */
const CASES = cases as unknown as {
  cases: { name: string; expect: Phase; finding: Partial<Finding> }[]
  excluded: { name: string; finding: Partial<Finding> }[]
}

function finding(overrides: Partial<Finding>): Finding {
  return {
    framework: 'aws_waf',
    pillar_id: 'security',
    check_id: 'SEC-1',
    status: 'fail',
    severity: 'medium',
    title: 'A gap',
    evidence: '',
    affected_components: [],
    remediation: '',
    remediation_effort: '',
    remediation_grounded_in: '',
    priority: 0,
    confidence: '',
    ...overrides,
  }
}

const shared = CASES.cases.map((entry) => ({ ...entry, built: finding(entry.finding) }))

describe('phaseFor — the shared rule', () => {
  for (const entry of shared) {
    it(entry.name, () => {
      expect(phaseFor(entry.built)).toBe(entry.expect)
    })
  }
})

describe('phaseFor — the rule stated directly', () => {
  it('sends high effort to structural regardless of severity', () => {
    for (const severity of ['high', 'medium', 'low'] as const) {
      expect(phaseFor(finding({ remediation_effort: 'high', severity }))).toBe('structural')
    }
  })

  it('sends anything touching two or more components to structural', () => {
    expect(
      phaseFor(finding({ remediation_effort: 'low', affected_components: ['a', 'b'] })),
    ).toBe('structural')
    // One component is not a span.
    expect(
      phaseFor(
        finding({ remediation_effort: 'low', severity: 'high', affected_components: ['a'] }),
      ),
    ).toBe('immediate')
  })

  it('requires BOTH cheap and high-severity for immediate', () => {
    expect(phaseFor(finding({ remediation_effort: 'low', severity: 'high' }))).toBe('immediate')
    // Cheap but not urgent.
    expect(phaseFor(finding({ remediation_effort: 'low', severity: 'medium' }))).toBe('short_term')
    // Urgent but not cheap.
    expect(phaseFor(finding({ remediation_effort: 'medium', severity: 'high' }))).toBe('short_term')
  })

  it('never treats an absent effort as low', () => {
    // The intent this test always had, now enforced properly. It used to assert
    // 'short_term', which honoured the name only narrowly: short_term is not "low",
    // but its blurb still claims the fix is "a component or flow change" — an amount
    // of work, asserted from data that contains none.
    for (const severity of ['high', 'medium', 'low'] as const) {
      expect(phaseFor(finding({ remediation_effort: '', severity }))).toBe('unspecified')
    }
  })

  it('puts an absent effort in no bucket that claims an amount of work', () => {
    const claimsEffort = ['immediate', 'short_term', 'structural']
    for (const severity of ['high', 'medium', 'low'] as const) {
      expect(claimsEffort).not.toContain(
        phaseFor(finding({ remediation_effort: '', severity })),
      )
    }
  })

  it('still lets blast radius override an absent effort, because it is measured', () => {
    // The one signal `unspecified` must not swallow: two or more components is a
    // structural change whatever the estimate says.
    expect(
      phaseFor(
        finding({
          remediation_effort: '',
          remediation_grounded_in: '',
          severity: 'low',
          affected_components: ['api', 'db'],
        }),
      ),
    ).toBe('structural')
  })

  it('does not swallow a real estimate', () => {
    expect(phaseFor(finding({ remediation_effort: 'low', severity: 'high' }))).toBe('immediate')
    expect(phaseFor(finding({ remediation_effort: 'medium', severity: 'medium' }))).toBe('short_term')
    expect(phaseFor(finding({ remediation_effort: 'high', severity: 'low' }))).toBe('structural')
  })

  it('ignores the pillar', () => {
    // The rejected alternative was routing Operational Excellence and Reliability to
    // structural. These two are the cheap fixes in those pillars; filing them as
    // architecture work would be wrong.
    const cheap = { remediation_effort: 'low' as const, severity: 'high' as const }
    expect(phaseFor(finding({ ...cheap, pillar_id: 'operational_excellence' }))).toBe('immediate')
    expect(phaseFor(finding({ ...cheap, pillar_id: 'reliability' }))).toBe('immediate')
    // And the same finding shape lands identically whichever pillar it came from.
    const phases = new Set(
      ['security', 'reliability', 'operational_excellence', 'cost_optimization'].map(
        (pillar_id) => phaseFor(finding({ ...cheap, pillar_id })),
      ),
    )
    expect(phases.size).toBe(1)
  })
})

describe('groupByPhase', () => {
  const all = shared.map((entry) => entry.built)

  it('partitions the open findings — none dropped, none duplicated', () => {
    const grouped = groupByPhase(all)
    const placed = PHASE_ORDER.flatMap((phase) => grouped[phase]).map((f) => f.check_id)

    expect(placed).toHaveLength(all.length)
    expect(new Set(placed).size).toBe(all.length)
    expect([...placed].sort()).toEqual(all.map((f) => f.check_id).sort())
  })

  it('leaves passing and not-applicable checks off the roadmap entirely', () => {
    const closed = CASES.excluded.map((entry) => finding(entry.finding))
    const grouped = groupByPhase([...all, ...closed])
    const placed = PHASE_ORDER.flatMap((phase) => grouped[phase]).map((f) => f.check_id)

    for (const entry of closed) expect(placed).not.toContain(entry.check_id)
    expect(placed).toHaveLength(all.length)
  })

  it('applies no cap — this is the whole plan, not a shortlist', () => {
    // Top Action Items stops at 10. A roadmap that did the same would silently drop
    // work the reviewer is meant to schedule.
    const many = Array.from({ length: 23 }, (_, index) =>
      finding({ check_id: `SEC-${index}`, remediation_effort: 'medium' }),
    )
    expect(groupByPhase(many).short_term).toHaveLength(23)
  })

  it('keeps several findings from one pillar — it does not dedupe like the shortlist', () => {
    const grouped = groupByPhase([
      finding({ check_id: 'SEC-1', pillar_id: 'security', remediation_effort: 'medium' }),
      finding({ check_id: 'SEC-2', pillar_id: 'security', remediation_effort: 'medium' }),
    ])
    expect(grouped.short_term.map((f) => f.check_id)).toEqual(['SEC-1', 'SEC-2'])
  })

  it('orders each phase by priority, with unranked findings last', () => {
    const grouped = groupByPhase([
      finding({ check_id: 'C', remediation_effort: 'medium', priority: 0 }),
      finding({ check_id: 'A', remediation_effort: 'medium', priority: 7 }),
      finding({ check_id: 'B', remediation_effort: 'medium', priority: 2 }),
    ])
    expect(grouped.short_term.map((f) => f.check_id)).toEqual(['B', 'A', 'C'])
  })
})

describe('determinism', () => {
  const all = shared.map((entry) => entry.built)
  const signature = (grouped: Record<Phase, Finding[]>) =>
    PHASE_ORDER.map((phase) => `${phase}:${grouped[phase].map((f) => f.check_id).join(',')}`)
      .join('|')

  it('produces an identical grouping when run twice over the same findings', () => {
    expect(signature(groupByPhase(all))).toBe(signature(groupByPhase(all)))
  })

  it('orders findings that share a priority by check_id, not by arrival', () => {
    // The case the shared fixture cannot cover: it gives every finding a distinct
    // priority, so a sort keyed on priority alone would look deterministic there.
    // A real review leaves most findings at priority 0, and THAT is where input
    // order leaks through without the tie-break.
    const tied = ['SEC-9', 'SEC-2', 'OPS-4', 'SEC-1'].map((check_id) =>
      finding({ check_id, remediation_effort: 'medium', priority: 0 }),
    )
    const expected = ['OPS-4', 'SEC-1', 'SEC-2', 'SEC-9']

    expect(groupByPhase(tied).short_term.map((f) => f.check_id)).toEqual(expected)
    expect(groupByPhase([...tied].reverse()).short_term.map((f) => f.check_id)).toEqual(
      expected,
    )
  })

  it('separates findings that share both priority and check_id by framework', () => {
    // The same check_id exists under both frameworks in principle; the sort key is
    // framework-qualified so the pair cannot swap between renders.
    const pair = [
      finding({ framework: 'trust7', check_id: 'X-1', remediation_effort: 'medium' }),
      finding({ framework: 'aws_waf', check_id: 'X-1', remediation_effort: 'medium' }),
    ]
    const order = (input: Finding[]) =>
      groupByPhase(input).short_term.map((f) => f.framework)

    expect(order(pair)).toEqual(['aws_waf', 'trust7'])
    expect(order([...pair].reverse())).toEqual(['aws_waf', 'trust7'])
  })

  it('produces an identical grouping regardless of input order', () => {
    // The real guarantee. Findings arrive in whatever order the pipeline sorted them,
    // and most share priority 0 — without the check_id tie-break, the phases would
    // reorder between two renders of the same review.
    const expected = signature(groupByPhase(all))

    for (const seed of [1, 2, 3, 4, 5]) {
      // A fixed rotation-and-reverse rather than Math.random, so a failure here
      // reproduces instead of appearing once in CI and never again.
      const shuffled = [...all.slice(seed), ...all.slice(0, seed)]
      if (seed % 2 === 0) shuffled.reverse()
      expect(signature(groupByPhase(shuffled))).toBe(expected)
    }
  })

  it('is unaffected by fields it must not read', () => {
    // confidence is display-only and evidence is prose; neither may move a finding
    // between phases.
    const base = groupByPhase(all)
    const perturbed = groupByPhase(
      all.map((f) => ({ ...f, confidence: 'low' as const, evidence: 'rewritten' })),
    )
    expect(signature(perturbed)).toBe(signature(base))
  })
})
