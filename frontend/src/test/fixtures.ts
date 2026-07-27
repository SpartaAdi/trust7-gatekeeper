import type { ReviewResult, ReviewStatus } from '../types'

export function statusFixture(overrides: Partial<ReviewStatus> = {}): ReviewStatus {
  return {
    review_id: 'rev-1',
    state: 'running',
    error: '',
    updated_at: '2026-07-27T10:00:05Z',
    stages: [
      {
        name: 'ingest',
        state: 'done',
        detail: '4 components from drawio',
        started_at: '2026-07-27T10:00:00Z',
        finished_at: '2026-07-27T10:00:02Z',
      },
      { name: 'normalize', state: 'done', detail: '', started_at: '', finished_at: '' },
      {
        // Detail text deliberately differs from the stage's own label, so a test
        // asserting on one cannot accidentally match the other.
        name: 'classify',
        state: 'running',
        detail: '12 components in the inventory',
        started_at: '2026-07-27T10:00:03Z',
        finished_at: '',
      },
      { name: 'evaluate', state: 'pending', detail: '', started_at: '', finished_at: '' },
      { name: 'prioritize', state: 'pending', detail: '', started_at: '', finished_at: '' },
      { name: 'remediate', state: 'pending', detail: '', started_at: '', finished_at: '' },
    ],
    ...overrides,
  }
}

export function resultFixture(overrides: Partial<ReviewResult> = {}): ReviewResult {
  return {
    review_id: 'rev-1',
    created_at: '2026-07-27T10:04:00Z',
    title: 'Payments platform',
    overall_score: 62.5,
    summary: 'Solid foundations, with gaps in data residency and audit logging.',
    components: [],
    token_usage: { input_tokens: 1000, output_tokens: 500 },
    delta: null,
    frameworks: [
      {
        framework: 'aws_waf',
        framework_name: 'AWS Well-Architected Framework',
        score: 65,
        pillars: [
          {
            framework: 'aws_waf',
            pillar_id: 'security',
            pillar_name: 'Security',
            score: 55,
            checks_total: 7,
            checks_evaluated: 7,
            checks_passed: 3,
          },
        ],
      },
      {
        framework: 'trust7',
        framework_name: 'Minfy TRUST-7 Framework',
        score: 60,
        pillars: [
          {
            framework: 'trust7',
            pillar_id: 'ai_governance',
            pillar_name: 'AI governance',
            score: 60,
            checks_total: 3,
            checks_evaluated: 3,
            checks_passed: 1,
          },
        ],
      },
    ],
    findings: [
      {
        framework: 'aws_waf',
        pillar_id: 'security',
        check_id: 'sec_encryption_at_rest',
        status: 'fail',
        severity: 'high',
        title: 'Customer data store has no encryption at rest specified',
        evidence: 'The design names a DynamoDB table but states no encryption setting.',
        affected_components: ['orders-db'],
        remediation: 'Enable SSE-KMS on the table with a customer-managed key.',
        remediation_effort: 'low',
        priority: 1,
      },
      {
        framework: 'trust7',
        pillar_id: 'ai_governance',
        check_id: 'gov_audit_trail',
        status: 'pass',
        severity: 'high',
        title: 'AI decisions are logged with model version and input',
        evidence: 'The design records prompt, model id, and output per review.',
        affected_components: [],
        remediation: '',
        remediation_effort: '',
        priority: 0,
      },
    ],
    ...overrides,
  }
}
