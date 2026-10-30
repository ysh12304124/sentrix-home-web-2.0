import { describe, expect, it } from "vitest";

describe("resident accounting v2", () => {
  it("shows only three memory totals and keeps legacy capacity labels", () => {
    const latest = { memory_accounting: "apple_footprint_resident_v2", system_memory_used_mib: 15000,
      product_stack_memory_mib: 9000, model_process_system_memory_used_mib: 7000 };
    const rows = liveTelemetryRows({ telemetry_live: { source: "apple_unified", latest } });
    expect(rows.filter(row => /内存/.test(row[0]))).toHaveLength(3);
    expect(rows.some(row => /footprint\+权重/.test(row[0]))).toBe(false);
    const phase = gpuMetricRows({ ...latest, system_memory_used_mib: { peak: 15000 } });
    expect(phase[0][0]).toBe("整机已占内存（含缓存）");
    const old = liveTelemetryRows({ telemetry_live: { source: "apple_unified",
      latest: { memory_accounting: "apple_capacity_estimate" } } });
    expect(old.some(row => /footprint\+权重/.test(row[0]))).toBe(true);
  });
  it("labels Orin system formula by recorded scope", () => {
    const run = { telemetry_live: { source: "jetson_local_pss", latest: {
      memory_accounting: "jetson_pss_plus_nvmap_estimate",
      system_memory_scope: "linux_physical_nonfree_including_cache_v2" } } };
    expect(liveTelemetryRows(run).find(row => row[0] === "整机 RAM")?.[2]).toContain("MemFree");
  });
});

import {
  aggregateMetricRows,
  comparableMemoryProfile,
  gpuMetricRows,
  gpuMetricsView,
  itemCallMetrics,
  jetsonUnifiedFromRun,
  keyMetricRows,
  liveTelemetryPhaseRows,
  liveTelemetryRows,
  memoryProfileRows,
  recallMetricRows,
  telemetryChart,
  telemetryHistory,
  telemetryLiveState,
  telemetryProcessList,
  telemetrySampleCount,
  telemetryObservationRows,
  tokenDistributionCount,
  tokenDistributionRows,
} from "./telemetry.ts";

// 夹具要点：让「按历史采样聚合」与「按 live.latest/peak 回退」两种口径给出不同答案；
// 让缺字段的几种回退（`-` / `0` / `未提供` / `未标注范围`）互不相同。

