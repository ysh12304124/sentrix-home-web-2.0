import { StrictMode } from "react";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Run } from "../../../api/types.ts";
import { RESOURCE_PROTOCOL } from "../../../lib/telemetry.ts";
import { TelemetryCard } from "./TelemetryCard.tsx";
const charts = vi.hoisted(() => ({ instances: [] as any[], init: vi.fn() }));
vi.mock("echarts", () => ({ init: charts.init }));
beforeEach(() => {
  charts.instances.length = 0;
  charts.init.mockReset().mockImplementation(() => {
    const instance = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() };
    charts.instances.push(instance);
    return instance;
  });
});
afterEach(cleanup);
function run(): Run {
  const history = [0, 1, 5].map((t, i) => ({ t, protocol_version: RESOURCE_PROTOCOL,
    system_memory_used_mib: 1024, model_process_system_memory_used_mib: i === 1 ? null : 256,
    product_stack_memory_mib: 512, device_gpu_memory_mib: 2048, model_process_gpu_memory_mib: 1536,
    phase: i < 2 ? "photo_import" : "qa_eval", phase_label: i < 2 ? "数据导入" : "QA 测评",
    product_stack_gpu_memory_mib: null, cpu_utilization_pct: 50, power_draw_w: 10, power_scope: "macmon_sys_power" }));
  return { run_id: "new", status: "running", telemetry_live: { protocol_version: RESOURCE_PROTOCOL,
    source: "host_nvml", status: "running", samples_count: 3, history, latest: history[2],
    peak: { system_memory_used_mib: 2048 } } } as Run;
}
describe("resource telemetry v1", () => {
  it("shows resource summaries before expanding charts and keeps missing current values separate from peaks", () => {
    const value = run();
    Object.assign(value.telemetry_live!.latest!, { cpu_utilization_pct: 0, gpu_utilization_pct: 73.25, cpu_temperature_c: 55.5, temperature_c: null });
    value.telemetry_live!.field_stats = { temperature_c: { peak: 61.2 }, power_draw_w: { peak: 22 } };
    const view = render(<TelemetryCard run={value} />);
    const cards = view.getByLabelText("利用率、温度与功率指标");
    expect(cards.children.length).toBe(5);
    const text = Array.from(cards.children).map(card => card.textContent);
    expect(text[0]).toContain("CPU 利用率0.0 %");
    expect(text[1]).toContain("GPU 利用率73.3 %");
    expect(text[2]).toContain("CPU 温度55.5 °C");
    expect(text[3]).toContain("GPU 温度未采集采样峰值 61.2 °C");
    expect(text[4]).toContain("Mac 系统功率估计10.0 W采样峰值 22.0 W");
    expect(charts.init).toHaveBeenCalledTimes(1);
  });
  it("labels unknown power scope explicitly and leaves absent readings uncollected", () => {
    const value = run();
    delete value.telemetry_live!.latest!.power_scope;
    delete value.telemetry_live!.latest!.power_draw_w;
    const view = render(<TelemetryCard run={value} />);
    const cards = view.getByLabelText("利用率、温度与功率指标");
    expect(cards.children[1]!.textContent).toContain("GPU 利用率未采集采样峰值 未记录");
    expect(cards.children[4]!.textContent).toContain("功率（范围未记录）未采集采样峰值 10.0 W");
  });
  it("draws one combined graph and preserves nulls and real gaps", () => {
    const view = render(<TelemetryCard run={run()} />);
    expect(charts.init).toHaveBeenCalledTimes(1);
    expect(view.getByRole("img", { name: "内存与显存占用趋势" })).toBeTruthy();
    const memory = charts.instances[0].setOption.mock.calls[0][0];
    const gpu = charts.instances[0].setOption.mock.calls[0][0];
    expect(memory.series.slice(0, 3).map((s: any) => s.name)).toEqual(["主模型内存", "产品内存", "整机内存"]);
    expect(gpu.series.slice(3).map((s: any) => s.name)).toEqual(["主模型显存", "产品显存", "整机／整卡显存"]);
    expect(memory.yAxis.name).toBe("GiB");
    expect(memory.series[0].lineStyle.type).toBe("solid");
    expect(memory.series[3].lineStyle.type).toBe("dashed");
    expect(new Set(memory.series.map((s: any) => s.itemStyle.color)).size).toBe(6);
    expect(memory.series.every((s: any) => s.itemStyle.color === s.lineStyle.color)).toBe(true);
    expect(memory.xAxis.type).toBe("value");
    expect(memory.series[0].markArea.data[0]).toEqual([expect.objectContaining({ name: "数据导入", xAxis: 0 }), { xAxis: 5 }]);
    expect(memory.series[0].data).toEqual([[0, .25], [1, null], [3, null], [5, .25]]);
    expect(memory.series[0].connectNulls).toBe(false);
    expect(gpu.series[4].data.every((point: any[]) => point[1] === null)).toBe(true);
    expect(view.container.querySelector(".live-telemetry-metrics")?.textContent).not.toMatch(/KV|RSS|PSS|nvmap/);
  });
  it("shows only device GPU reference for Mac without filling model or product values", () => {
    const value = run();
    value.telemetry_live!.source = "apple_unified";
    value.telemetry_live!.latest!.model_process_gpu_memory_mib = null;
    const view = render(<TelemetryCard run={value} />);
    const gpu = charts.instances[0].setOption.mock.calls[0][0];
    expect(gpu.series.map((s: any) => s.name)).toEqual(["主模型内存", "产品内存", "整机内存", "整机显存（参考）"]);
    expect(gpu.series[3].data[0]).toEqual([0, 2]);
    const cards = view.container.querySelector(".live-telemetry-metrics")!;
    expect(cards.children.length).toBe(4);
    expect(cards.textContent).not.toContain("主模型显存");
    expect(cards.textContent).not.toContain("产品显存");
    expect(cards.textContent).toContain("整机显存（参考）");
  });
  it("keeps instances across identical polling and disposes it on unmount", () => {
    const view = render(<TelemetryCard run={run()} />);
    view.rerender(<TelemetryCard run={run()} />);
    expect(charts.init).toHaveBeenCalledTimes(1);
    charts.instances.forEach(instance => expect(instance.setOption).toHaveBeenCalledTimes(1));
    view.unmount();
    charts.instances.forEach(instance => expect(instance.dispose).toHaveBeenCalledTimes(1));
  });
  it("expands three separate graphs for utilization, temperature and power", () => {
    const view = render(<TelemetryCard run={run()} />);
    const details = view.getByText("利用率、温度与功率").closest("details")!;
    expect(details.open).toBe(false);
    details.open = true;
    fireEvent(details, new Event("toggle"));
    expect(charts.init).toHaveBeenCalledTimes(4);
    expect(charts.instances.slice(1).map(instance => instance.setOption.mock.calls[0][0].yAxis.name)).toEqual(["%", "°C", "W"]);
    expect(view.container.textContent).toContain("Mac 系统功率估计");
  });
  it("does not invent gaps between sparse downsampled points", () => {
    const value = run();
    value.telemetry_live!.samples_count = 30;
    render(<TelemetryCard run={value} />);
    expect(charts.instances[0].setOption.mock.calls[0][0].series[2].data).toEqual([[0, 1], [1, 1], [5, 1]]);
  });
  it("fits the last sample and isolates a hovered series without changing legend selection", () => {
    render(<TelemetryCard run={run()} />);
    const graph = charts.instances[0].setOption.mock.calls[0][0];
    expect(graph.xAxis.max).toBe(5);
    expect(graph.xAxis.boundaryGap).toEqual([0, 0]);
    expect(graph.legend.selected).toBeUndefined();
    expect(graph.series.every((series: any) => series.emphasis.focus === "series" && series.blur.lineStyle.opacity === 0)).toBe(true);
    expect(graph.series[0].markArea.data[0][0].itemStyle.color).not.toBe(graph.series[0].markArea.data[1][0].itemStyle.color);
  });
  it("switches between metric colors and non-overlapping phase colors while preserving curve data", () => {
    const view = render(<TelemetryCard run={run()} />);
    const instance = charts.instances[0];
    const original = instance.setOption.mock.calls[0][0];
    fireEvent.click(view.getByRole("checkbox", { name: "曲线按阶段着色" }));
    const colored = instance.setOption.mock.calls[1][0];
    expect(colored.visualMap[0].pieces).toEqual([
      { gte: 0, lt: 5, color: "#2563eb" }, { gte: 5, lte: 5, color: "#15803d" },
    ]);
    expect(colored.visualMap[0].seriesIndex).toEqual([0, 1, 2, 3, 4, 5]);
    expect(colored.series[0].lineStyle.color).toBeUndefined();
    expect(colored.series[0].data).toEqual(original.series[0].data);
    fireEvent.click(view.getByRole("checkbox", { name: "曲线按阶段着色" }));
    expect(instance.setOption.mock.calls[2][0].visualMap).toEqual([]);
    expect(instance.setOption.mock.calls[2][1]).toEqual({ replaceMerge: ["series", "visualMap"] });
    expect(charts.init).toHaveBeenCalledTimes(1);
  });
});

