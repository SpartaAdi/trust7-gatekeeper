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
  | 'vision_minor_gaps'
  | 'drawio_mostly_unparsed'
  | 'document_sparse_text'
  | 'relevance_uncertain'

export interface IngestWarning {
  code: WarningCode
  message: string
  detail: string
}

/**
 * Three data-fidelity numbers, mirroring `DataFidelity` in backend/schema.py.
 *
 * NEVER COMBINED. Each measures a different thing against a different kind of
 * reference: `structural` is an exact ratio against the draw.io XML, `ocr_proxy` is
 * an estimate against a second fallible reader, and `grounding` is a count of what
 * was removed that says nothing about what remains. There is deliberately no
 * composite "accuracy" field, and there must not be one added — averaging them
 * would launder the estimate's uncertainty into a figure that looks measured.
 */

/** EXACT. draw.io uploads only; the XML enumerates its own elements. */
export interface StructuralCoverage {
  parsed_elements: number
  total_elements: number
  percent: number
  /** Why elements did not survive, counted, most common first. */
  dropped: string[]
}

/**
 * AN ESTIMATE. Image uploads only.
 *
 * `is_estimate` is always true and exists so the UI cannot present this as a
 * measurement by omission. `available: false` means no OCR engine was reachable —
 * the metric is then ABSENT, not zero, and a zero would wrongly read as "the vision
 * model missed everything".
 */
export interface OcrCoverageProxy {
  available: boolean
  unavailable_reason: string
  is_estimate: boolean
  ocr_tokens: number
  matched_tokens: number
  percent: number
  /** Words OCR read that the graph lacks. Missed labels or OCR noise — cannot tell. */
  sample_unmatched: string[]
}

/** A COUNT of ungrounded claims removed. Deliberately carries no percentage. */
export interface GroundingFilter {
  checked: number
  removed: number
  incomplete: number
  removed_for: string[]
}

export interface DataFidelity {
  structural: StructuralCoverage | null
  ocr_proxy: OcrCoverageProxy | null
  grounding: GroundingFilter | null
}

/** Mirrors `COVERAGE_REVIEW_THRESHOLD` in backend/schema.py. */
export const COVERAGE_REVIEW_THRESHOLD = 95.0

/**
 * The AI/ML evidence record, mirroring `AiDetection` in backend/schema.py.
 *
 * Nineteen of the forty-five checks only mean anything if the design has an AI or
 * ML component in it, and whether they apply is the evaluate stage's
 * `not_applicable` judgement. This record is what makes that judgement checkable:
 * what was searched for, what matched, and where.
 *
 * It moves NO verdict and NO score. Where it and the model disagree, the UI says so
 * and a human decides — see `disagreesWithPillar`.
 */
export type AiSignalTier =
  /** A component an earlier stage already called `ai_model`. */
  | 'classified_kind'
  /** A specific product or model family: Bedrock, SageMaker, GPT-4. */
  | 'named_service'
  /** Unambiguous ML vocabulary: 'training data', 'vector store', 'inference'. */
  | 'explicit_term'
  /** A capability usually but not necessarily model-backed: 'recommendation engine'. */
  | 'implicit_function'
  /** The design states it has no AI/ML. A claim, never believed on its own. */
  | 'denial'

export interface AiSignal {
  tier: AiSignalTier
  /** What was found, named for a human. */
  signal: string
  /** Where — a named component, a diagram edge, the document, a classify field. */
  source: string
  /** The match with surrounding text, so the reader can judge it. */
  excerpt: string
}

/**
 * `verdict` and `rationale` are computed server-side and arrive read-only, so the UI
 * cannot describe the evidence differently from the backend or the PDF.
 *
 * `not_run` is deliberately distinct from `absent`: one means nobody looked (a review
 * stored before this existed), the other means patterns ran and found nothing. Never
 * render `not_run` as "no AI detected".
 */
export interface AiDetection {
  signals: AiSignal[]
  patterns_checked: number
  components_seen: string[]
  verdict: 'present' | 'likely' | 'contradicted' | 'denied' | 'absent' | 'not_run'
  rationale: string
}

/**
 * Mirrors `AiDetection.disagrees_with_pillar` in backend/schema.py.
 *
 * True when the evaluate stage skipped every check in a pillar while the evidence
 * says AI is present or likely. Reported, never corrected — a keyword record is more
 * auditable than the model, not more right.
 */
export function disagreesWithPillar(
  detection: AiDetection | undefined,
  pillar: PillarScore,
): boolean {
  if (!detection) return false
  return (
    pillar.checks_evaluated === 0 &&
    (detection.verdict === 'present' ||
      detection.verdict === 'likely' ||
      detection.verdict === 'contradicted')
  )
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
  /** The three fidelity numbers. Absent on reviews stored before they existed. */
  fidelity?: DataFidelity
  /**
   * Why the AI-dependent checks were or were not applicable. Optional because a
   * review stored before this existed has none — and when it IS present but
   * `verdict` is `not_run`, that means the same thing and must read that way.
   */
  ai_detection?: AiDetection

  // ----------------------------------------------------------------------- //
  // Version linkage. A follow-up re-review never overwrites the version it came
  // from — it writes a NEW record pointing back at it — so these describe where
  // this record sits in a chain rather than what it replaced.
  //
  // All optional: a review stored before follow-ups existed has none, and every
  // consumer treats a missing `version` as 1, which is what it is.
  // ----------------------------------------------------------------------- //
  /** 1 for an original review, incrementing once per follow-up round. */
  version?: number
  /** The chain's identity — the id of the original. */
  root_review_id?: string
  /** The version this round was built on. '' on an original. */
  based_on_review_id?: string
  /** The reviewer's own words that produced this version. '' on an original. */
  feedback?: string

  /**
   * Open findings with no remediation text. Computed server-side, so it is present
   * on every review including ones stored before it existed. Optional only because
   * a hand-built fixture may omit it.
   */
  remediation_gap?: RemediationGap
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

/**
 * Open findings the remediate stage produced no guidance for. Mirrors
 * `RemediationGap` in backend/schema.py, and computed there from the same findings
 * the roadmap renders — so the count cannot disagree with the page.
 *
 * A COUNT of what is missing, never a rate: "22 of 28 have guidance" invites
 * reading 79% as a quality figure for the six that do not.
 */
export interface RemediationGap {
  open_findings: number
  without_guidance: number
  check_ids: string[]
}

/** Every open finding, not some — a different failure with a different cause. */
export function remediationTotallyMissing(gap?: RemediationGap): boolean {
  return (
    !!gap && gap.open_findings > 0 && gap.without_guidance === gap.open_findings
  )
}

export interface ReviewAccepted {
  review_id: string
  status_url: string
  result_url: string
}

/**
 * One entry in a review's version chain. Mirrors `ReviewVersion` in
 * backend/api/routes.py.
 *
 * Enough to render a chain without fetching every version in full — the score and
 * the open count are here, so a version list costs one request rather than N.
 */
export interface ReviewVersion {
  review_id: string
  version: number
  created_at: string
  overall_score: number
  open_findings: number
  /** The feedback that produced this version. Empty on the original. */
  feedback: string
  based_on_review_id: string
  is_original: boolean
}

export interface ReviewVersions {
  root_review_id: string
  latest_review_id: string
  /** Oldest first, original at index 0. */
  versions: ReviewVersion[]
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
