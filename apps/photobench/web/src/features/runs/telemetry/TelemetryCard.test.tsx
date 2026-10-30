import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Run } from "../../../api/types.ts";

import { TelemetryCard } from "./TelemetryCard.tsx";

// 夹具口径：`telemetry_live.history` 有 3 个采样点（`telemetryChart` 要 ≥2 才给数据）。
// 1.0 参考 `App.vue:1027-1125`（renderTelemetryChart）+ `:2824-2834`（toggle / watch）。
//
// jsdom 画不出真图，所以只断言命令式生命周期：init 次数 / setOption / resize / dispose。
const echartsMock = vi.hoisted(() => {
  const instance = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() };
  return { instance, init: vi.fn(() => instance) };
});

vi.mock("echarts", () => ({ init: echartsMock.init }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  echartsMock.init.mockClear();
  echartsMock.instance.setOption.mockClear();
  echartsMock.instance.resize.mockClear();
  echartsMock.instance.dispose.mockClear();
});

function run(overrides: Partial<Run> = {}): Run {
  return {
    run_id: "r1",
    status: "running",
    current_phase: "qa_eval",
    telemetry_live: {
      source: "nvidia",
      status: "running",
      samples_count: 3,
      history: [
        { t: 0, phase: "qa_eval", memory_used_mib: 1024, system_memory_used_mib: 2048, model_process_memory_used_mib: 512, model_process_system_memory_used_mib: 256 },
        { t: 60, phase: "qa_eval", memory_used_mib: 2048, system_memory_used_mib: 2560, model_process_memory_used_mib: 640, model_process_system_memory_used_mib: 320 },
        { t: 120, phase: "qa_eval", memory_used_mib: 3072, system_memory_used_mib: 2560, model_process_memory_used_mib: 768, model_process_system_memory_used_mib: 384 },
      ],
    },
    ...overrides,
  } as Run;
}

/** 没有任何采样点的 run：`telemetrySampleCount` 为 0，整张卡不渲染。 */
function emptyRun(): Run {
  return { run_id: "r0", status: "done" } as Run;
}

