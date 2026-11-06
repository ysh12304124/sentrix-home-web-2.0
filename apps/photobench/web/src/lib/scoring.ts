export const OPEN_PROFILE = 'photobench_open_v1';
export const ATM_PROFILE = 'atm_qs_v1';
export function newBatchNonce(): string {
  return Array.from(crypto.getRandomValues(new Uint32Array(4)), n => n.toString(16).padStart(8, '0')).join('');
}
export const PROFILE_LABELS: Record<string, string> = {
  [OPEN_PROFILE]: '开放式回答（0/1/2 LLM as Judge评分）', [ATM_PROFILE]: 'ATM-Bench式QS',
};
export interface ScoringConfig { use_gt_images: boolean; use_gt_metadata?: boolean; gt_metadata_mode?: 'readable_v1' | 'image_metadata_v1'; prompts?: Record<string, string> }
export interface ScoringSelection { profiles: string[]; configs: Record<string, ScoringConfig> }
export function defaultScoring(): ScoringSelection {
  return {profiles: [OPEN_PROFILE], configs: Object.fromEntries(Object.keys(PROFILE_LABELS).map(p => [p, {use_gt_images:false, gt_metadata_mode:'image_metadata_v1'}]))};
}
export function scoringPayload(selection: ScoringSelection, drafts: Record<string, string> = {}) {
  return {scoring_profiles: selection.profiles, scoring_configs: Object.fromEntries(selection.profiles.map(profile => [profile, {
    use_gt_images: selection.configs[profile]?.use_gt_images ?? false, gt_metadata_mode: 'image_metadata_v1' as const, prompts: Object.fromEntries(Object.entries(drafts).filter(([k]) => k.startsWith(profile + ':')).map(([k,v]) => [k.split(':')[1], v])),
  }]))};
}
