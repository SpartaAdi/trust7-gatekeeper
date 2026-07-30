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
/**
 * The model's confidence in its own observation — display only, never arithmetic.
 * `''` means it was not reported: an older stored review, or a check the model
 * skipped and the pipeline backfilled.
 */
export type Confidence = 'high' | 'medium' | 'low' | ''

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
  confidence: Confidence
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

/** Mirrors `UseCaseNote` in backend/schema.py. */
export interface UseCaseNote {
  component: string
  recommendation: string
  /** The phrase from the submitted context this rests on, verbatim. */
  grounded_in: string
}

/**
 * Mirrors `IngestWarning` in backend/schema.py.
 *
 * A reason to distrust how completely the design was read. NOT an error and NOT a
 * rejection — the review ran. It exists because the failure it describes is
 * otherwise silent: a mostly-scanned PDF or a barely-legible diagram produces a
 * real score on a real heatmap with nothing to say most of the design was never
 * seen. `code` is what the UI and tests key on; `message` is prose to render
 * verbatim; `detail` carries the numbers behind the judgement.
 */
export type WarningCode =
  | 'diagram_near_empty'
  | 'vision_low_confidence'
  | 'drawio_mostly_unparsed'
  | 'document_sparse_text'
  | 'relevance_uncertain'

export interface IngestWarning {
  code: WarningCode
  message: string
  detail: string
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
  /** Upload key of the diagram, used by the PDF appendix. '' on older reviews. */
  diagram_key: string
  /** The submitter's own purpose/use-case text; empty when none was given. */
  context: string
  use_case_notes: UseCaseNote[]
  /** Reasons to distrust how completely the design was read. Empty is normal. */
  warnings: IngestWarning[]
  delta: ScoreDelta | null
  token_usage: Record<string, number>
}

/** Stage names, in pipeline order — matches STAGES in backend/schema.py. */
export const STAGE_NAMES = [
  'ingest',
  'normalize',
  'screen',
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
  screen: 'Checking the upload is a design',
  classify: 'Classifying components',
  evaluate: 'Evaluating against rubric',
  prioritize: 'Prioritizing findings',
  remediate: 'Generating remediation',
}

/**
 * `cancelled` marks the stage a deliberate stop interrupted — the one the pipeline
 * stopped at. Distinct from `error` because nothing went wrong, and from `done`
 * because it did not finish.
 */
export type StageState =
  | 'pending'
  | 'running'
  | 'done'
  | 'error'
  | 'cancelled'
  /**
   * The screen stage refused the upload — it is not a solution design. Distinct
   * from `error` because nothing malfunctioned, so it must not be shown under a
   * "Pipeline error" heading that sends the submitter hunting for a fault.
   */
  | 'rejected'

export interface StageProgress {
  name: StageName
  state: StageState
  detail: string
  started_at: string
  finished_at: string
}

export interface ReviewStatus {
  review_id: string
  // `cancelled` is terminal like `complete` and `error`, and is not a kind of
  // success: no result is stored for a cancelled review, so `getReview` 409s and
  // the history list never shows it.
  // `cancelled` and `rejected` are terminal like `complete` and `error`, and
  // neither is a kind of success: no result is stored for either, so `getReview`
  // does not 200 and the history list never shows them.
  state: 'queued' | 'running' | 'complete' | 'error' | 'cancelled' | 'rejected'
  stages: StageProgress[]
  error: string
  /**
   * Why the upload was refused, in prose for the person who uploaded it. Populated
   * only when `state === 'rejected'`; `error` stays empty in that case.
   */
  rejection: string
  /** Surfaced while the review is still running, so a bad upload can be stopped. */
  warnings: IngestWarning[]
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

/**
 * A single completed review as seen through a read-only share link.
 *
 * Deliberately not `ReviewResult`: a share link bypasses the demo gate, so this
 * carries scores and their movement but no finding text, no evidence and no
 * remediation. Mirrors `SharedReview` in backend/api/routes.py.
 */
export interface SharedPillar {
  framework: string
  pillar_id: string
  pillar_name: string
  score: number
  checks_evaluated: number
  checks_passed: number
}

export interface SharedReview {
  review_id: string
  title: string
  created_at: string
  overall_score: number
  frameworks: string[]
  pillars: SharedPillar[]
  open_findings: number
  high_severity_open: number
  component_count: number
  delta: ScoreDelta | null
  /** Why the link is not permanent. Rendered verbatim — see share.py. */
  expires_note: string
}

export interface ShareLink {
  review_id: string
  token: string
  path: string
  expires_note: string
}
