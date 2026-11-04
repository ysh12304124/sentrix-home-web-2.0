import type { Run } from "../../api/types.ts";

export interface GraphBaselineContext {
  albumId?: string;
  qaSet?: string;
  modelName?: string;
}

function value(value: string | null | undefined): string {
  return String(value || "").trim();
}

function timestamp(run: Run): number {
  const raw = run.finished_at || run.created_at || "";
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : 0;
}

/** 找出可用于当前有图 run 对照的、同配置无图完整测评。 */
export function filterGraphBaselineRuns(
  runs: Run[],
  context: GraphBaselineContext,
): Run[] {
  const albumId = value(context.albumId);
  const qaSet = value(context.qaSet);
  const modelName = value(context.modelName);

  return runs
    .filter(
      (run) =>
        run.graph_enabled === false &&
        run.status === "completed" &&
        value(run.album_id) === albumId &&
        value(run.qa_set) === qaSet &&
        value(run.model_name) === modelName,
    )
    .sort((left, right) => timestamp(right) - timestamp(left));
}