describe("gpuMetricsView (:857-935)", () => {
  it("降采样曲线不能覆盖完整样本统计，空值不进入均值", () => {
    const view = gpuMetricsView({
      status: "running",
      telemetry_live: {
        samples_count: 500,
        history: [{ gpu_utilization_pct: 1 }, { gpu_utilization_pct: null }],
        field_stats: { gpu_utilization_pct: { peak: 80, mean: 35, p95: 65, valid_count: 400 } },
      },
    });
    expect(view.gpu_utilization_pct).toEqual({ peak: 80, mean: 35, p95: 65, valid_count: 400 });
    const sparse = gpuMetricsView({ status: "running", telemetry_live: {
      samples_count: 3, history: [{ gpu_utilization_pct: null }, { gpu_utilization_pct: 0 }, { gpu_utilization_pct: 20 }],
    } });
    expect(sparse.gpu_utilization_pct).toEqual({ peak: 20, mean: 10, p50: 20, p95: 20 });
  });
  it("有 ≥20 个历史采样时：peak=max、mean=算术平均、p95=floor(n*0.95) 位（不是 max）", () => {
    const history = Array.from({ length: 21 }, (_, i) => ({ gpu_utilization_pct: i + 1 }));
    const run = {
      status: "running",
      phases: { gpu_metrics: { source: "nvidia_smi", samples_count: 21, gpu_utilization_pct: {} } },
      telemetry_live: { samples_count: 21, history },
    };
    const view = gpuMetricsView(run);
    // 1..21：mean=11，peak=21，sorted[floor(21*0.95)]=sorted[19]=20
    expect(view.gpu_utilization_pct).toEqual({ peak: 21, mean: 11, p50: 11, p95: 20 });
  });

  it("采样数 <20 时 p95 退化为 max（与 ≥20 口径不同）", () => {
    const history = [{ gpu_utilization_pct: 10 }, { gpu_utilization_pct: 20 }, { gpu_utilization_pct: 30 }];
    const run = {
      status: "running",
      phases: { gpu_metrics: { source: "nvidia_smi", samples_count: 3, gpu_utilization_pct: {} } },
      telemetry_live: { samples_count: 3, history, latest: { gpu_utilization_pct: 99 }, peak: { gpu_utilization_pct: 100 } },
    };
    const view = gpuMetricsView(run);
    // 有历史就用历史，不理会 latest=99：mean=20
    expect(view.gpu_utilization_pct).toEqual({ peak: 30, mean: 20, p50: 20, p95: 30 });
  });

  it("缺少完整历史时不伪造均值和 P95", () => {
    const run = {
      status: "running",
      telemetry_source: "nvidia_smi",
      telemetry_live: { samples_count: 5, latest: { gpu_utilization_pct: 5 }, peak: { gpu_utilization_pct: 9 } },
    };
    const view = gpuMetricsView(run);
    expect(view.gpu_utilization_pct).toEqual({});
    expect(view.samples_count).toBe(5);
    expect(view.source).toBe("nvidia_smi");
  });

  it("相位里已有的聚合（带 peak）不被历史覆盖", () => {
    const run = {
      status: "running",
      phases: { gpu_metrics: { samples_count: 3, gpu_utilization_pct: { peak: 7, mean: 3, p95: 6 } } },
      telemetry_live: { samples_count: 3, history: [{ gpu_utilization_pct: 1 }, { gpu_utilization_pct: 2 }, { gpu_utilization_pct: 3 }] },
    };
    expect(gpuMetricsView(run).gpu_utilization_pct).toEqual({ peak: 7, mean: 3, p95: 6 });
  });

  it("done 相位保留原值，不重算", () => {
    const run = { status: "completed", phases: { gpu_metrics: { status: "done", samples_count: 3, temperature_c: { peak: 55 } } } };
    const view = gpuMetricsView(run);
    expect(view.partial).toBe(false);
    expect(view.temperature_c).toEqual({ peak: 55 });
  });
});

