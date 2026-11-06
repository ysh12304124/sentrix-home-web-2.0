type Dict = Record<string, unknown>;
const obj = (v: unknown): Dict => v && typeof v === "object" ? v as Dict : {};
const pct = (v: unknown) => typeof v === "number" ? (v * 100).toFixed(1) + "%" : "未评分";
const names: Record<string, string> = {choice: "选择题语义匹配", media_list: "媒体列表", open: "开放回答", number: "ATM 数值", list_recall: "ATM 列表召回", open_end: "ATM 开放问答"};
const scorerTypes: Record<string, string> = {choice: "choice", number: "number", list: "list_recall", open: "open_end", answer_quality: "open", atm_number: "number", atm_list: "list_recall", atm_open: "open_end"};
export function evaluationTypeLabel(value: unknown): string {
  const d = obj(value);
  const key = String(d.source_qtype || scorerTypes[String(d.scorer)] || d.answer_format || "");
  return names[key] || key;
}
export function EvaluationTypeBadge({value}: {value: unknown}) {
  const label = evaluationTypeLabel(value);
  return label ? <span className="evaluation-type-badge">题型 · {label}</span> : null;
}
function scoringExplanation(value: unknown): string {
  const d = obj(value);
  if (d.scorer === "answer_quality") return "开放式回答：Judge 根据问题、参考答案与模型回答按 0 / 1 / 2 分评价回答质量。选项匹配由独立的 ATM-Bench式QS 评分记录展示。";
  if (d.scorer === "atm_number") return "数值题：按 ATM 官方数值规则计主分；语义辅分单独展示。";
  if (d.scorer === "atm_list") return "列表召回题：按实际交付的图片 / 视频 ID 集合计算主分；工具累计召回单列展示。";
  if (d.source_qtype === "open_end" || d.scorer === "atm_open") return "开放问答题：Judge 按回答内容判 0 / 1 分；媒体交付和工具证据另列指标。";
  if (d.answer_format === "media_list") return "列表召回题：按实际交付媒体集合评分。";
  if (d.answer_format === "choice") return "选择题：选项仅供评测端使用，答题模型回答原问题。Judge 逐项匹配回答含义，程序核对正确项；全部不匹配归为“其他”，记 0 分。";
  return "开放问答题：按题目定义评价回答内容，媒体指标单独计算。";
}
export function evaluationReason(value: unknown): string {
  const reason = String(value || "");
  const labels: Record<string, string> = {
    media_set: "按模型实际交付与目标媒体集合的重合度评分。",
    delivery_unavailable: "交付媒体记录缺失，无法计算集合得分。",
    "ATM-Bench ef4e5dff deterministic_accuracy": "按 ATM 官方数值匹配规则计主分；数值语义辅分单独展示。",
  };
  return labels[reason] || reason;
}
export function ChoiceMatchingResult({value, definition, standalone = false}: {value: unknown; definition?: unknown; standalone?: boolean}) {
  const r = obj(value), d = obj(definition);
  const comparisons = Array.isArray(r.comparisons) ? r.comparisons.map(obj) : [];
  const options = Array.isArray(d.options) ? d.options.map(obj) : [];
  const choiceOptions: Dict[] = [...options, ...comparisons.filter(c => !options.some(o => o.id === c.option_id)).map(c => ({id: c.option_id}))];
  if (!choiceOptions.length) return null;
  const matched = r.matched_option;
  const verdict = matched == null ? "尚无匹配结论" : matched === "other" ? "给定选项均不匹配" : !d.correct_option ? "正确项未记录" : matched === d.correct_option ? "匹配正确" : "匹配错误";
  return <section aria-label="选项语义匹配" className={standalone ? "evaluation-panel" : ""}>
    {standalone && <><h4>ATM-Bench式QS · 选项语义匹配</h4><p className="muted small">答题模型回答原问题，Judge 将回答与选项逐项比较；这里展示 Judge 的匹配结果。</p></>}
    <p className="evaluation-score">{verdict}{standalone && <> · QS {r.score == null ? (r.judge_status === "not_collected" ? "未运行采集" : "未评分") : String(r.score) + "/1 分"}</>}</p>
    <div className="evaluation-choice-definition">
      <p className="muted small">{r.matched_option != null ? <>Judge匹配：{r.matched_option === "other" ? "其他（给定选项之外）" : String(r.matched_option)} · 正确选项：{String(d.correct_option || "未记录")}</> : <>尚无匹配结论 · 正确项 {String(d.correct_option || "未记录")}</>}</p>
      <ul className="evaluation-options">{choiceOptions.map(o => {
        const comparison = comparisons.find(c => c.option_id === o.id);
        const matches = comparison?.matches ?? (r.matched_option === o.id ? true : undefined);
        return <li className={[o.id === d.correct_option ? "is-correct" : "", matches === true ? "is-match" : ""].filter(Boolean).join(" ")} key={String(o.id)}>
          <span className="evaluation-option-id">{String(o.id)}</span>
          <div className="evaluation-option-content">
            <div className="evaluation-option-heading">
              <span className="evaluation-option-text">{String(o.text || "选项原文未记录")}</span>
              {o.id === d.correct_option && <span className="evaluation-badge">正确项</span>}
              <span className={"evaluation-badge" + (matches === true ? "" : " neutral")}>{matches === true ? "匹配" : matches === false ? "不匹配" : "未记录逐项比较"}</span>
            </div>
            {comparison?.reason != null && <p className="evaluation-option-reason">{String(comparison.reason)}</p>}
          </div>
        </li>;
      })}</ul>
    </div>
    {standalone && <><p className="muted small">第 {Number(r.turn_index || 0) + 1} 轮 · 评分批次 {String(r.batch_id || "未记录")}</p><small>模型 {String(r.judge_model || "未记录")}</small></>}
  </section>;
}
export function EvaluationResult({value, definition, showChoiceMatching = true}: {value: unknown; definition?: unknown; showChoiceMatching?: boolean}) {
  const r = obj(value), d = obj(definition), metrics = obj(r.media_metrics);
  if (!r.scorer) return null;
  return <section aria-label="题型评分" className="evaluation-panel">
    <h4>{evaluationTypeLabel({...d, ...r}) || String(r.scorer)}</h4>
    <p className="muted small">{scoringExplanation({...d, ...r})}</p>
    <p className="evaluation-score">主分 {r.score == null ? (r.judge_status === "not_collected" ? "未运行采集" : "未评分") : String(r.score) + "/" + String(r.max_score)} · 归一化 {pct(r.normalized_score)} · {r.judge_status === "needs_review" ? "题目待复核" : r.judge_status === "failed" ? "评分失败" : r.score == null ? "未完成" : "已评分"}</p>
    <p>{evaluationReason(r.reason)}</p>
    {showChoiceMatching && (["choice", "choice_judge"].includes(String(r.scorer)) || r.matched_option != null || Array.isArray(r.comparisons)) && <ChoiceMatchingResult value={r} definition={d} />}
    {Object.keys(metrics).length > 0 && <div className="evaluation-table-wrap"><table><thead><tr><th>集合</th><th>Recall</th><th>Jaccard</th><th>Precision</th><th>F1</th></tr></thead><tbody>{["delivery", "tools"].map(k => {const m = obj(metrics[k]); return <tr key={k}><td>{k === "delivery" ? "模型交付" : "工具累计召回"}</td>{["recall", "jaccard", "precision", "f1"].map(v => <td key={v}>{pct(m[v])}</td>)}</tr>;})}</tbody></table></div>}
    {Object.keys(metrics).length > 0 && <details className="evaluation-disclosure"><summary>GT、预测、交集与差集</summary><pre>{JSON.stringify(metrics, null, 2)}</pre></details>}
    {r.number_aux != null && <p>LLM 数值辅分 {pct(obj(r.number_aux).score)} · 与官方分{r.number_aux_disagrees === true ? "不一致" : r.number_aux_disagrees === false ? "一致" : "尚不可比较"}</p>}
    {(r.number_aux != null || r.semantic_aux != null) && <details className="evaluation-disclosure"><summary>辅分与归一化依据</summary><pre>{JSON.stringify(r.number_aux || r.semantic_aux, null, 2)}</pre></details>}
    <small>协议 {String(r.version || r.rule_version || d.version || "未记录")} · 模型 {String(r.judge_model || (["media_set", "atm_list", "list", "atm_number"].includes(String(r.scorer)) ? "确定性评分" : "未记录"))}</small>
  </section>;
}
export function EvaluationSummary({value}: {value: unknown}) {
  const r = obj(value); if (!r.count) return null;
  return <section aria-label="评分协议汇总" className="evaluation-panel"><h3>题型评分汇总</h3>
    <p className="evaluation-score">逐题平均归一化分 {pct(r.mean)} · 覆盖 {String(r.scored)}/{String(r.count)}（{pct(r.coverage)}） · 其他 {String(r.other_count || 0)} 题</p>
    <p>按当前题集逐题汇总，仅纳入有效评分；覆盖不全时不作为完整 QS。ATM 主集、hard 和小测集应分别比较。选项仅供评测端匹配，列表按实际交付媒体评分。</p>
    <div className="evaluation-table-wrap"><table><thead><tr><th>题型</th><th>有效 / 总数</th><th>平均归一化分</th></tr></thead><tbody>{Object.entries(obj(r.by_type)).map(([k, v]) => {const row=obj(v); return <tr key={k}><td>{names[k] || k}</td><td>{String(row.scored)}/{String(row.count)}</td><td>{pct(row.mean)}</td></tr>;})}</tbody></table></div>
    <p>数值辅分不一致题：{Array.isArray(r.number_disagreement_ids) ? r.number_disagreement_ids.join("、") || "无" : "未记录"}</p>
    <details className="evaluation-disclosure"><summary>模型与工具集合平均指标</summary><pre>{JSON.stringify(r.media_metrics, null, 2)}</pre><p>累计集合指标不等于有序检索的 Recall@k 或 Recall@kGT。</p></details>
  </section>;
}

