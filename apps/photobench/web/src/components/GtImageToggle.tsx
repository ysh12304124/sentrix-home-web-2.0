import { useId } from "react";

/** Controlled option shared by new runs, resume and scoring. */
export function GtImageToggle({ checked, disabled = false, onChange }: {
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  const descriptionId = useId();
  return <span className="gt-image-option">
    <label className={"btn ghost gt-image-toggle" + (checked ? " active" : "") + (disabled ? " disabled" : "")}
      title="标注图片仅供质量评分模型参考，不传给答题模型，不附视频">
      <input type="checkbox" aria-label="GT 图片辅助评分" aria-describedby={descriptionId}
        checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)} />
      GT 图片辅助评分
    </label>
    <span id={descriptionId} className="gt-image-hint">{checked ? "向评分模型提供标注图片；需支持图片输入" : "仅使用文字参考答案评分"}</span>
  </span>;
}