describe("liveTelemetryRows (:941-991)", () => {
  it("v2 metadata controls live formulas and power scope despite a legacy source label", () => {
    const rows = liveTelemetryRows({ status: "running", telemetry_live: {
      source: "nvidia_smi", latest: { memory_accounting: "jetson_pss_plus_nvmap_estimate",
        model_process_system_memory_used_mib: null, model_process_host_memory_mib: 100,
        model_process_gpu_memory_mib: null, product_stack_memory_mib: null,
        power_draw_w: 4, power_scope: "rail", power_rail: "VDD_GPU_SOC" },
    } });
    const byLabel = new Map(rows.map((row) => [row[0], row]));
    expect(byLabel.get("主模型占用（smaps RSS + nvmap）")?.[1]).toBe("-");
    expect(byLabel.get("主模型 smaps RSS")?.[1]).toBe("0.10 GiB");
    expect(byLabel.get("主模型 GPU/UMA 分量")?.[1]).toBe("-");
    expect(byLabel.get("整套产品内存")?.[1]).toBe("-");
    expect(byLabel.get("电源轨功耗")?.[2]).toContain("VDD_GPU_SOC");
    expect(rows.some((row) => row[0].includes("GPU 显存"))).toBe(false);
    const apple = liveTelemetryRows({ telemetry_live: { latest: { memory_accounting: "apple_capacity_estimate" } } });
    expect(apple.find((row) => row[0] === "整机内存")?.[2]).toContain("active + wired + compressor");
    expect(apple.find((row) => row[0] === "整套产品内存")?.[2]).toContain("容量估计");
  });
  it("非 apple：缺字段回 `-`、`0` 保留为 `0.00%`、KV 缺字段各有专门文案", () => {
    const run = {
      status: "running",
      current_phase: "qa_eval",
      telemetry_live: {
        source: "nvidia_smi",
        samples_count: 4,
        latest: { model_process_memory_used_mib: 2048, gpu_utilization_pct: 0 },
        peak: {},
      },
    };
    const rows = liveTelemetryRows(run);
    const byLabel = new Map(rows.map((row) => [row[0], row]));
    expect(byLabel.get("当前阶段")).toEqual(["当前阶段", "QA 评测", "已采样 4 次"]);
    expect(byLabel.get("主模型 GPU 显存")?.[1]).toBe("2.00 GiB");
    expect(byLabel.get("主模型 RAM")?.[1]).toBe("-");
    // 0 不是缺字段：回 "0.00%"，不是 "-"
    expect(byLabel.get("GPU 利用率")?.[1]).toBe("0.00%");
    // 峰值在 peak 为空时回退到 latest（两处口径一致）
    expect(byLabel.get("GPU 利用率")?.[2]).toBe("峰值 0.00%");
    expect(byLabel.get("KV Cache")?.[1]).toBe("未提供");
    expect(byLabel.get("KV Cache")?.[2]).toBe("当前框架未暴露运行时 KV 指标");
    // system_memory_scope 缺失 -> 未标注范围（不是 宿主机全部进程）
    expect(byLabel.get("整机 RAM")?.[2]).toContain("未标注范围");
    expect(byLabel.get("GPU 温度")?.[1]).toBe("-");
  });

  it("peak 非空时生效；整机 RAM scope 命中回 宿主机全部进程；KV 有值时给数值", () => {
    const run = {
      status: "completed",
      telemetry_live: {
        source: "nvidia_smi",
        samples_count: 9,
        latest: { model_process_memory_used_mib: 1024, kv_cache_usage_pct: 50, kv_cache_used_tokens: 128 },
        peak: { model_process_memory_used_mib: 4096, kv_cache_used_tokens: 256, system_memory_used_mib: 8192 },
      },
    };
    const rows = liveTelemetryRows(run);
    const byLabel = new Map(rows.map((row) => [row[0], row]));
    expect(byLabel.get("当前阶段")).toEqual(["当前阶段", "完成", "已采样 9 次"]);
    expect(byLabel.get("主模型 GPU 显存")).toEqual(["主模型 GPU 显存", "1.00 GiB", "峰值 4.00 GiB · nvidia-smi 按主模型 PID 归因"]);
    expect(byLabel.get("KV Cache")?.[1]).toBe("50.00%");
    expect(byLabel.get("KV Cache")?.[2]).toBe("峰值 token 256.00");
  });

  it("apple_unified 走另一套行；PSS source 改标签", () => {
    const apple = liveTelemetryRows({ status: "running", telemetry_live: { source: "apple_unified", samples_count: 2, latest: { gpu_utilization_pct: 12 }, peak: {} } });
    expect(apple[0][0]).toBe("当前阶段");
    expect(apple.some((row) => row[0] === "主模型占用（footprint+权重）")).toBe(true);
    const orin = liveTelemetryRows({ status: "running", telemetry_live: { source: "orin_ssh_pss", samples_count: 1, latest: {}, peak: {} } });
    expect(orin.some((row) => row[0] === "主模型 PSS（Orin UMA）")).toBe(true);
  });
});

describe("telemetryLiveState / telemetryHistory / 计数 (:992-1008)", () => {
  it("非对象/数组当空；history 非数组当空", () => {
    expect(telemetryLiveState({ telemetry_live: [] })).toEqual({});
    expect(telemetryLiveState(undefined)).toEqual({});
    expect(telemetryHistory({ telemetry_live: { history: "x" } })).toEqual([]);
    expect(telemetryHistory({ telemetry_live: { history: [{ a: 1 }] } })).toEqual([{ a: 1 }]);
    expect(telemetryProcessList({ telemetry_live: { all_processes: [{ pid: 1 }] } })).toEqual([{ pid: 1 }]);
    expect(telemetryProcessList({})).toEqual([]);
  });
  it("sampleCount 取 samples_count 与历史长度的较大者", () => {
    expect(telemetrySampleCount({ telemetry_live: { samples_count: 2, history: [1, 2, 3] } })).toBe(3);
    expect(telemetrySampleCount({ telemetry_live: { samples_count: 7, history: [1] } })).toBe(7);
    expect(telemetrySampleCount({ telemetry_live: { samples_count: -4 } })).toBe(0);
  });
});

