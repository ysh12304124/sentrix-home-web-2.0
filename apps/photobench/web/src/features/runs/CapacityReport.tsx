import { useEffect, useState } from "react";
import "./capacity.css";

export interface CapacityRound {
  label: string;
  run_id: string;
  concurrency: number;
  questions: number;
  completed: number;
  judge_completed: number;
  judge_failed: number;
  status: string;
  passed: boolean;
  qa_per_min: number | null;
  qa_seconds: number | null;
  judge_seconds: number | null;
  latency_p50_s: number | null;
  latency_p95_s: number | null;
  ttft_p95_s: number | null;
  actual_batch_peak: number | null;
  queue_peak: number | null;
  memory_peak_gib: number | null;
  swap_delta_gib: number | null;
  failures: Record<string, number>;
  resource_summary?: { samples: number; engine_metrics_missing_samples: number };
  model_call_evidence?: { expected_calls: number; recorded_calls: number; verified: boolean };
  prefix_cache_evidence?: { verified: boolean; observed_requests: number; expected_requests: number; requests_with_reuse: number; cached_tokens: number; token_hit_rate: number | null };
  metrics_probe_timeout_s?: number[];
  samples: { seconds: number; cpu: number | null; gpu: number | null; memory: number; swap: number }[];
}

export interface CapacityEvidence {
  schema_version: 1;
  updated_at: string;
  hardware: string;
  engine: string;
  model: string;
  model_sha256: string;
  dataset_sha256: string;
  context_tokens: number;
  conditions: string[];
  rounds: CapacityRound[];
  baseline_rounds?: CapacityRound[];
  prior_optimization_rounds?: CapacityRound[];
  diagnostic_rounds?: CapacityRound[];
  baseline_reference_mode?: string;
  interrupted_rounds?: { label: string; qa_completed: number; judge_completed: number; reason: string }[];
  speed_gate?: { passed: boolean; baseline_qpm: number; candidate_qpm: number; weighted_speedup: number } | null;
}

const number = (value: number | null | undefined, digits = 1) =>
  typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "未采集";

function ResourceChart({ round, memory = false }: { round: CapacityRound; memory?: boolean }) {
  const samples = round.samples;
  const maxX = Math.max(1, ...samples.map((s) => s.seconds));
  const maxY = memory ? Math.max(1, ...samples.flatMap((s) => [s.memory, s.swap])) * 1.1 : 100;
  const series = memory
    ? [{ key: "memory" as const, label: "活跃及有线内存", color: "#2454a6" }, { key: "swap" as const, label: "交换空间", color: "#c75b37" }]
    : [{ key: "cpu" as const, label: "CPU", color: "#2454a6" }, { key: "gpu" as const, label: "GPU", color: "#1b8b75" }];
  return <figure className="capacity-chart">
    <figcaption>{memory ? "内存与交换空间（GiB）" : "CPU / GPU 利用率（%）"}</figcaption>
    <svg viewBox="0 0 580 210" role="img" aria-label={memory ? "内存与交换空间曲线" : "CPU 和 GPU 利用率曲线"}>
      {[0, .5, 1].map((fraction) => <g key={fraction}>
        <line x1="44" x2="566" y1={170 - 145 * fraction} y2={170 - 145 * fraction} stroke="#dce2ec" />
        <text x="4" y={175 - 145 * fraction} fontSize="11" fill="#65738b">{number(maxY * fraction, 0)}</text>
      </g>)}
      {series.map((s) => <polyline key={s.key} fill="none" stroke={s.color} strokeWidth="2"
        points={samples.filter((p) => p[s.key] !== null).map((p) => `${44 + p.seconds / maxX * 522},${170 - (p[s.key] ?? 0) / maxY * 145}`).join(" ")} />)}
      <text x="44" y="195" fontSize="11" fill="#65738b">0 分钟</text>
      <text x="515" y="195" fontSize="11" fill="#65738b">{number(maxX / 60)} 分钟</text>
    </svg>
    <div>{series.map((s) => <span key={s.key} style={{ color: s.color }}>{s.label}　</span>)}</div>
  </figure>;
}

