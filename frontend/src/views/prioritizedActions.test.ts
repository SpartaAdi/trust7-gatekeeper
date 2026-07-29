/**
 * The merged action roadmap.
 *
 * These were the Top Action Items tests. That shortlist and the separate "How to
 * Improve" roadmap are now one section, so the properties they pinned — dedupe by
 * framework+pillar, deterministic tie-breaks, ordering, and a bounded fix-it
 * prompt — are asserted here against `prioritizedActions` instead of being lost
 * with the component they used to describe.
 *
 * What deliberately changed: the shortlist took only high-severity findings and
 * capped at ten. The roadmap takes every open finding, because it is the whole
 * plan, and the cap now applies only to the copied prompt.
 */

import { describe, expect, it } from 'vitest'

import type { Finding } from '../types'
import { FIX_IT_PREAMBLE, buildFixItPrompt } from './ResultsView'
import {
  MAX_FOCUS_ITEMS,
  flattenActions,
  prioritizedActions,
  priorityFocus,
} from './roadmap'

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

const ids = (findings: Finding[]) => findings.map((f) => f.check_id)

describe('prioritizedActions', () => {
  it('takes only open findings, because a passed check has nothing to act on', () => {
    const grouped = prioritizedActions([
      finding({ check_id: 'failing', status: 'fail' }),
      finding({ check_id: 'partial', status: 'partial', pillar_id: 'reliability' }),
      finding({ check_id: 'passed', status: 'pass', pillar_id: 'cost_optimization' }),
      finding({ check_id: 'na', status: 'not_applicable', pillar_id: 'sustainability' }),
    ])

    expect(ids(flattenActions(grouped)).sort()).toEqual(['failing', 'partial'])
  })

  /**
   * The shortlist dropped these entirely. The roadmap keeps them — a medium
   * finding is still work — and files them by effort, not by severity.
   */
  it('keeps medium and low findings, unlike the shortlist it replaced', () => {
    const grouped = prioritizedActions([
      finding({ check_id: 'high', severity: 'high' }),
      finding({ check_id: 'med', severity: 'medium', pillar_id: 'reliability' }),
      finding({ check_id: 'low', severity: 'low', pillar_id: 'cost_optimization' }),
    ])

    expect(ids(flattenActions(grouped)).sort()).toEqual(['high', 'low', 'med'])
    expect(ids(grouped.immediate)).toEqual(['high'])
    expect(ids(grouped.short_term).sort()).toEqual(['low', 'med'])
  })

  it('keeps one per pillar per phase — the one touching the most components', () => {
    // Both are low-effort and high-severity, so both land in Immediate and the
    // dedupe has to choose between them.
    const grouped = prioritizedActions([
      finding({ check_id: 'narrow', affected_components: [] }),
      finding({ check_id: 'wider', affected_components: ['a'] }),
    ])

    expect(ids(grouped.immediate)).toEqual(['wider'])
  })

  it('treats the same pillar id in different frameworks as different pillars', () => {
    // `sustainability` exists in both AWS WAF and TRUST-7. Collapsing them would
    // silently drop one framework's action.
    const grouped = prioritizedActions([
      finding({ framework: 'aws_waf', pillar_id: 'sustainability', check_id: 'waf' }),
      finding({ framework: 'trust7', pillar_id: 'sustainability', check_id: 't7' }),
    ])

    expect(ids(grouped.immediate).sort()).toEqual(['t7', 'waf'])
  })

  /**
   * The dedupe is per phase, not global: one pillar can need a cheap fix now and
   * a structural one later, and those are two pieces of work.
   */
  it('lets one pillar appear in two different phases', () => {
    const grouped = prioritizedActions([
      finding({ check_id: 'cheap', remediation_effort: 'low' }),
      finding({ check_id: 'structural', remediation_effort: 'high' }),
    ])

    expect(ids(grouped.immediate)).toEqual(['cheap'])
    expect(ids(grouped.structural)).toEqual(['structural'])
  })

  it('breaks a component-count tie on priority, so the pick is deterministic', () => {
    const grouped = prioritizedActions([
      finding({ check_id: 'later', priority: 7 }),
      finding({ check_id: 'sooner', priority: 2 }),
    ])

    expect(ids(grouped.immediate)).toEqual(['sooner'])
  })

  it('orders a phase by severity first, then priority, unranked last', () => {
    const grouped = prioritizedActions([
      finding({
        pillar_id: 'p1',
        check_id: 'medium_first',
        severity: 'medium',
        priority: 1,
        remediation_effort: 'medium',
      }),
      finding({
        pillar_id: 'p2',
        check_id: 'high_unranked',
        severity: 'high',
        priority: 0,
        remediation_effort: 'medium',
      }),
      finding({
        pillar_id: 'p3',
        check_id: 'high_ranked',
        severity: 'high',
        priority: 5,
        remediation_effort: 'medium',
      }),
    ])

    // Severity outranks priority: both high-severity items precede the medium one
    // even though the medium one is priority 1.
    expect(ids(grouped.short_term)).toEqual([
      'high_ranked',
      'high_unranked',
      'medium_first',
    ])
  })

  it('applies no cap — the roadmap is the whole plan', () => {
    const many = Array.from({ length: 14 }, (_, index) =>
      finding({ pillar_id: `pillar_${index}`, check_id: `c${index}`, priority: index + 1 }),
    )

    expect(flattenActions(prioritizedActions(many))).toHaveLength(14)
  })

  it('is empty in every phase when there is nothing open', () => {
    const grouped = prioritizedActions([finding({ status: 'pass' })])

    expect(flattenActions(grouped)).toEqual([])
    expect(flattenActions(prioritizedActions([]))).toEqual([])
  })

  it('produces the same result regardless of input order', () => {
    const findings = [
      finding({ pillar_id: 'p1', check_id: 'a', priority: 3 }),
      finding({
        pillar_id: 'p2',
        check_id: 'b',
        priority: 1,
        severity: 'medium',
        remediation_effort: 'medium',
      }),
      finding({ pillar_id: 'p3', check_id: 'c', priority: 2, remediation_effort: 'high' }),
    ]

    const forward = flattenActions(prioritizedActions(findings))
    const reversed = flattenActions(prioritizedActions([...findings].reverse()))

    expect(ids(forward)).toEqual(ids(reversed))
  })
})

