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

/** 找出可用于当前有图 run 对照的、同配置无图测评。
 *
 * ``completed_with_errors`` 仍可能保留完整的 QA/Judge 结果和部分最终媒体
 * 指标。它不能冒充“无错误完整基线”，但应该进入选择列表，供报告按两轮
 * 的有效媒体指标进行对比；界面会单独标注这类 run。
 */
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
        (run.status === "completed" || run.status === "completed_with_errors") &&
        value(run.album_id) === albumId &&
        value(run.qa_set) === qaSet &&
        value(run.model_name) === modelName,
    )
    .sort((left, right) => timestamp(right) - timestamp(left));
}
