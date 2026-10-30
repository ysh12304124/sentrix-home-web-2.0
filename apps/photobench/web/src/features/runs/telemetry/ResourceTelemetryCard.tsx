import { useEffect, useMemo, useRef, useState } from "react";
import * as echarts from "echarts";
import type { EChartsOption, EChartsType } from "echarts";
import type { Run } from "../../../api/types.ts";
import { fmtMemory } from "../../../lib/format.ts";
import {
  gpuMetricsView, RESOURCE_MEMORY_FIELDS, telemetryChart, telemetryLiveState,
  telemetryNumber, telemetryObservationRows, telemetryPlatform, telemetrySampleCount,
} from "../../../lib/telemetry.ts";

type Point = Record<string, any>;
type Line = { key: string; label: string; color?: string; gpu?: boolean };
type Graph = { title: string; unit: string; divisor: number; lines: Line[] };
const MEMORY_COLORS = ["#2563eb", "#ea580c", "#15803d", "#9333ea", "#dc2626", "#8b6b00"];
const GPU_TEMPERATURE_REASONS: Record<string, string> = {
  gpu_temperature_sensor_temporarily_unavailable: "GPU 温度节点暂时不可读，恢复读数后自动继续采集。",
  gpu_temperature_sensor_missing: "系统未提供 GPU 温度节点。",
  gpu_temperature_sensor_read_failed: "GPU 温度节点读取失败。",
  gpu_temperature_invalid_reading: "GPU 温度读数无效。",
  gpu_temperature_observation_expired: "GPU 温度读数已过期，等待下一次有效采样。",
};
const PHASE_COLORS: Record<string, string> = {
  model_deploy: "#64748b",
  scope_setup: "#795548", scope_attach: "#795548",
  identity_seed: "#7c3aed",
  photo_import: "#2563eb",
  pipeline_processing: "#ea580c",
  qa_eval: "#15803d",
  gpu_metrics: "#db2777",
  aggregate: "#a16207",
  unassigned: "#64748b",
};
const GRAPHS: Graph[] = [
  { title: "内存与显存占用", unit: "GiB", divisor: 1024,
    lines: RESOURCE_MEMORY_FIELDS.map(([label, key], i) => ({ label, key, color: MEMORY_COLORS[i], gpu: i >= 3 })) },
  { title: "利用率", unit: "%", divisor: 1, lines: [{ key: "cpu_utilization_pct", label: "整机 CPU" }, { key: "gpu_utilization_pct", label: "GPU" }] },
  { title: "温度", unit: "°C", divisor: 1, lines: [{ key: "cpu_temperature_c", label: "CPU" }, { key: "temperature_c", label: "GPU" }] },
  { title: "功率", unit: "W", divisor: 1, lines: [{ key: "power_draw_w", label: "采集范围内功率" }] },
];

function option(graph: Graph, history: Point[], snapshots: boolean, sampledDown: boolean, phaseColors: boolean): EChartsOption {
  const start = telemetryNumber(history[0]?.t) ?? 0;
  const times = history.map((point, i) => snapshots ? i + 1 : (telemetryNumber(point.t) ?? start) - start);
  const areas: Array<[{ name: string; xAxis: number; itemStyle: { color: string } }, { xAxis: number }]> = [];
  const pieces: Array<{ gte: number; lt?: number; lte?: number; color: string }> = [];
  if (!snapshots) {
    let first = 0;
    for (let i = 1; i <= history.length; i++) {
      if (i < history.length && history[i]?.phase === history[first]?.phase) continue;
      if (history[first]?.phase) {
        const color = PHASE_COLORS[history[first]!.phase] ?? "#64748b";
        const end = times[Math.min(i, history.length - 1)]!;
        areas.push([
          { name: history[first]!.phase_label || history[first]!.phase, xAxis: times[first]!, itemStyle: { color: `${color}16` } },
          { xAxis: end },
        ]);
        pieces.push({ gte: times[first]!, ...(i === history.length ? { lte: end } : { lt: end }), color });
      }
      first = i;
    }
  }
  return {
    animation: false,
    grid: { left: 65, right: 35, top: 56, bottom: 80 },
    legend: { type: "scroll", top: 6 },
    tooltip: { trigger: "axis", confine: true, valueFormatter: (value) => typeof value === "number" ? `${value.toFixed(2)} ${graph.unit}` : "未采集" },
    xAxis: { type: "value", min: snapshots ? 1 : 0, max: times.at(-1), boundaryGap: [0, 0],
      name: snapshots ? "快照序号" : "经过时间（秒）", nameLocation: "middle", nameGap: 32 },
    yAxis: { type: "value", min: 0, name: graph.unit },
    dataZoom: [{ type: "inside", filterMode: "none" }, { type: "slider", height: 18, bottom: 4 }],
    visualMap: phaseColors && pieces.length ? [{ type: "piecewise", show: false, dimension: 0,
      seriesIndex: graph.lines.map((_, index) => index), pieces, outOfRange: { color: "#64748b" } }] : [],
    series: graph.lines.map((line, lineIndex) => {
      const points: Array<[number, number | null]> = [];
      history.forEach((point, i) => {
        if (i && !snapshots && (point.gap_before === true || (!sampledDown && times[i]! - times[i - 1]! > 3))) {
          points.push([(times[i]! + times[i - 1]!) / 2, null]);
        }
        const value = telemetryNumber(point[line.key]);
        points.push([times[i]!, value === null ? null : value / graph.divisor]);
      });
      return { name: line.label, type: "line", showSymbol: false, connectNulls: false, smooth: false, data: points,
        emphasis: { focus: "series", blurScope: "coordinateSystem", lineStyle: { width: 3 } },
        blur: { lineStyle: { opacity: 0 }, itemStyle: { opacity: 0 } },
        itemStyle: { color: phaseColors ? "#64748b" : line.color },
        lineStyle: { ...(!phaseColors && line.color ? { color: line.color } : {}), type: line.gpu ? "dashed" as const : "solid" as const },
        ...(lineIndex === 0 && areas.length ? { markArea: { silent: true,
          label: { show: true, color: "#687888", fontSize: 10, position: "insideTop" as const }, data: areas } } : {}),
      };
    }),
  };
}

