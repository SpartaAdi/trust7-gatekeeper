import { describe, expect, it } from 'vitest'

import type { Finding } from '../types'
import { FIX_IT_PREAMBLE, buildFixItPrompt, selectTopActions } from './ResultsView'

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
    confidence: 'high',
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

describe('buildFixItPrompt', () => {
  it('is assembled from selectTopActions, not a second filter', () => {
    // Same input, two consumers. The prompt must list exactly the shortlist —
    // otherwise the page says ten items and the clipboard says eight.
    const findings = [
      finding({ pillar_id: 'p1', check_id: 'a', remediation: 'Encrypt the store.' }),
      finding({ pillar_id: 'p2', check_id: 'b', remediation: 'Pin the region.', priority: 2 }),
      finding({ pillar_id: 'p3', check_id: 'skipped', severity: 'medium' }),
      finding({ pillar_id: 'p4', check_id: 'passed', status: 'pass' }),
    ]

    const prompt = buildFixItPrompt(findings)

    expect(prompt).toContain('1. Encrypt the store.')
    expect(prompt).toContain('2. Pin the region.')
    expect(prompt).not.toContain('skipped')
    expect(prompt).not.toContain('passed')
    // Numbering matches the shortlist's ordering and length exactly.
    expect(prompt.match(/^\d+\. /gm)).toHaveLength(selectTopActions(findings).length)
  })

  it('copies remediation text verbatim', () => {
    const exact =
      'Enable encryption at rest with a customer-managed KMS key, and set a key ' +
      'rotation policy. Existing snapshots must be re-encrypted, not just new writes.'

    expect(buildFixItPrompt([finding({ remediation: exact })])).toContain(exact)
  })

  it('falls back to the title exactly as the action list does', () => {
    // Not a separate rule: TopActionItems renders `remediation || title` too.
    const prompt = buildFixItPrompt([
      finding({ remediation: '', title: 'No region constraint documented' }),
    ])

    expect(prompt).toContain('1. No region constraint documented')
  })

  it('honours the same ten-item cap', () => {
    const many = Array.from({ length: 14 }, (_, index) =>
      finding({ pillar_id: `pillar_${index}`, check_id: `c${index}`, priority: index + 1 }),
    )

    expect(buildFixItPrompt(many).match(/^\d+\. /gm)).toHaveLength(10)
  })

  it('honours the same dedupe-by-framework-and-pillar rule', () => {
    const prompt = buildFixItPrompt([
      finding({ check_id: 'narrow', affected_components: ['a'], remediation: 'Narrow fix.' }),
      finding({ check_id: 'wide', affected_components: ['a', 'b'], remediation: 'Wide fix.' }),
    ])

    expect(prompt).toContain('Wide fix.')
    expect(prompt).not.toContain('Narrow fix.')
  })

  it('names no assistant, so it can be pasted into any of them', () => {
    const prompt = buildFixItPrompt([finding()]).toLowerCase()

    for (const vendor of ['claude', 'chatgpt', 'gpt', 'gemini', 'copilot', 'anthropic', 'openai']) {
      expect(prompt).not.toContain(vendor)
    }
  })

  it('opens by giving the assistant the architecture and the task', () => {
    const prompt = buildFixItPrompt([finding()])

    expect(prompt.startsWith(FIX_IT_PREAMBLE)).toBe(true)
    expect(prompt).toContain('revise the diagram')
  })

  it('is empty when there is nothing to act on', () => {
    expect(buildFixItPrompt([])).toBe('')
    expect(buildFixItPrompt([finding({ status: 'pass' })])).toBe('')
  })
})
