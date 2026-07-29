/**
 * Phase grouping for the "How to Improve" roadmap.
 *
 * Reads only fields already on a finding. No request, no new field, no LLM: the
 * function is pure, so the same findings always produce the same three buckets.
 *
 * ---------------------------------------------------------------------------- #
 * THE RULE
 *
 * The primary signal is `remediation_effort`, which the remediate stage already
 * writes for every open finding, and whose own prompt defines the three values as:
 *
 *   low    — "a configuration or design-document change"
 *   medium — "a component or flow change"
 *   high   — "a structural change to the architecture"
 *
 * Those definitions already are the three phases, so the rule leans on them rather
 * than inferring effort from severity, which measures how much a gap matters and
 * not how much work it is to close.
 *
 *   1. STRUCTURAL   if effort is "high", OR the fix touches more than one component.
 *   2. IMMEDIATE    else if effort is "low" AND severity is "high".
 *   3. SHORT-TERM   everything else.
 *
 * The component-count override in (1) exists because a change spanning three
 * components is a structural change whatever it was scored: the reviewer sequencing
 * work cares about blast radius, and that is the one effort signal in the data that
 * is not the model's opinion.
 *
 * Where `remediation_effort` is absent — an older stored review, or a check the
 * model omitted from its remediation list — the rule falls back to severity and
 * component count alone, which lands high-severity single-component gaps in
 * Immediate and the rest in Short-term. Absent effort never silently becomes "low".
 *
 * DELIBERATELY NOT USED: pillar. Routing Operational Excellence and Reliability to
 * Structural was considered and rejected — those pillars contain some of the
 * cheapest fixes in the rubric ("no runbook referenced", "health check interval not
 * specified") alongside genuinely structural ones ("single-AZ"). A pillar names a
 * topic, not an amount of work, so keying off it would file cheap documentation
 * fixes as architecture projects.
 * ---------------------------------------------------------------------------- #
 */

import type { Finding } from '../types'

export type Phase = 'immediate' | 'short_term' | 'structural'

/** Presentation order, and the order the phases are worked in. */
export const PHASE_ORDER: readonly Phase[] = ['immediate', 'short_term', 'structural']

export const PHASE_LABEL: Record<Phase, string> = {
  immediate: 'Immediate',
  short_term: 'Short-term',
  structural: 'Structural',
}

/** What each phase means, in the reviewer's terms rather than the rule's. */
export const PHASE_BLURB: Record<Phase, string> = {
  immediate: 'High-severity gaps closable by a configuration or document change.',
  short_term: 'A component or flow change — worth scheduling, not architecture work.',
  structural: 'Spans several components or needs a design change.',
}

/** Above one component, a fix is a structural change however it was scored. */
const STRUCTURAL_COMPONENT_COUNT = 2

export function phaseFor(finding: Finding): Phase {
  const spansComponents = finding.affected_components.length >= STRUCTURAL_COMPONENT_COUNT
  if (finding.remediation_effort === 'high' || spansComponents) return 'structural'
  if (finding.remediation_effort === 'low' && finding.severity === 'high') {
    return 'immediate'
  }
  if (finding.remediation_effort === '' && finding.severity === 'high') {
    // No effort recorded. Severity and blast radius are all there is, and a
    // single-component high-severity gap is the shape that is usually cheap.
    return 'immediate'
  }
  return 'short_term'
}

/**
 * Every open finding, in exactly one phase.
 *
 * A partition, not a shortlist: unlike Top Action Items there is no cap and no
 * dedupe, because this is the whole plan — a roadmap that silently dropped the
 * eleventh item would be worse than no roadmap.
 *
 * The same open/closed filter as everywhere else: a passing or not-applicable check
 * has nothing to schedule.
 */
export function groupByPhase(findings: readonly Finding[]): Record<Phase, Finding[]> {
  const grouped: Record<Phase, Finding[]> = {
    immediate: [],
    short_term: [],
    structural: [],
  }

  for (const finding of findings) {
    if (finding.status !== 'fail' && finding.status !== 'partial') continue
    grouped[phaseFor(finding)].push(finding)
  }

  for (const phase of PHASE_ORDER) grouped[phase].sort(byPriorityThenId)
  return grouped
}

/**
 * Remediation order, with an unranked 0 last rather than first — matching the
 * findings list and Top Action Items.
 *
 * The `check_id` tie-break is what makes the output independent of input order:
 * most findings in a real review share `priority: 0`, and a sort keyed on priority
 * alone would preserve whatever order the array happened to arrive in.
 */
function byPriorityThenId(a: Finding, b: Finding): number {
  const rank = (finding: Finding) =>
    finding.priority > 0 ? finding.priority : Number.MAX_SAFE_INTEGER
  if (rank(a) !== rank(b)) return rank(a) - rank(b)
  return `${a.framework}:${a.check_id}`.localeCompare(`${b.framework}:${b.check_id}`)
}