describe("telemetryChart (:1011-1026)", () => {
  it("历史 >=2 直接返回 history", () => {
    const history = [{ t: 0, phase: "a", memory_used_mib: 1 }, { t: 60, phase: "b", memory_used_mib: 2 }];
    expect(telemetryChart({ telemetry_live: { history } })).toEqual({ history });
  });

  it("历史 <2 时用 phase_snapshots 拼相位趋势，并只保留含 _mib 的快照", () => {
    const run = {
      telemetry_live: {
        phase_snapshots: {
          model_deploy: { latest: { model_process_memory_used_mib: 1024 } },
          qa_eval: { peak: { system_memory_used_mib: 2048 } },
          empty_phase: { latest: { gpu_utilization_pct: 5 } },
        },
      },
    };
    const data = telemetryChart(run);
    expect(data?.history).toHaveLength(2);
    expect(data?.history[0]).toMatchObject({ t: 0, phase: "model_deploy", model_process_memory_used_mib: 1024 });
    expect(data?.history[1]).toMatchObject({ t: 1, phase: "qa_eval", system_memory_used_mib: 2048 });
  });

  it("两条路都凑不足 2 点回 null", () => {
    expect(telemetryChart({ telemetry_live: { history: [{ t: 0, memory_used_mib: 1 }] } })).toBeNull();
    expect(telemetryChart({ telemetry_live: { phase_snapshots: { a: { latest: { gpu_utilization_pct: 5 } } } } })).toBeNull();
  });
});

describe("liveTelemetryPhaseRows (:1126-1150)", () => {
  it("unknown phase peaks stay unavailable while observed zero remains zero", () => {
    const run = { telemetry_live: {
      source: "apple_unified", latest: { memory_accounting: "apple_capacity_estimate" },
      phase_snapshots: { photo_import: { peak: {} }, qa_eval: { peak: {} } },
      history: [
        { phase: "photo_import", model_process_system_memory_used_mib: null },
        { phase: "photo_import", model_process_system_memory_used_mib: false },
        { phase: "photo_import", model_process_system_memory_used_mib: "" },
        { phase: "qa_eval", model_process_system_memory_used_mib: 0 },
      ],
    } };
    const rows = liveTelemetryPhaseRows(run);
    expect(rows[0][1]).toBe("-");
    expect(rows[0][2]).toContain("主模型占用（footprint+权重）");
    expect(rows[1][1]).toBe("0.00 GiB");
    expect(liveTelemetryPhaseRows({ telemetry_live: { ...run.telemetry_live, source: "nvidia_smi" } })).toEqual(rows);
  });

  it("downsampled phase history cannot replace a missing saved peak", () => {
    const rows = liveTelemetryPhaseRows({ telemetry_live: { source: "nvidia_smi", samples_count: 20,
      history: [{ phase: "qa_eval", model_process_memory_used_mib: 1024 }],
      phase_snapshots: { qa_eval: { peak: { model_process_memory_used_mib: false, memory_used_mib: "", system_memory_used_mib: false } } },
    } });
    expect(rows[0][1]).toBe("-");
    expect(rows[0][2]).toContain("整卡 - · 整机 RAM -");
  });
  it("按 source 选模型指标键与标签；peak 缺失时从历史取该相位峰值", () => {
    const run = {
      telemetry_source: "nvidia_smi",
      telemetry_live: {
        phase_snapshots: {
          qa_eval: { peak: { model_process_memory_used_mib: 2048, memory_used_mib: 4096, system_memory_used_mib: 8192 }, samples_count: 3 },
          photo_import: { peak: {}, samples_count: 1 },
        },
        history: [
          { phase: "photo_import", model_process_memory_used_mib: 512 },
          { phase: "photo_import", model_process_memory_used_mib: 1024 },
        ],
      },
    };
    const rows = liveTelemetryPhaseRows(run);
    const byLabel = new Map(rows.map((row) => [row[0], row]));
    expect(byLabel.get("QA 评测")).toEqual(["QA 评测", "2.00 GiB", "主模型 GPU峰值 · 整卡 4.00 GiB · 整机 RAM 8.00 GiB · 3 次"]);
    // photo_import 的 peak 没有模型键 -> 从历史取最大值 1024
    expect(byLabel.get("照片/视频导入")?.[1]).toBe("1.00 GiB");
  });

  it("apple_unified 用 phys_footprint 键与标签，且被算进 orin 分支", () => {
    const run = {
      telemetry_source: "apple_unified",
      telemetry_live: { phase_snapshots: { qa_eval: { peak: { model_process_system_memory_used_mib: 3072 }, samples_count: 2 } } },
    };
    const rows = liveTelemetryPhaseRows(run);
    expect(rows[0][0]).toBe("QA 评测");
    expect(rows[0][1]).toBe("3.00 GiB");
    expect(rows[0][2]).toContain("主模型占用（footprint+权重）峰值");
  });
});