describe("TelemetryCard · echarts 生命周期（1.0 :1027-1125 / :2824-2837）", () => {
  it("检查点窗口不能标成完整测评过程，明确曲线点数和时间起点", () => {
    const initial = run();
    const { container } = render(<TelemetryCard run={run({
      history_mode: "checkpoint",
      telemetry_live: { ...initial.telemetry_live, samples_count: 1011 },
    })} />);
    const caption = container.querySelector(".telemetry-chart small")?.textContent;
    expect(caption).toContain("最近保存的遥测窗口");
    expect(caption).toContain("当前绘制 3 个点");
    expect(caption).toContain("整轮已采集 1011 个采样点");
    expect(caption).toContain("横轴从该窗口首点开始计时");
    expect(caption).not.toMatch(/完整测评过程|全时段抽稀/);
  });

  it("显式加载整轮后才标全时段，分开总采样数与绘图点数", () => {
    const initial = run();
    const { container } = render(<TelemetryCard run={run({
      history_mode: "full",
      telemetry_live: { ...initial.telemetry_live, samples_count: 1011 },
    })} />);
    const caption = container.querySelector(".telemetry-chart small")?.textContent;
    expect(caption).toContain("完整测评过程，共 1011 个采样点");
    expect(caption).toContain("曲线按全时段抽稀为 3 个点");
  });

  it("旧记录未标曲线范围时不推断成全轮", () => {
    const { container } = render(<TelemetryCard run={run()} />);
    const caption = container.querySelector(".telemetry-chart small")?.textContent;
    expect(caption).toContain("曲线范围未记录");
    expect(caption).not.toMatch(/完整测评过程|全时段抽稀/);
  });

  it("阶段快照回退不是完整采样曲线，即使读取模式为 full", () => {
    const { container } = render(<TelemetryCard run={run({
      history_mode: "full",
      telemetry_live: { samples_count: 10, history: [], phase_snapshots: {
        photo_import: { latest: { system_memory_used_mib: 2048 } },
        qa_eval: { latest: { system_memory_used_mib: 3072 } },
      } },
    })} />);
    const caption = container.querySelector(".telemetry-chart small")?.textContent;
    expect(caption).toContain("阶段快照示意");
    expect(caption).not.toMatch(/完整测评过程|全时段抽稀/);
    expect(echartsMock.instance.setOption.mock.calls[0]?.[0].xAxis.data).toEqual(["#1", "#2"]);
  });

  it("缺失内存保持曲线缺口，不转零或连接缺口", () => {
    const view = render(<TelemetryCard run={run({ telemetry_live: {
      source: "host_nvidia_smi", samples_count: 3,
      history: [{ t: 0, system_memory_used_mib: 1024, memory_complete: true },
        { t: 1, system_memory_used_mib: null, memory_complete: false },
        { t: 2, system_memory_used_mib: 2048, memory_complete: true }],
    } })} />);
    const option = echartsMock.instance.setOption.mock.calls[0]?.[0] as {
      series: Array<{ name: string; data: Array<number | null>; connectNulls: boolean }>;
    };
    const memory = option.series.find((row) => row.name === "整机 RAM");
    expect(memory?.data).toEqual([1, null, 2]);
    expect(memory?.connectNulls).toBe(false);
    expect(view.container.textContent).toContain("当前绘图点中有 1/3 点核心内存采集不完整");
  });
  it("挂载建一次实例；run 变化只重画，不再 init", () => {
    const view = render(<TelemetryCard run={run()} />);

    expect(echartsMock.init).toHaveBeenCalledTimes(1);
    expect(echartsMock.instance.setOption).toHaveBeenCalled();
    // 1.0 `:1124`：setOption 之后立刻 resize 一次。
    expect(echartsMock.instance.resize).toHaveBeenCalled();

    // 换一个 run 对象（内容不同）→ 组件重渲染，实例必须复用而不是重建。
    view.rerender(<TelemetryCard run={run({ status: "completed" })} />);
    view.rerender(<TelemetryCard run={run({ status: "failed" })} />);
    expect(echartsMock.init).toHaveBeenCalledTimes(1);
    // 只是状态变了、历史采样没变：不 setOption，避免把 tooltip 重置掉。
    const afterStatus = echartsMock.instance.setOption.mock.calls.length;

    // 历史采样点变化 → 必须重画（1.0 `:2834` 的 deep watch）。
    const before = run();
    const next = run({
      telemetry_live: {
        ...before.telemetry_live,
        samples_count: 4,
        history: [...(before.telemetry_live?.history || []), { t: 180, phase: "qa_eval", memory_used_mib: 3200 }],
      },
    });
    view.rerender(<TelemetryCard run={next} />);
    expect(echartsMock.instance.setOption.mock.calls.length).toBeGreaterThan(afterStatus);
  });

  it("卸载时 dispose 实例（1.0 :2837）", () => {
    const view = render(<TelemetryCard run={run()} />);
    expect(echartsMock.instance.dispose).not.toHaveBeenCalled();

    view.unmount();
    expect(echartsMock.instance.dispose).toHaveBeenCalledTimes(1);
  });

  it("时间和点数相同，修正缺失读数后也重画", () => {
    const initial = run();
    const view = render(<TelemetryCard run={initial} />);
    const before = echartsMock.instance.setOption.mock.calls.length;
    view.rerender(<TelemetryCard run={run({ telemetry_live: {
      ...initial.telemetry_live,
      history: initial.telemetry_live!.history!.map((point, index) => index === 1
        ? { ...point, system_memory_used_mib: null } : point),
    } })} />);
    expect(echartsMock.instance.setOption.mock.calls.length).toBeGreaterThan(before);
  });

  it("没有采样点时整张卡不渲染，也不 init", () => {
    const { container } = render(<TelemetryCard run={emptyRun()} />);
    expect(container.querySelector(".live-telemetry-card")).toBeNull();
    expect(echartsMock.init).not.toHaveBeenCalled();
  });

  it("展开时 resize、折叠时不 resize（1.0 :2824-2830 的 0×0 容器坑）", () => {
    const { container } = render(<TelemetryCard run={run()} />);
    const details = container.querySelector("details.live-telemetry-card") as HTMLDetailsElement;
    expect(details).not.toBeNull();

    echartsMock.instance.resize.mockClear();

    details.open = false;
    fireEvent(details, new Event("toggle"));
    expect(echartsMock.instance.resize).not.toHaveBeenCalled();

    details.open = true;
    fireEvent(details, new Event("toggle"));
    expect(echartsMock.instance.resize).toHaveBeenCalledTimes(1);
  });

  it("容器尺寸变化时 resize（ResizeObserver）", () => {
    let notify: (() => void) | null = null;
    class FakeResizeObserver {
      constructor(callback: () => void) {
        notify = callback;
      }
      observe = vi.fn();
      disconnect = vi.fn();
    }
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);

    render(<TelemetryCard run={run()} />);
    expect(notify).not.toBeNull();

    echartsMock.instance.resize.mockClear();
    (notify as unknown as () => void)();
    expect(echartsMock.instance.resize).toHaveBeenCalledTimes(1);
  });

  it("option 按数据源选线：NVIDIA 源画整卡显存 / 主模型显存，且 tooltip 与 markArea 就位", () => {
    render(<TelemetryCard run={run()} />);
    const option = echartsMock.instance.setOption.mock.calls[0]?.[0] as {
      series: Array<{ name: string; data: Array<number | null> }>;
      xAxis: { data: string[] };
    };

    expect(option.series.map((series) => series.name)).toEqual([
      "整卡 GPU 显存",
      "整机 RAM",
      "主模型 GPU 显存（nvidia-smi 按 PID）",
      "主模型 RAM（PSS）",
    ]);
    // MiB → GiB：1024/2048/3072 MiB → 1/2/3 GiB。
    expect(option.series[0]?.data).toEqual([1, 2, 3]);
    // 首点 t=0 为基准 → +0.0 / +1.0 / +2.0 min。
    expect(option.xAxis.data).toEqual(["+0.0 min", "+1.0 min", "+2.0 min"]);
  });

  it("Mac 统一内存源画另外四条线（1.0 :1041-1046）", () => {
    const macRun = run({
      telemetry_live: {
        source: "apple_unified",
        status: "running",
        samples_count: 2,
        history: [
          { t: 0, system_memory_used_mib: 8192, product_stack_memory_mib: 4096, sentrix_stack_pss_mib: 2048, model_process_system_memory_used_mib: 3072 },
          { t: 60, system_memory_used_mib: 8192, product_stack_memory_mib: 4096, sentrix_stack_pss_mib: 2048, model_process_system_memory_used_mib: 3072 },
        ],
      },
    });
    render(<TelemetryCard run={macRun} />);
    const option = echartsMock.instance.setOption.mock.calls[0]?.[0] as { series: Array<{ name: string }> };
    expect(option.series.map((series) => series.name)).toEqual([
      "整机内存（active+wired+compressor，平台特定）",
      "整套产品内存（相关进程 footprint+权重）",
      "Sentrix 周边（不含主模型，footprint+权重）",
      "主模型进程占用（footprint+权重映射）",
    ]);
  });

  it("Mac resident v2 draws three totals and ignores legacy/diagnostic fields", () => {
    render(<TelemetryCard run={run({ telemetry_live: {
      source: "apple_unified", samples_count: 2,
      latest: { memory_accounting: "apple_footprint_resident_v2" },
      history: [{ t: 1, system_memory_used_mib: 10000, product_stack_memory_mib: 5000,
        model_process_system_memory_used_mib: 3000, sentrix_stack_pss_mib: 2000,
        gpu_in_use_unified_memory_mib: 4000 }, { t: 2, system_memory_used_mib: 10000, product_stack_memory_mib: 5000, model_process_system_memory_used_mib: 3000 }],
    } })} />);
    const option = echartsMock.instance.setOption.mock.calls[0]?.[0] as { series: Array<{ name: string }> };
    expect(option.series.map(series => series.name)).toEqual([
      "整机已占内存（含缓存）", "整套产品驻留内存（估计）", "主模型驻留内存（估计）",
    ]);
  });
});

it("retains replay-only historical memory values inside the telemetry section", () => {
  const value = { run_id: "old-replay", status: "completed", memory_profile: {
    status: "completed", memory_profile: { method: "macos_unified_memory_v1", memory_used_peak_gib: 12.5 },
  } } as Run;
  const view = render(<TelemetryCard run={value} />);
  expect(view.getByText("历史采样汇总")).toBeTruthy();
  expect(view.getByText("12.50 GiB")).toBeTruthy();
  expect(echartsMock.init).not.toHaveBeenCalled();
});