it("uses full-sample percentiles for sparse charts and preserves zero and missing statistics", () => {
  const value = run();
  value.telemetry_live!.samples_count = 1000;
  value.telemetry_live!.field_stats = {
    model_process_system_memory_used_mib: { peak: 4096, p50: 1024, p95: 3072 },
    cpu_utilization_pct: { peak: 90, p50: 0, p95: 75 },
  };
  const view = render(<TelemetryCard run={value} />);
  const memory = view.container.querySelector(".live-telemetry-metrics")!;
  expect(memory.children[0]!.textContent).toContain("采样峰值 4.00 GiBP50 1.00 GiBP95 3.00 GiB");
  expect(memory.children[1]!.textContent).toContain("采样峰值 未记录P50 未记录P95 未记录");
  expect(view.getByLabelText("利用率、温度与功率指标").children[0]!.textContent)
    .toContain("采样峰值 90.0 %P50 0.0 %P95 75.0 %");
});

it("redraws a fresh chart when development StrictMode replays effects", () => {
  render(<StrictMode><TelemetryCard run={run()} /></StrictMode>);
  expect(charts.instances).toHaveLength(2);
  expect(charts.instances[0].dispose).toHaveBeenCalledOnce();
  expect(charts.instances[1].setOption).toHaveBeenCalledOnce();
});