export function CapacityReport({ evidence }: { evidence?: CapacityEvidence }) {
  const [report, setReport] = useState<CapacityEvidence | null>(evidence ?? null);
  const [error, setError] = useState("");
  const [selection, setSelection] = useState("");
  useEffect(() => {
    if (evidence) return;
    let active = true;
    const load = async () => {
      try {
        const response = await fetch("./capacity-report.json", { cache: "no-store" });
        if (!response.ok) throw new Error("本部署尚未发布并发实验记录。");
        const data = await response.json() as CapacityEvidence;
        if (data.schema_version !== 1 || !Array.isArray(data.rounds)) throw new Error("实验记录格式不兼容。");
        if (active) { setReport(data); setError(""); }
      } catch (e) { if (active) setError(e instanceof Error ? e.message : "实验记录读取失败。"); }
    };
    void load();
    const timer = setInterval(() => { void load(); }, 30000);
    return () => { active = false; clearInterval(timer); };
  }, [evidence]);
  if (!report) return <section className="capacity-report"><h2>MLX 并发实验</h2><p>{error || "正在读取实验记录…"}</p></section>;
  const formal = report.rounds.filter((r) => r.questions === 64);
  const tiers = [4, 8, 12, 16].filter((c) => formal.filter((r) => r.concurrency === c && r.passed).length >= 2);
  const full = report.rounds.findLast((r) => r.questions === 487);
  const best = tiers.map((c) => {
    const rounds = formal.filter((r) => r.concurrency === c && r.passed && r.qa_seconds !== null);
    const seconds = rounds.reduce((sum, r) => sum + (r.qa_seconds ?? 0), 0);
    return { concurrency: c, throughput: seconds > 0 ? rounds.reduce((sum, r) => sum + r.completed, 0) / seconds * 60 : null };
  }).filter((tier) => tier.throughput !== null).sort((a, b) => (b.throughput ?? 0) - (a.throughput ?? 0))[0];
  const allRounds = [...(report.baseline_rounds ?? []), ...(report.prior_optimization_rounds ?? []), ...(report.diagnostic_rounds ?? []), ...report.rounds];
  const selected = allRounds.find((r) => r.run_id === selection) ?? full ?? report.rounds.at(-1) ?? allRounds.at(-1);
  return <section className="capacity-report">
    <div className="capacity-heading"><div><p className="capacity-eyebrow">PHOTOBENCH · APPLE SILICON</p><h2>MLX 并发实验</h2></div><span>更新于 {report.updated_at}</span></div>
    {error ? <p role="alert">{error} 当前显示上次成功读取的记录。</p> : null}
    <p className="capacity-identity">{report.hardware} · {report.model} · {report.engine} · 上下文 {report.context_tokens.toLocaleString()} tokens</p>
    <div className="capacity-cards">
      <article><span>{report.baseline_rounds?.length ? "优化配置通过并发 · 两轮均通过" : "最高通过并发 · 两轮均通过"}</span><strong>{tiers.length ? `C${Math.max(...tiers)}` : "待验收"}</strong></article>
      <article><span>{report.baseline_rounds?.length ? "优化配置吞吐 · 两轮合计" : "吞吐最佳档 · 两轮合计"}</span><strong>{best ? `C${best.concurrency} · ${number(best.throughput)} 题/分钟` : "待验收"}</strong></article>
      <article><span>487 题独立测试</span><strong>{full ? `${full.completed} / 487` : "尚未开始"}</strong><small>{full ? `评分完成 ${full.judge_completed}，评分失败 ${full.judge_failed}` : "在最高通过档运行"}</small></article>
    </div>
    {report.baseline_reference_mode === "single_completed_trial_user_cancel" ? <p>速度对照仅采用原配置 C16 第一轮；第二轮按用户要求取消，不计入吞吐或通过档位。</p> : null}
    {report.interrupted_rounds?.map((round) => <p key={round.label}>{round.label} · 按用户要求取消 · 已完成 QA {round.qa_completed}、评分 {round.judge_completed}；结果保留，排除性能比较。</p>)}
    {report.speed_gate ? <p role="status">优化速度验收：{report.speed_gate.passed ? "通过" : "未通过"}；原 C16 {number(report.speed_gate.baseline_qpm, 3)} → 优化 C16 {number(report.speed_gate.candidate_qpm, 3)} 题/分钟，提升 {number((report.speed_gate.weighted_speedup - 1) * 100)}%。要求两轮合计提升至少 15%，每轮提升至少 10%。</p> : null}
    {report.prior_optimization_rounds?.length ? <section aria-label="先前优化结果">
      <h3>先前优化 · 各轮原始验收结果</h3>
      <p>保留原始结果，不计入当前候选的两轮验收。执行验收与整组速度门槛分别判断；旧版 Judge 终态计数不代表有效评分数。</p>
      {report.prior_optimization_rounds.map((r) => <p key={r.run_id}><button onClick={() => setSelection(r.run_id)}>{r.label}</button> · {r.passed ? "执行验收通过" : "执行验收未通过"} · QA {r.completed}/{r.questions} · Judge 记录终态 {r.judge_completed}/{r.questions} · {number(r.qa_per_min, 3)} 题/分钟</p>)}
    </section> : null}
    {report.diagnostic_rounds?.length ? <section aria-label="独立诊断结果">
      <h3>独立诊断 · 不计入正式速度门槛</h3>
      {report.diagnostic_rounds.map((r) => <p key={r.run_id}><button onClick={() => setSelection(r.run_id)}>{r.label}</button> · QA {r.completed}/{r.questions} · {number(r.qa_seconds)} 秒 · {r.passed ? "执行通过" : "执行未通过"}</p>)}
    </section> : null}
    {(report.baseline_rounds?.length ? [
      { title: report.baseline_reference_mode === "single_completed_trial_user_cancel" ? "原配置基线 · 已完成轮次" : "原配置基线 · 四档并发 · 同一组 64 题 · 各两轮", rows: report.baseline_rounds },
      { title: "优化配置 · 16 并发 · 同一组 64 题 · 两轮", rows: formal },
    ] : [{ title: "四档并发 · 同一组 64 题 · 各两轮", rows: formal }]).map((section) => <div key={section.title}>
    <h3>{section.title}</h3>
    <p>客户端并发是同时执行的 QA 数；实际模型批次来自推理调度器。QA 与云端评分分别计时。</p>
    <div className="capacity-table-wrap"><table><thead><tr>
      {["轮次", "QA 完成", "评分完成 / 失败", "QA 题/分钟", "QA P50 / P95 秒", "首 token P95 秒", "实际批次峰值", "队列峰值", "活跃/有线峰值 GiB", "交换增量 GiB", "验收"].map((h) => <th key={h}>{h}</th>)}
    </tr></thead><tbody>{section.rows.map((r) => <tr key={r.run_id}>
      <td><button onClick={() => setSelection(r.run_id)}>{r.label}</button></td><td>{r.completed}/{r.questions}</td><td>{r.judge_completed} / {r.judge_failed}</td>
      <td>{number(r.qa_per_min)}</td><td>{number(r.latency_p50_s)} / {number(r.latency_p95_s)}</td><td>{number(r.ttft_p95_s)}</td>
      <td>{number(r.actual_batch_peak, 0)}</td><td>{number(r.queue_peak, 0)}</td><td>{number(r.memory_peak_gib)}</td><td>{number(r.swap_delta_gib, 2)}</td>
      <td className={r.passed ? "capacity-pass" : "capacity-pending"}>{r.passed ? "通过" : "未通过"}{r.resource_summary?.engine_metrics_missing_samples ? <small> · 指标缺失 {r.resource_summary.engine_metrics_missing_samples} 次</small> : null}</td>
    </tr>)}</tbody></table></div></div>)}
    {full ? <section aria-label="487 题独立测试总览">
      <h3>487 题独立测试 · C{full.concurrency}</h3>
      <p>{full.label} · {full.passed ? "验收通过" : "验收未通过"} · 独立恢复记忆库与会话</p>
      <div className="capacity-cards">
        <article><span>QA 完成 / 总题数</span><strong>{full.completed} / {full.questions}</strong><small>{number(full.qa_seconds)} 秒 · {number(full.qa_per_min)} 题/分钟</small></article>
        <article><span>Judge 完成 / 失败</span><strong>{full.judge_completed} / {full.judge_failed}</strong><small>独立评分阶段 {number(full.judge_seconds)} 秒</small></article>
        <article><span>实际模型批次 / 队列采样峰值</span><strong>{number(full.actual_batch_peak, 0)} / {number(full.queue_peak, 0)}</strong><small>客户端 QA 并发 {full.concurrency}</small></article>
      </div>
      <p>QA 延迟 P50 / P95：{number(full.latency_p50_s)} / {number(full.latency_p95_s)} 秒；首 token P95：{number(full.ttft_p95_s)} 秒。</p>
      <p>活跃及有线内存峰值 {number(full.memory_peak_gib)} GiB；交换空间增量 {number(full.swap_delta_gib, 2)} GiB。</p>
    </section> : null}
    {selected ? <>
      <div className="capacity-heading"><h3>{selected.label} · 资源与耗时</h3><select aria-label="选择资源曲线轮次" value={selected.run_id} onChange={(e) => setSelection(e.target.value)}>{allRounds.map((r) => <option key={r.run_id} value={r.run_id}>{r.label}</option>)}</select></div>
      <p>QA {number(selected.qa_seconds)} 秒 · Judge {number(selected.judge_seconds)} 秒 · 状态 {selected.status} · {selected.completed}/{selected.questions} 题完成</p>
      {selected.resource_summary ? <p>模型指标采样 {selected.resource_summary.samples - selected.resource_summary.engine_metrics_missing_samples}/{selected.resource_summary.samples} 次成功；缺失 {selected.resource_summary.engine_metrics_missing_samples} 次，未补值。队列峰值是已观测到的采样峰值。</p> : null}
      {selected.model_call_evidence ? <p>实际模型调用记录 {selected.model_call_evidence.recorded_calls}/{selected.model_call_evidence.expected_calls} 次；与应用调用及 token 数{selected.model_call_evidence.verified ? "完整对应" : "尚未核对通过"}。</p> : null}
      {selected.prefix_cache_evidence ? <p>实际前缀复用：{selected.prefix_cache_evidence.verified ? `${selected.prefix_cache_evidence.requests_with_reuse}/${selected.prefix_cache_evidence.expected_requests} 次调用，${selected.prefix_cache_evidence.cached_tokens.toLocaleString()} 输入 token（${number((selected.prefix_cache_evidence.token_hit_rate ?? 0) * 100)}%）` : "证据尚未完整核对，不作为缓存命中率"}。</p> : null}
      {selected.metrics_probe_timeout_s ? <p>本轮只读指标查询期限：{selected.metrics_probe_timeout_s.join(" / ")} 秒；推理请求期限另按固定测试配置执行。</p> : null}
      <div className="capacity-charts"><ResourceChart round={selected} /><ResourceChart round={selected} memory /></div>
      {Object.keys(selected.failures).length ? <p>失败分类：{Object.entries(selected.failures).map(([name, count]) => `${name} ${count}`).join("；")}</p> : null}
    </> : null}
    <h3>测试条件与证据</h3><ul>{report.conditions.map((condition) => <li key={condition}>{condition}</li>)}</ul>
    <p className="capacity-hashes">模型 SHA256：{report.model_sha256}<br />487 题 SHA256：{report.dataset_sha256}</p>
  </section>;
}
