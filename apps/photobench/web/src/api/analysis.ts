/** Derived analysis artifacts. Raw traces stay behind the existing item endpoint. */
export interface AnalysisEvent {
  event_id: string;
  code: string;
  label: string;
  source: string;
  turn: number;
  step_id: string | null;
  recovery: string;
  basis: string;
  role?: string;
  message?: string;
  call_id?: string;
  diagnostics?: Record<string, unknown>;
  event_index?: number;
  tool_name?: string;
  component?: string;
  issue_kind?: string;
  failure_reason?: string;
  tool_call_id?: string;
  model_call_id?: string;
  parent_step_id?: string;
  call_key?: string;
  evidence_refs?: {
    kind: string;
    turn: number;
    index: number;
    path?: string;
    call_id?: string;
    step_id?: string;
  }[];
  related_event_ids?: string[];
}
export interface AnalysisTurn {
  question?: string;
  answer?: string;
  reference_answer?: string;
  context_turn_count?: number;
  turn: number;
  turn_id?: string;
  termination_reason: string;
  expected: string | null;
  actual: string | null;
  behavior_correct: boolean | null;
  judge_reason: string;
  score: number | null;
  ability_family?: string[];
  ability_type?: string[];
  benchmark_group?: string[];
  content_type?: string[];
  content_subtype?: string[];
  difficulty?: string[];
  answerability?: string[];
}
export interface AnalysisCase {
  index: number;
  qa_id: string;
  question: string;
  answer: string;
  reference_answer: string;
  score: number | null;
  execution: string;
  ability_taxonomy_version?: string | null;
  ability_labels?: Record<string, string>;
  dimensions: Record<string, string>;
  events: AnalysisEvent[];
  turns: AnalysisTurn[];
  missing: string[];
  signals: string[];
  text_truncated: boolean;
  matched_event_ids?: string[];
  matched_turns?: number[];
}
export interface BehaviorGroup {
  value: string;
  total: number;
  valid: number;
  correct: number;
  incorrect: number;
  missing: number;
}
export interface AnalysisReport {
  schema_version: number;
  rule_version: number;
  generated_at: number;
  ability_taxonomy_versions?: string[];
  ability_labels?: Record<string, string>;
  item_count: number;
  source: Record<string, unknown>;
  result_fingerprint: string;
  gt_fingerprint: string;
  annotation_fingerprint?: string;
  group_partitions?: Record<string, Record<string, GroupPartition>>;
  group_errors?: {
    unit: string;
    dimension: string;
    value: string;
    code: string;
    count: number;
  }[];
  counts: Record<string, number>;
  denominators: Record<string, number>;
  errors: {
    code: string;
    label: string;
    affected_items: number;
    events: number;
    final_failed_items: number;
    recovered_items: number;
  }[];
  groups: Record<
    string,
    ({ value: string; total: number } & Record<string, string | number>)[]
  >;
  turn_groups?: AnalysisReport["groups"];
  behavior_groups?: BehaviorGroup[];
  behavior_scenarios?: BehaviorGroup[];
  heatmap: { content_type: string; code: string; count: number }[];
  ability_heatmap?: { ability_family: string; code: string; count: number }[];
  behavior_matrix: { expected: string; actual: string; count: number }[];
  definitions: string[];
}
export interface GroupPartition {
  total: number;
  labeled: number;
  unknown: number;
  not_applicable: number;
  multi_labeled: number;
  exclusive: boolean;
  exhaustive: boolean;
}
export interface ComparisonReport {
  comparable: boolean;
  differences: string[];
  missing_configuration: string[];
  duplicates: string[];
  pairing_counts: Record<string, number>;
  quality_changes: Record<string, number> | null;
  sources: Record<string, unknown>;
  note: string;
  rows: {
    qa_id: string;
    pairing: string;
    quality_change: string;
    score_delta: number | null;
    execution_changed: boolean;
    errors_added: string[];
    errors_removed: string[];
    gt_changed: boolean;
    left: AnalysisCase | null;
    right: AnalysisCase | null;
  }[];
}
export interface DiagnosisReport {
  schema_version?: number;
  scope?: {
    kind: string;
    indices: number[];
    filters: AnalysisFilters;
    result_fingerprint: string;
  };
  coverage?: Record<string, number>;
  issues?: IssueGroup[];
  facts?: Record<string, unknown>;
  overview?: { claims: AnalysisClaim[]; missing_evidence: string[] };
  requests?: {
    stage: string;
    status: string;
    duration_ms: number;
    usage: Record<string, number> | null;
  }[];
  stage?: string;
  summary_status?: string;
  summary_error?: string;
  prompt_version?: string;
  sample_indices?: number[];
  requested: number;
  covered: number;
  model: string;
  provider_id: string;
  note: string;
  cases: {
    index: number;
    qa_id: string;
    valid: boolean;
    result: {
      claims?: AnalysisClaim[];
      observations?: string;
      possible_causes?: string;
      missing_evidence?: string | string[];
      recommendations?: string;
      experiments?: string;
      evidence_refs?: string[];
    };
    raw: string;
    error?: string;
  }[];
}
export interface AnalysisJob<T = AnalysisReport> {
  status: string;
  job_id?: string;
  run_id?: string;
  kind?: string;
  processed: number;
  stale?: boolean;
  error?: string;
  report?: T;
  created_at?: number;
  cancel_requested?: boolean;
}
export interface AnalysisPage {
  items: AnalysisCase[];
  total: number;
  page: number;
  page_size: number;
}
export type AnalysisFilters = Record<string, string>;
export const activeAnalysis = (job: AnalysisJob<unknown> | null) =>
  !!job && ["queued", "running", "paused", "cancelling"].includes(job.status);

export interface IssueGroup {
  group_id: string;
  component?: string;
  tool_name: string;
  code: string;
  label: string;
  failure_reason: string;
  issue_kind: string;
  affected_items: number;
  affected_calls: number;
  low_score_items: number;
  indices: number[];
  event_ids: string[];
  events: number;
  recovery: Record<string, number>;
}
export interface AnalysisView extends AnalysisPage {
  summary: AnalysisReport;
  issues: IssueGroup[];
  tools: {
    tool_name: string;
    recorded_calls: number;
    using_items: number;
    affected_items: number;
    affected_calls: number;
  }[];
  facets: Record<string, { value: string; count: number }[]>;
  ability_tree: Record<string, string[]>;
}
export interface AnalysisClaim {
  kind: "observation" | "hypothesis" | "recommendation";
  text: string;
  evidence_refs: string[];
  limitations: string;
}