function ResourceGraph({ graph, history, snapshots, sampledDown, revision }: {
  graph: Graph; history: Point[]; snapshots: boolean; sampledDown: boolean; revision: string;
}) {
  const canvas = useRef<HTMLDivElement>(null);
  const chart = useRef<EChartsType | null>(null);
  const previous = useRef("");
  const [phaseColors, setPhaseColors] = useState(false);
  const latest = history.at(-1);
  const temperatureReason = latest?.resource_metrics?.gpu_temperature?.reason ?? latest?.resource_missing_reasons?.temperature_c;
  useEffect(() => {
    if (!canvas.current || history.length < 2) return;
    chart.current ??= echarts.init(canvas.current);
    const drawingRevision = `${revision}:${phaseColors}`;
    if (previous.current !== drawingRevision) {
      chart.current.setOption(option(graph, history, snapshots, sampledDown, phaseColors), { replaceMerge: ["series", "visualMap"] });
      previous.current = drawingRevision;
    }
    chart.current.resize();
    const resize = () => chart.current?.resize();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
    observer?.observe(canvas.current);
    // A parent details element may be collapsed while the chart is mounted.
    const parents: HTMLDetailsElement[] = [];
    let element: HTMLElement | null = canvas.current.parentElement;
    while (element) {
      if (element instanceof HTMLDetailsElement) { parents.push(element); element.addEventListener("toggle", resize); }
      element = element.parentElement;
    }
    return () => { observer?.disconnect(); parents.forEach(parent => parent.removeEventListener("toggle", resize)); };
  }, [graph, history, snapshots, sampledDown, revision, phaseColors]);
  useEffect(() => () => { chart.current?.dispose(); chart.current = null; previous.current = ""; }, []);
  return <div className="telemetry-chart"><div className="telemetry-chart-header"><b>{graph.title}</b>
    {!snapshots && history.some(point => point.phase) ? <label>
      <input type="checkbox" checked={phaseColors} onChange={event => setPhaseColors(event.target.checked)} />曲线按阶段着色
    </label> : null}</div>
    {history.length >= 2
      ? <div ref={canvas} className="telemetry-chart-canvas" role="img" aria-label={`${graph.title}趋势`} />
      : <p className="muted">等待至少两个采样点。</p>}
    {!history.some(point => graph.lines.some(line => telemetryNumber(point[line.key]) !== null))
      ? <small className="muted">此范围未采集到有效数据。</small> : null}
    {graph.title === "温度" && temperatureReason && telemetryNumber(latest?.temperature_c) === null
      ? <small className="muted">{GPU_TEMPERATURE_REASONS[temperatureReason] ?? "当前未采集到 GPU 温度。"}</small> : null}
  </div>;
}

