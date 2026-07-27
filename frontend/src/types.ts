/**
 * Mirrors the Pydantic models in backend/schema.py.
 *
 * Keep field names identical to the API's JSON — these are the wire shapes, not
 * a view model. If a name here drifts from the backend, the UI reads `undefined`
 * silently, so this file is the one place to check when a value goes missing.
 */

export type CheckStatus = 'pass' | 'partial' | 'fail' | 'not_applicable'
export type Severity = 'high' | 'medium' | 'low'
export type Effort = 'low' | 'medium' | 'high' | ''

export interface Component {
  id: string
  label: string
  kind: string
  provider: string
  service: string
  attributes: Record<string, string>
}

export interface Finding {
  framework: string
  pillar_id: string
  check_id: string
  status: CheckStatus
  severity: Severity
  title: string
  evidence: string
  affected_components: string[]
  remediation: string
  remediation_effort: Effort
  priority: number
}

export interface PillarScore {
  framework: string
  pillar_id: string
  pillar_name: string
  score: number
  checks_total: number
  checks_evaluated: number
  checks_passed: number
}

export interface FrameworkScore {
  framework: string
  framework_name: string
  score: number
  pillars: PillarScore[]
}

export interface PillarDelta {
  framework: string
  pillar_id: string
  pillar_name: string
  previous_score: number
  current_score: number
  change: number
}

export interface ScoreDelta {
  previous_review_id: string
  previous_overall_score: number
  current_overall_score: number
  change: number
  pillars: PillarDelta[]
  resolved_checks: string[]
  new_checks: string[]
  unchanged_failures: string[]
}

export interface PillarSummary {
  framework: string
  pillar_id: string
  pillar_name: string
  score: number
  checks_evaluated: number
}

/** One row of the history list. Deliberately lighter than a full ReviewResult. */
export interface ReviewSummary {
  review_id: string
  title: string
  created_at: string
  overall_score: number
  open_findings: number
  high_severity_open: number
  has_delta: boolean
  pillars: PillarSummary[]
}

export interface ReviewResult {
  review_id: string
  created_at: string
  title: string
  overall_score: number
  frameworks: FrameworkScore[]
  findings: Finding[]
  components: Component[]
  summary: string
  executive_summary: string
  delta: ScoreDelta | null
  token_usage: Record<string, number>
}

/** Stage names, in pipeline order — matches STAGES in backend/schema.py. */
export const STAGE_NAMES = [
  'ingest',
  'normalize',
  'classify',
  'evaluate',
  'prioritize',
  'remediate',
] as const

export type StageName = (typeof STAGE_NAMES)[number]

/** Human labels for the progress list. */
export const STAGE_LABELS: Record<StageName, string> = {
  ingest: 'Reading uploads',
  normalize: 'Normalizing to common schema',
  classify: 'Classifying components',
  evaluate: 'Evaluating against rubric',
  prioritize: 'Prioritizing findings',
  remediate: 'Generating remediation',
}

export type StageState = 'pending' | 'running' | 'done' | 'error'

export interface StageProgress {
  name: StageName
  state: StageState
  detail: string
  started_at: string
  finished_at: string
}

export interface ReviewStatus {
  review_id: string
  state: 'queued' | 'running' | 'complete' | 'error'
  stages: StageProgress[]
  error: string
  updated_at: string
}

export interface UploadTicket {
  key: string
  filename: string
  size_bytes: number
}

export interface ReviewAccepted {
  review_id: string
  status_url: string
  result_url: string
}
