import { useState } from "react";
import { GtImageToggle } from "../../components/GtImageToggle.tsx";
import type { RecoveryPlan } from "../../hooks/useRunDetail.ts";

export function ResumeCard({ plan, busy, onResume, error, onRetry }: {
  error?: string;
  onRetry?: () => void;
  plan: RecoveryPlan | null;
  busy: boolean;
  onResume: (body: Record<string, unknown>) => void;
}) {
  const [repair, setRepair] = useState(true);
  const [useImages, setUseImages] = useState<boolean | null>(null);
  const [useCurrentVersion, setUseCurrentVersion] = useState(false);
  if (error) return <p className="error">恢复信息读取失败：{error} <button className="btn ghost compact" onClick={onRetry}>重新读取</button></p>;
  if (!plan?.can_resume) return null;
  const images = useImages ?? plan.judge_use_gt_images;
  return <section className="rejudge-card">
    <h3>继续未完成的答题</h3>
    <p>复用原相册，保留并跳过已执行的 {plan.inherited_qa_count} 题，继续剩余 {plan.remaining_qa_count} 题。已执行但答题失败的题目保留原结果。续跑创建新任务，原任务保留。</p>
    <div className="recovery-options">
      <label><input type="checkbox" checked={repair} disabled={busy} onChange={event => setRepair(event.target.checked)} /> 同时补齐已有回答的缺失评分</label>
      <GtImageToggle checked={images} disabled={busy} onChange={setUseImages} />
      {plan.product_version_changed && <div>
        <p>原产品版本：<code>{plan.source_product_build_revision || "未记录"}</code><br />当前产品版本：<code>{plan.current_product_build_revision || "未配置"}</code></p>
        <label><input type="checkbox" checked={useCurrentVersion} disabled={busy || !plan.current_product_build_revision} onChange={event => setUseCurrentVersion(event.target.checked)} /> 选择当前产品版本续跑，并记录版本变化</label>
      </div>}
      <button type="button" className="btn compact" disabled={busy || (plan.product_version_changed && !useCurrentVersion)} onClick={() => {
        if (!window.confirm(`继续剩余 ${plan.remaining_qa_count} 题；${repair ? "补评已有缺分" : "保留已有评分状态"}；${images ? "使用 GT 图片" : "仅文字 GT"}。${useCurrentVersion ? "按当前产品版本执行。" : ""}确定开始？`)) return;
        onResume({repair_missing_judges: repair, judge_use_gt_images: images,
          ...(useCurrentVersion ? {product_build_revision: plan.current_product_build_revision} : {})});
      }}>{busy ? "已有任务运行，暂不可续跑" : "继续剩余 QA 答题"}</button>
    </div>
  </section>;
}