describe("comparableMemoryProfile (:1151-1165) —— 可比判据", () => {
  it("null -> null；replay/pending/gpu_metrics 三档互不相同", () => {
    expect(comparableMemoryProfile(null)).toBeNull();

    const replay = comparableMemoryProfile({ memory_profile: { method: "vllm_reserved_v1" } });
    expect(replay?.source).toBe("replay");
    // 1.0 replay 分支用展开算子把画像字段**摊平**（1.0 遗留，照搬）：method 在顶层，
    // 而不是像 gpu_metrics 分支那样嵌在 memory_profile 下。
    expect(replay?.method).toBe("vllm_reserved_v1");
    expect(replay?.memory_profile).toBeUndefined();

    const pendingNoPhase = comparableMemoryProfile({ summary: { completed: 1, total: 5 } });
    expect(pendingNoPhase).toMatchObject({ source: "pending", status: "pending", questions_completed: 1, questions_total: 5 });

    const pendingNoProfile = comparableMemoryProfile({ phases: { gpu_metrics: { status: "running" } } });
    expect(pendingNoProfile).toMatchObject({ source: "pending", status: "running" });
  });

  it("有 gpu_metrics.memory_profile 才判为可比（source=gpu_metrics），否则不可比（pending）", () => {
    const comparable = comparableMemoryProfile({
      summary: { completed: 3, total: 3 },
      phases: { gpu_metrics: { status: "done", memory_profile: { method: "vllm_reserved_v1" }, model_process_memory_used_mib: { peak: 100 } } },
    });
    const notComparable = comparableMemoryProfile({
      summary: { completed: 3, total: 3 },
      phases: { gpu_metrics: { status: "done", model_process_memory_used_mib: { peak: 100 } } },
    });
    expect(comparable?.source).toBe("gpu_metrics");
    expect(comparable?.memory_profile).toEqual({ method: "vllm_reserved_v1" });
    expect(comparable?.model_process_memory_used_mib).toEqual({ peak: 100 });
    expect(notComparable?.source).toBe("pending");
    expect(notComparable?.memory_profile).toEqual({});
  });
});