const RESOURCE_LABELS: Record<string, string> = {
  model_process_memory: "主模型内存", product_process_memory: "产品内存", device_memory: "整机内存",
  model_gpu_memory: "主模型显存", product_gpu_memory: "产品显存", device_gpu_memory: "整机／整卡显存",
  cpu_utilization: "CPU 利用率", gpu_utilization: "GPU 利用率", cpu_temperature: "CPU 温度",
  gpu_temperature: "GPU 温度", power: "功率",
};
const POWER_LABELS: Record<string, string> = {
  gpu_device: "NVIDIA 显卡功率", module_estimate: "AGX 模块功率估计", module_input: "NX 模块输入功率",
  macmon_sys_power: "Mac 系统功率估计", system: "Mac 系统功率估计", system_estimate: "Mac 系统功率估计",
};
const STATUS_FIELDS = [
  ["CPU 利用率", "cpu_utilization_pct", "%"],
  ["GPU 利用率", "gpu_utilization_pct", "%"],
  ["CPU 温度", "cpu_temperature_c", "°C"],
  ["GPU 温度", "temperature_c", "°C"],
] as const;
function formatResource(value: unknown, unit: string): string {
  const number = telemetryNumber(value);
  return number === null ? "未采集" : `${number.toFixed(1)} ${unit}`;
}
function resourceStatistics(stats: Point | undefined, format: (value: unknown) => string) {
  const value = (key: string) => telemetryNumber(stats?.[key]) === null ? "未记录" : format(stats![key]);
  return <>{[["采样峰值", "peak"], ["P50", "p50"], ["P95", "p95"]].map(([label, key]) =>
    <span key={key} style={{ whiteSpace: "nowrap" }}>{label} {value(key)}</span>
  )}</>;
}
export function ResourceTelemetryCard({ run }: { run: Run | null | undefined }) {
  const live = telemetryLiveState(run);
  const stats = gpuMetricsView(run);
  const latest: Point = live.latest ?? {};
  const history = useMemo(() => telemetryChart(run)?.history ?? [], [run]);
  const snapshots = !Array.isArray(live.history) || live.history.length < 2;
  const sampledDown = telemetrySampleCount(run) > history.length;
  const isMac = telemetryPlatform(latest) === "apple_unified" || live.source === "apple_unified";
  const fields = isMac
    ? [...RESOURCE_MEMORY_FIELDS.slice(0, 3), ["整机显存（参考）", "device_gpu_memory_mib"] as const]
    : RESOURCE_MEMORY_FIELDS;
  const statusFields: ReadonlyArray<readonly [string, string, string]> = [...STATUS_FIELDS,
    [POWER_LABELS[latest.power_scope || stats.power_scope] ?? "功率（范围未记录）", "power_draw_w", "W"]];
  const graph = useMemo(() => isMac
    ? { ...GRAPHS[0]!, lines: [...GRAPHS[0]!.lines.slice(0, 3),
      { ...GRAPHS[0]!.lines[5]!, label: "整机显存（参考）" }] }
    : GRAPHS[0]!, [isMac]);
  const revision = JSON.stringify([history, snapshots, sampledDown, isMac]);
  const [extra, setExtra] = useState(false);
  const sampleCount = telemetrySampleCount(run);
  const diagnostics = telemetryObservationRows(run);
  if (!sampleCount && !diagnostics.length) return null;
  return <details open className="phase-card result-phase-card gpu-result-card live-telemetry-card">
    <summary className="phase-title"><b>实时资源遥测</b><span className={`phase-status ${live.status === "running" ? "running" : "completed"}`}>
      {live.status === "running" ? "实时更新中" : live.status === "stop_pending" ? "停止未确认" : "已停止"}
    </span></summary>
    <p className="metric-calc-time">内存与显存统一用 GiB 展示，每项颜色不同，背景色区分阶段。悬浮图例可单独查看一项。产品包含主模型及其他产品进程，各项不相加。</p>
    <div className="phase-metrics live-telemetry-metrics">{fields.map(([label, key]) =>
      <div className="phase-metric" key={key}><span>{label}</span>
        <strong>{telemetryNumber(latest[key]) === null ? "未采集" : fmtMemory(latest[key])}</strong>
        <small>{resourceStatistics(stats[key], fmtMemory)}</small>
      </div>)}
    </div>
    <div className="phase-metrics resource-status-metrics" aria-label="利用率、温度与功率指标">
      {statusFields.map(([label, key, unit]) =>
        <div className="phase-metric" key={key}><span>{label}</span>
          <strong>{formatResource(latest[key], unit)}</strong>
          <small>{resourceStatistics(stats[key], value => formatResource(value, unit))}</small>
        </div>)}
    </div>
    <ResourceGraph {...{ graph, history, snapshots, sampledDown, revision }} />
    <small className="muted">{snapshots ? "阶段快照，横轴为序号。" : run?.history_mode === "full"
      ? `完整测评过程，${sampleCount} 个采样点，绘制 ${history.length} 点。`
      : `当前遥测窗口，绘制 ${history.length} 点，整轮已采集 ${sampleCount} 点。`}
      {!snapshots ? `${run?.history_mode === "full" ? "横轴从本轮首个采样点计时。" : ""}峰值、P50、P95取本轮有效采样，缩放不改变统计。` : ""}</small>
    <details onToggle={event => setExtra(event.currentTarget.open)}>
      <summary>利用率、温度与功率</summary>
      <p className="muted">功率范围：{POWER_LABELS[latest.power_scope || stats.power_scope] ?? "未记录"}。不同设备的功率范围可能不同。</p>
      {extra ? GRAPHS.slice(1).map(graph => <ResourceGraph key={graph.title} {...{ graph, history, snapshots, sampledDown, revision }} />) : null}
    </details>
    <details className="metric-definition-panel"><summary>指标说明与采集状态</summary>
      <div className="resource-definition-table-wrap" role="region" aria-label="各平台主模型内存与显存采集方法" tabIndex={0}>
        <table className="resource-definition-table">
          <caption>主模型占用：Σ 表示进程合计，相同 PID（进程编号）只计一次</caption>
          <thead><tr><th scope="col">平台</th><th scope="col">主模型内存</th><th scope="col">主模型显存</th></tr></thead>
          <tbody>
            <tr><th scope="row">3090（独立显卡）</th>
              <td>读取 /proc 的 smaps_rollup。<br /><code>内存 = Σ 进程 PSS</code><br />PSS 按比例分摊共享内存页。</td>
              <td>读取 NVIDIA NVML 的进程显存。<br /><code>显存 = Σ 当前显卡上模型进程的 usedGpuMemory</code><br />同一 PID 的计算与图形记录取较大值，避免重复。</td></tr>
            <tr><th scope="row">NX / AGX（统一内存）</th>
              <td>读取 /proc 的 smaps_rollup。<br /><code>内存 = Σ 进程 PSS</code></td>
              <td>读取 nvmap iovmm/clients 的分配记录。<br /><code>显存 = Σ 模型进程的 nvmap 分配量</code><br />表示 GPU 分配的共享内存。</td></tr>
            <tr><th scope="row">Mac mini（统一内存）</th>
              <td>通过 proc_pid_rusage 读取内核 phys_footprint（进程物理内存负担）。<br /><code>内存 = Σ 进程 ri_phys_footprint</code></td>
              <td>当前模型服务未提供进程级读数，显示“未记录”。IORegistry 的整机 GPU 分配量另作参考，包含其他应用。</td></tr>
          </tbody>
        </table>
      </div>
      <ul className="resource-definition-notes">
        <li><strong>统计范围：</strong>主模型包含所选服务的相关进程；同一进程内的目标模型和草稿模型合并统计。产品包含主模型、产品后端、嵌入及视觉等服务，按 PID 去重。线程共享所属进程的内存，不单独求和。</li>
        <li><strong>产品与整机：</strong>Linux 产品内存为各进程 PSS 合计；Mac 产品内存仍读取 footprint 进程组的驻留估计，公式为脏页 − 已换出页 + 干净页 + 可回收页（Dirty − Swapped + Clean + Reclaimable）。Mac 逐进程明细记录 phys_footprint，与该组统计的口径不同。整机内存为总物理内存减空闲页，包含缓存。</li>
        <li><strong>显存与单位：</strong>产品显存对产品进程按上述平台方法统计，必要来源不齐时留空。NX、AGX、Mac 的 GPU 与 CPU 使用同一物理内存池，内存与显存可能重叠，不相加。1 GiB = 1024 MiB；1 MiB = 1,048,576 字节。</li>
        <li><strong>采样与统计：</strong>目标每秒采样，来源超过 3 秒有效期即留空。峰值取有效采样最大值，P50 为中位数，P95 表示约 95% 的有效采样不超过该值；缺失值不补零。历史记录按该次保存的采集方法解释。</li>
        <li><strong>温度与功率：</strong>NX / AGX 的 GPU 温度读取系统 GPU 温度节点。功率按图中标明的硬件范围解释。来源不可读时留空。</li>
      </ul>
      {Object.entries(latest.resource_metrics ?? {}).filter(([, raw]) => (raw as Point).status !== "available").map(([key, raw]) =>
        <p key={key}>{RESOURCE_LABELS[key] ?? "资源指标"}：{GPU_TEMPERATURE_REASONS[(raw as Point).reason] ?? ((raw as Point).reason === "runtime_does_not_expose_process_gpu_bytes" ? "模型服务未提供进程显存占用" : (raw as Point).reason === "product_gpu_sources_incomplete" ? "产品显存来源未齐全" : "来源不可用或已过期")}</p>)}
      {diagnostics.map(row => <p key={row[0]}>{row[0]}：{row[1]} · {row[2]}</p>)}
    </details>
  </details>;
}