export function EvaluationDefinition({value}: {value: unknown}) {
  const d = obj(value);
  if (!d.scorer) return null;
  const options = Array.isArray(d.options) ? d.options.map(obj) : [];
  return <details className="evaluation-definition"><summary><span>评分定义与选项</span><EvaluationTypeBadge value={d} /><span className="evaluation-badge neutral">仅评测端</span></summary>
    <div className="evaluation-definition-body">
      <p>{scoringExplanation(d)}</p>
      <p>{evaluationTypeLabel(d)} · 第 {Number(d.applies_to_turn || 0) + 1} 轮 · 满分 {String(d.max_score)}</p>
      {options.length > 0 && <ul className="evaluation-options">{options.map(o => <li className={o.id === d.correct_option ? "is-correct" : ""} key={String(o.id)}><span className="evaluation-option-id">{String(o.id)}</span><span className="evaluation-option-text">{String(o.text)}</span>{o.id === d.correct_option && <span className="evaluation-badge">正确项</span>}</li>)}</ul>}
      {d.answer_format === "media_list" && <p>{d.retrieval_requirement === "any" ? "命中任意一个目标即可" : "按完整目标集合评分"}</p>}
      <small>评分协议 {String(d.version)} · 选项和答案仅供评测使用</small>
    </div>
  </details>;
}