describe("memoryProfileRows (:1176-1211) —— 三种 method 分道", () => {
  it("新 Jetson 公式说明与归属估计一致，历史记录不套现行公式", () => {
    const run = { phases: { gpu_metrics: {
      source: "jetson_local_pss", memory_accounting: "jetson_pss_plus_nvmap_estimate",
      model_process_system_memory_used_mib: { peak: 1024 },
      memory_profile: { method: "orin_smaps_rss_plus_gpu_uma_v1", scope: "host_smaps_rss_plus_gpu_uma" },
    } } };
    const profile = comparableMemoryProfile(run);
    expect(profile?.memory_accounting).toBe("jetson_pss_plus_nvmap_estimate");
    const rows = memoryProfileRows(profile);
    const notes = rows.map((row) => row[2]).join(" ");
    expect(notes).toContain("优先按 PID 汇总 nvmap");
    expect(notes).toContain("未知分量使合计不可用");
    expect(notes).toContain("不保证物理去重");
    expect(notes).not.toContain("max(");
    const metricNote = keyMetricRows(run, {}).find((row) => row.label === "主模型内存占用")?.note;
    expect(metricNote).toContain("优先 nvmap");
    expect(metricNote).toContain("不保证物理去重");
    expect(metricNote).not.toContain("两半不重叠");
    const historical = memoryProfileRows({ ...profile, memory_accounting: undefined });
    expect(historical.find((row) => row[0] === "数据来源")?.[2]).toContain("历史 GPU 归属公式");
    expect(historical.find((row) => row[0] === "数据来源")?.[2]).not.toContain("优先");
  });

  it("Mac 主指标将 footprint 加映射权重标为容量估计", () => {
    const rows = keyMetricRows({ phases: { gpu_metrics: {
      source: "apple_unified", memory_accounting: "apple_capacity_estimate",
      model_process_system_memory_used_mib: { peak: 1024 },
    } } }, {});
    const note = rows.find((row) => row.label === "主模型内存占用")?.note;
    expect(note).toContain("容量估计");
    expect(note).toContain("映射不保证驻留");
    expect(note).not.toContain("进程提交占用");
  });

  it("macos / orin / 默认(vLLM) 首行各不相同", () => {
    expect(memoryProfileRows({ memory_profile: { method: "macos_unified_memory_v1" } })[0][0]).toBe("内存占用峰值");
    expect(memoryProfileRows({ memory_profile: { method: "orin_process_pss_uma_v1" } })[0][0]).toBe("进程物理内存峰值（PSS）");
    expect(memoryProfileRows({ memory_profile: { method: "vllm_reserved_v1" } })[0][0]).toBe("估算工作负载显存");
  });

  it("orin 的行把 KV 字节数标为不可用", () => {
    const rows = memoryProfileRows({ memory_profile: { method: "orin_process_pss_uma_v1" } });
    const byLabel = new Map(rows.map((row) => [row[0], row]));
    expect(byLabel.get("工作负载内存估算")?.[1]).toBe("不可计算");
    expect(byLabel.get("KV 实际字节数")?.[1]).toBe("不可用");
  });

  it("source=gpu_metrics 时末两行换成「评测采样覆盖 / 数据来源」", () => {
    const gpuRows = memoryProfileRows({ source: "gpu_metrics", questions_completed: 2, questions_total: 4, memory_profile: { method: "vllm_reserved_v1" } });
    const labels = gpuRows.map((row) => row[0]);
    expect(labels).toContain("评测采样覆盖");
    expect(labels).toContain("数据来源");

    const replayRows = memoryProfileRows({ source: "replay", memory_profile: { method: "vllm_reserved_v1" } });
    const replayLabels = replayRows.map((row) => row[0]);
    expect(replayLabels).toContain("复测进度");
    expect(replayLabels).toContain("原测评数据一致性");
  });
});

describe("aggregateMetricRows (:1212-1253) / keyMetricRows (:1254-1304)", () => {
  it("retrieval_metric_scope=all_media 换标签，否则回历史图片口径", () => {
    const typed = aggregateMetricRows({}, { retrieval_metric_scope: "all_media" });
    expect(typed[0][0]).toBe("媒体检索 Precision");
    const historical = aggregateMetricRows({}, {});
    expect(historical[0][0]).toBe("历史图片检索 Precision");
    expect(historical.find((r) => r[0] === "视频检索 P / R / F1")?.[1]).toBe("未记录"); // 视频检索 P/R/F1
  });

  it("吞吐时延 note 区分「实测相位」与「时间线回退」", () => {
    const measured = aggregateMetricRows({ qa_concurrency: 4 }, { agent_throughput_latency_mode: "measured_agent_phase", agent_phase_completed_count: 10 });
    expect(measured.find((r) => r[0] === "Agent 并发吞吐折算时延")?.[2]).toContain("实际墙钟");
    const fallback = aggregateMetricRows({}, { agent_throughput_latency_sample_count: 3, agent_throughput_latency_total_count: 10 });
    expect(fallback.find((r) => r[0] === "Agent 并发吞吐折算时延")?.[2]).toContain("不能视为实测");
  });

  it("keyMetricRows 未落盘候选顺序时不补零", () => {
    const rows = keyMetricRows({}, {});
    const recall = rows.find((row) => row.label === "工具图片召回率");
    expect(recall?.value).toBe("未计算");
    expect(recall?.note).toContain("不补零");
    const gpuRow = rows.find((row) => row.label === "主模型显存占用");
    expect(gpuRow?.value).toBe("未记录");
  });
});