describe('buildFixItPrompt', () => {
  it('is assembled from the roadmap, not a second filter', () => {
    // Same input, two consumers. The prompt must list exactly what the roadmap
    // shows — otherwise the page and the clipboard disagree.
    const findings = [
      finding({ pillar_id: 'p1', check_id: 'a', remediation: 'Encrypt the store.' }),
      finding({ pillar_id: 'p2', check_id: 'b', remediation: 'Pin the region.', priority: 2 }),
      finding({ pillar_id: 'p4', check_id: 'passed', status: 'pass' }),
    ]

    const prompt = buildFixItPrompt(findings)

    expect(prompt).toContain('1. Encrypt the store.')
    expect(prompt).toContain('2. Pin the region.')
    expect(prompt).not.toContain('passed')
    expect(prompt.match(/^\d+\. /gm)).toHaveLength(
      flattenActions(prioritizedActions(findings)).length,
    )
  })

  it('follows the roadmap phase order, not raw priority', () => {
    const prompt = buildFixItPrompt([
      finding({
        pillar_id: 'p1',
        check_id: 'structural',
        remediation: 'Re-architect it.',
        remediation_effort: 'high',
        priority: 1,
      }),
      finding({
        pillar_id: 'p2',
        check_id: 'immediate',
        remediation: 'Flip the flag.',
        remediation_effort: 'low',
        priority: 9,
      }),
    ])

    // Immediate work is listed first even though it ranks lower on priority.
    expect(prompt.indexOf('Flip the flag.')).toBeLessThan(
      prompt.indexOf('Re-architect it.'),
    )
  })

  it('copies remediation text verbatim', () => {
    const exact =
      'Enable encryption at rest with a customer-managed KMS key, and set a key ' +
      'rotation policy. Existing snapshots must be re-encrypted, not just new writes.'

    expect(buildFixItPrompt([finding({ remediation: exact })])).toContain(exact)
  })

  it('falls back to the title exactly as the roadmap rows do', () => {
    const prompt = buildFixItPrompt([
      finding({ remediation: '', title: 'No region constraint documented' }),
    ])

    expect(prompt).toContain('1. No region constraint documented')
  })

  /**
   * The roadmap on screen is uncapped; the prompt is not. Forty-five imperatives
   * pasted into an assistant is not a usable instruction.
   */
  it('caps the prompt at ten even though the roadmap shows everything', () => {
    const many = Array.from({ length: 14 }, (_, index) =>
      finding({ pillar_id: `pillar_${index}`, check_id: `c${index}`, priority: index + 1 }),
    )

    expect(buildFixItPrompt(many).match(/^\d+\. /gm)).toHaveLength(10)
    expect(flattenActions(prioritizedActions(many))).toHaveLength(14)
  })

  it('honours the same dedupe-by-framework-and-pillar rule', () => {
    const prompt = buildFixItPrompt([
      finding({ check_id: 'narrow', affected_components: [], remediation: 'Narrow fix.' }),
      finding({ check_id: 'wide', affected_components: ['a'], remediation: 'Wide fix.' }),
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

describe('priorityFocus', () => {
  it('leads with the Immediate phase — urgent work that is cheap to close', () => {
    const focus = priorityFocus([
      finding({ pillar_id: 'p1', check_id: 'structural', remediation_effort: 'high' }),
      finding({ pillar_id: 'p2', check_id: 'immediate', remediation_effort: 'low' }),
    ])

    expect(focus[0]!.check_id).toBe('immediate')
  })

  it('caps at five, so it stays a glance', () => {
    const many = Array.from({ length: 9 }, (_, i) =>
      finding({ pillar_id: `p${i}`, check_id: `c${i}`, priority: i + 1 }),
    )

    expect(priorityFocus(many)).toHaveLength(MAX_FOCUS_ITEMS)
    expect(MAX_FOCUS_ITEMS).toBe(5)
  })

  /**
   * A design whose gaps are all structural still deserves a focus list. Without
   * the top-up it would show an empty box on exactly the reviews that need
   * direction most.
   */
  it('tops up from other phases when Immediate is thin', () => {
    const focus = priorityFocus([
      finding({ pillar_id: 'p1', check_id: 'struct_a', remediation_effort: 'high' }),
      finding({ pillar_id: 'p2', check_id: 'struct_b', remediation_effort: 'high' }),
    ])

    expect(focus.map((f) => f.check_id).sort()).toEqual(['struct_a', 'struct_b'])
  })

  it('tops up with high severity only — a low-severity gap is not urgent', () => {
    const focus = priorityFocus([
      finding({ pillar_id: 'p1', check_id: 'low_struct', severity: 'low',
                remediation_effort: 'high' }),
      finding({ pillar_id: 'p2', check_id: 'high_struct', severity: 'high',
                remediation_effort: 'high' }),
    ])

    expect(focus.map((f) => f.check_id)).toEqual(['high_struct'])
  })

  it('is empty when nothing is open, so the callout can hide itself', () => {
    expect(priorityFocus([finding({ status: 'pass' })])).toEqual([])
    expect(priorityFocus([])).toEqual([])
  })

  it('re-uses the roadmap selection rather than ranking again', () => {
    const findings = [
      finding({ pillar_id: 'p1', check_id: 'a' }),
      finding({ pillar_id: 'p1', check_id: 'b', affected_components: ['x'] }),
    ]

    // Same pillar, same phase: the roadmap's one-per-pillar dedupe applies here
    // too, because this reads prioritizedActions rather than the raw findings.
    expect(priorityFocus(findings)).toHaveLength(1)
    expect(priorityFocus(findings)[0]!.check_id).toBe('b')
  })
})
