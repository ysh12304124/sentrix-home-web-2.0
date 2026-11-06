import { useRef, useState } from "react";
import { GtImageToggle } from "../../components/GtImageToggle.tsx";
import type { RecoveryPlan } from "../../hooks/useRunDetail.ts";

export function ResumeCard({ plan, busy, onResume, error, onRetry, disabledReason }: {
  error?: string;
  onRetry?: () => void;
  disabledReason?: string;
  plan: RecoveryPlan | null;
  busy: boolean;
  onResume: (body: Record<string, unknown>) => void | Promise<void>;
}) {
  const [repair, setRepair] = useState(true);
  const [useImages, setUseImages] = useState<boolean | null>(null);
  const [useCurrentVersion, setUseCurrentVersion] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const submitLock = useRef(false);
  const available = !disabledReason && !error && !!plan?.can_resume && plan.remaining_qa_count > 0;
  const locked = busy || submitting;
  const frozen = !!plan?.frozen_scoring_profiles?.length;
  const images = useImages ?? plan?.judge_use_gt_images ?? false;
  const versionBlocked = !!plan?.product_version_changed && (!useCurrentVersion || !plan.current_product_build_revision);
  return <div className="resume-content">
    <div className="recovery-column-head"><h4>断点续跑</h4><span className="recovery-kind">继续答题</span></div>
    {!available ? <div className="recovery-unavailable">
      <p>{error ? `恢复信息读取失败：${error}` : disabledReason || "没有可续跑的题目。"}</p>
      {error && onRetry ? <button className="btn ghost compact" onClick={onRetry}>重新读取</button> : null}
      <button type="button" className="btn" disabled>继续剩余 QA 答题</button>
    </div> : <>
      <p className="recovery-description">复用原相册，跳过已执行题目，继续剩余答题。</p>
      <div className="recovery-counts"><span>已执行 <strong>{plan.inherited_qa_count}</strong> 题</span><span>待续跑 <strong>{plan.remaining_qa_count}</strong> 题</span></div>
      <div className="recovery-options">
        {!frozen && plan.scoring?.missing?.subscore_count > 0 ? <label><input type="checkbox" checked={repair} disabled={locked} onChange={event => setRepair(event.target.checked)} /> 同时补齐已有回答的缺失评分</label> : null}
        {frozen ? <p className="small muted">评分沿用原任务冻结的标准、Judge、提示词及GT配置。已有回答的缺失评分请使用重新评分恢复。</p> : <GtImageToggle checked={images} disabled={locked} onChange={setUseImages} />}
        {plan.product_version_changed && <div className="recovery-version">
          <p>原版本 <code>{plan.source_product_build_revision?.slice(0, 12) || "未记录"}</code> → 当前 <code>{plan.current_product_build_revision?.slice(0, 12) || "未配置"}</code></p>
          <label><input type="checkbox" checked={useCurrentVersion} disabled={locked || !plan.current_product_build_revision} onChange={event => setUseCurrentVersion(event.target.checked)} /> 选择当前产品版本续跑，并记录版本变化</label>
        </div>}
      </div>
      <div className="recovery-action-row"><button type="button" className="btn" disabled={locked || versionBlocked} onClick={async () => {
        if (locked || versionBlocked || submitLock.current) return;
        if (!window.confirm(`继续剩余 ${plan.remaining_qa_count} 题；${frozen ? "沿用原评分配置；已有缺分单独恢复" : (repair ? "补评已有缺分" : "保留已有评分状态") + "；" + (images ? "使用 GT 图片" : "仅文字 GT")}。${useCurrentVersion ? "按当前产品版本执行。" : ""}确定开始？`)) return;
        submitLock.current = true;
        setSubmitting(true);
        try {
          await onResume({...(frozen ? {} : {repair_missing_judges: repair, judge_use_gt_images: images}),
            ...(useCurrentVersion ? {product_build_revision: plan.current_product_build_revision} : {})});
        } finally { submitLock.current = false; setSubmitting(false); }
      }}>{submitting ? "正在提交…" : busy ? "已有任务运行，暂不可续跑" : "继续剩余 QA 答题"}</button></div>
      {versionBlocked ? <p className="recovery-note">产品版本已变化，请先确认使用当前版本。</p> : null}
      {busy ? <p className="recovery-note">请等待当前任务结束，再执行续跑。</p> : null}
      <p className="recovery-note">创建新任务，保留原任务及已执行失败的题目。</p>
    </>}
  </div>;
}