describe("recallMetricRows (:1305-1320)", () => {
  it("无 image_ranking.question_count 回空数组", () => {
    expect(recallMetricRows({})).toEqual([]);
    expect(recallMetricRows({ image_ranking: {} })).toEqual([]);
  });
  it("有 question_count 时出 9 行，且缺失 K 值回 未计算", () => {
    const rows = recallMetricRows({ image_ranking: { question_count: 12, r_at_5: 0.5 } });
    expect(rows).toHaveLength(9);
    const byLabel = new Map(rows.map((row) => [row[0], row]));
    expect(byLabel.get("R@5")).toEqual(["R@5", "50.0%", "0 题（GT ≤ 5 张）· 关键指标卡里的主值"]);
    expect(byLabel.get("mAP")?.[1]).toBe("未计算");
    expect(byLabel.get("计分题数")?.[1]).toBe("12 题");
  });
});

describe("tokenDistributionRows (:1321-1334)", () => {
  it("字段缺失回 `-`、0 回 `0 token`、.5 落在四舍五入边界上", () => {
    const rows = tokenDistributionRows({
      llm_prompt_tokens_max: 0,
      llm_prompt_tokens_p95: 1024.5, // Math.round -> 1025
      llm_context_tokens_max: null,
      llm_completion_tokens_max: NaN,
    });
    const byLabel = new Map(rows.map((row) => [row[0], row]));
    expect(byLabel.get("最大输入 token")?.[1]).toBe("0 token");
    expect(byLabel.get("P95 输入 token")?.[1]).toBe("1,025 token");
    expect(byLabel.get("最大总上下文")?.[1]).toBe("-");
    expect(byLabel.get("最大输出 token")?.[1]).toBe("-");
  });
  it("tokenDistributionCount 缺字段回 0（不是 `-`）", () => {
    expect(tokenDistributionCount({ llm_context_samples_count: 7 })).toBe(7);
    expect(tokenDistributionCount({})).toBe(0);
  });
});

describe("itemCallMetrics (:1335-1339)", () => {
  it("只保留对象元素；非数组回空", () => {
    expect(itemCallMetrics({ model_call_metrics: [{ a: 1 }, null, "x", 3, { b: 2 }] })).toEqual([{ a: 1 }, { b: 2 }]);
    expect(itemCallMetrics({ model_call_metrics: "nope" })).toEqual([]);
    expect(itemCallMetrics(undefined)).toEqual([]);
  });
});

describe("gpuMetricRows (:785-856)", () => {
  it("默认 NVIDIA 分支 10 行、主指标行带 true、缺字段留 `-`", () => {
    const rows = gpuMetricRows({ source: "nvidia_smi", samples_count: 3, model_process_memory_used_mib: { mean: 2048, peak: 4096 } });
    expect(rows).toHaveLength(10);
    expect(rows[0][0]).toBe("模型进程显存");
    expect(rows[0][3]).toBe(true);
    expect(rows[0][1]).toBe("2.00 GiB");
    expect(rows[0][2]).toContain("峰值 4.00 GiB");
    // 采不到的项留空，不补 0
    const clockRow = rows.find((row) => row[0] === "SM 时钟");
    expect(clockRow?.[1]).toBe("-");
  });

  it("memory_pressure 分支回另一套行且 thermal 走标签映射", () => {
    const rows = gpuMetricRows({ memory_pressure: { mean: 2 }, thermal_state: { mean: 3, peak: 1 }, samples_count: 9 });
    expect(rows).toHaveLength(10);
    expect(rows[0][0]).toBe("内存压力");
    expect(rows[0][1]).toBe("2");
    const thermal = rows.find((row) => row[0] === "散热状态");
    expect(thermal?.[1]).toBe("critical");
    expect(thermal?.[2]).toContain("峰值 fair");
  });

  it("apple_unified / orin 分支行数与首标签不同", () => {
    const apple = gpuMetricRows({ source: "apple_unified", samples_count: 4, model_process_system_memory_used_mib: { peak: 1024, mean: 512 } });
    expect(apple).toHaveLength(13);
    expect(apple[0][0]).toBe("主模型占用（footprint+权重）");
    const orin = gpuMetricRows({ source: "orin_ssh_pss", samples_count: 4 });
    expect(orin).toHaveLength(9);
    expect(orin[0][0]).toBe("Orin 模型进程占用（VmRSS）");
  });
});

