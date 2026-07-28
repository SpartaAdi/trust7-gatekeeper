import { describe, expect, it } from 'vitest'

import type { Finding } from '../types'
import { selectTopActions } from './ResultsView'

function finding(over: Partial<Finding> = {}): Finding {
  return {
    framework: 'aws_waf',
    pillar_id: 'security',
    check_id: 'sec_a',
    status: 'fail',
    severity: 'high',
    title: 'Something is missing',
    evidence: '',
    affected_components: [],
    remediation: 'Do the thing.',
    remediation_effort: 'low',
    priority: 1,
    ...over,
  }
}

describe('selectTopActions', () => {
  it('takes only high-severity findings', () => {
    const picked = selectTopActions([
      finding({ check_id: 'high', severity: 'high' }),
      finding({ check_id: 'med', severity: 'medium', pillar_id: 'reliability' }),
      finding({ check_id: 'low', severity: 'low', pillar_id: 'cost_optimization' }),
    ])

    expect(picked.map((f) => f.check_id)).toEqual(['high'])
  })

  it('takes only open findings, because a passed check has nothing to act on', () => {
    const picked = selectTopActions([
      finding({ check_id: 'failing', status: 'fail' }),
      finding({ check_id: 'partial', status: 'partial', pillar_id: 'reliability' }),
      finding({ check_id: 'passed', status: 'pass', pillar_id: 'cost_optimization' }),
      finding({ check_id: 'na', status: 'not_applicable', pillar_id: 'sustainability' }),
    ])

    expect(picked.map((f) => f.check_id).sort()).toEqual(['failing', 'partial'])
  })

  it('keeps one per pillar — the one touching the most components', () => {
    const picked = selectTopActions([
      finding({ check_id: 'narrow', affected_components: ['a'] }),
      finding({ check_id: 'wide', affected_components: ['a', 'b', 'c'] }),
      finding({ check_id: 'middling', affected_components: ['a', 'b'] }),
    ])

    expect(picked.map((f) => f.check_id)).toEqual(['wide'])
  })

  it('treats the same pillar id in different frameworks as different pillars', () => {
    // `sustainability` exists in both AWS WAF and TRUST-7. Collapsing them would
    // silently drop one framework's highest-severity action.
    const picked = selectTopActions([
      finding({ framework: 'aws_waf', pillar_id: 'sustainability', check_id: 'waf' }),
      finding({ framework: 'trust7', pillar_id: 'sustainability', check_id: 't7' }),
    ])

    expect(picked.map((f) => f.check_id).sort()).toEqual(['t7', 'waf'])
  })

  it('breaks a component-count tie on priority, so the pick is deterministic', () => {
    const picked = selectTopActions([
      finding({ check_id: 'later', priority: 7, affected_components: ['a'] }),
      finding({ check_id: 'sooner', priority: 2, affected_components: ['b'] }),
    ])

    expect(picked.map((f) => f.check_id)).toEqual(['sooner'])
  })

  it('orders the list by remediation priority, with unranked items last', () => {
    const picked = selectTopActions([
      finding({ pillar_id: 'p1', check_id: 'unranked', priority: 0 }),
      finding({ pillar_id: 'p2', check_id: 'third', priority: 9 }),
      finding({ pillar_id: 'p3', check_id: 'first', priority: 1 }),
    ])

    expect(picked.map((f) => f.check_id)).toEqual(['first', 'third', 'unranked'])
  })

  it('caps the list at ten, keeping the highest-priority ten', () => {
    const many = Array.from({ length: 14 }, (_, index) =>
      finding({ pillar_id: `pillar_${index}`, check_id: `c${index}`, priority: index + 1 }),
    )

    const picked = selectTopActions(many)

    expect(picked).toHaveLength(10)
    expect(picked.map((f) => f.priority)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
  })

  it('returns nothing when there is no open high-severity finding', () => {
    expect(selectTopActions([])).toEqual([])
    expect(selectTopActions([finding({ severity: 'medium' })])).toEqual([])
  })
})