describe("jetsonUnifiedFromRun", () => {
  it("按 memory_profile.scope 判断，不按 source 猜", () => {
    expect(
      jetsonUnifiedFromRun({ phases: { gpu_metrics: { memory_profile: { scope: "host_smaps_rss_plus_gpu_uma" } } } }),
    ).toBe(true);
    expect(jetsonUnifiedFromRun({ memory_profile: { scope: "host_smaps_rss_plus_gpu_uma" } })).toBe(true);
    expect(jetsonUnifiedFromRun({ phases: { gpu_metrics: { memory_profile: { scope: "vmrss_only" } } } })).toBe(false);
    // 本次实测的 46 run：memory_profile 还是旧 PSS，但拆分字段已经落盘。
    expect(
      jetsonUnifiedFromRun({ phases: { gpu_metrics: { model_process_host_memory_mib: { peak: 1670.84 } } } }),
    ).toBe(true);
    expect(jetsonUnifiedFromRun(null)).toBe(false);
  });

  it("同一台 Orin 上新旧两种口径的标签不同——写死 VmRSS 会读错旧 run", () => {
    const legacy = { source: "orin_ssh_pss", samples_count: 1, model_process_system_memory_used_mib: { peak: 1024, mean: 512 } };
    expect(gpuMetricRows(legacy)[0][0]).toBe("Orin 模型进程占用（VmRSS）");
    const unified = { ...legacy, memory_profile: { scope: "host_smaps_rss_plus_gpu_uma" } };
    expect(gpuMetricRows(unified)[0][0]).toBe("主模型占用（smaps Rss + GPU）");
  });
});


describe("telemetryObservationRows", () => {
  it("显示真实来源值龄、过期来源及记录失败，不把未知耗时转零", () => {
    const rows = telemetryObservationRows({
      phases: { gpu_metrics: { sampling_integrity: {
        callback_failures: 1, publisher_failures: 0, collection_failures: 0, stop_pending_threads: ["gpu-sampler"],
      } } },
      telemetry_live: { latest: {
        memory_observation_version: 1,
        memory_observations: {
          nvmap: { age_seconds: 3.2, fresh: false },
          system: { age_seconds: 0.1, fresh: true },
        },
        sampling_diagnostics: { collection_seconds: null, schedule_lateness_seconds: 0.25 },
      } },
    });
    expect(rows.find((row) => row[0] === "内存观测值龄")?.[1]).toBe("3.200 s");
    expect(rows.find((row) => row[0] === "内存来源有效性")?.slice(1)).toEqual(["1/2", "不可用或过期：nvmap"]);
    expect(rows.find((row) => row[0] === "采集 / 调度延迟")?.[1]).toBe("不可用 / 0.250 s");
    expect(rows.find((row) => row[0] === "采样记录 / 发布 / 采集异常")?.slice(1)).toEqual([
      "1 / 0 / 0", "停止超时，线程待退出：gpu-sampler",
    ]);
  });

  it("历史缺少来源记录时不补造；真实零耗时保留", () => {
    expect(telemetryObservationRows({})).toEqual([]);
    const rows = telemetryObservationRows({ telemetry_live: { latest: {
      sampling_diagnostics: { collection_seconds: 0, schedule_lateness_seconds: 0 },
    } } });
    expect(rows[0][1]).toBe("0.000 s / 0.000 s");
  });
});


it("取消后的partial遥测保持部分采集标记", () => {
  expect(gpuMetricsView({ status: "cancelled", phases: { gpu_metrics: {
    status: "partial", partial: true, samples_count: 10, collection_end_reason: "cancelled",
  } } })).toMatchObject({ status: "partial", partial: true, collection_end_reason: "cancelled" });
});
