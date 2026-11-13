import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ManifestSummary, ReuseBase, Run } from "../../api/types.ts";
import { ConfigSection, type ConfigSectionProps } from "./ConfigSection.tsx";

const MANIFEST: ManifestSummary = {
  album_id: "album3",
  album_name: "A3",
  face_count: 1,
  photo_count: 2,
  video_count: 0,
  qa_sets: ["compact-10q"],
};

const REUSE_BASE: ReuseBase = {
  base_id: "b1",
  album_id: "albumX",
  model_profile: "prof",
  scope_id: "s1",
  scope_name: "scope",
  created_at: "",
  source_run_ids: [],
  ready: true,
};

function makeProps(
  runMode: string,
  onModeChange: (next?: string) => void,
): ConfigSectionProps {
  const selectedModels = new Set(["reg1"]);
  return {
    connection: {
      config: null,
      manifests: [MANIFEST],
      sentrixUrl: "",
      setSentrixUrl: vi.fn(),
      judgeUrl: "",
      setJudgeUrl: vi.fn(),
      judgeModel: "",
      setJudgeModel: vi.fn(),
      judgeApiKey: "",
      setJudgeApiKey: vi.fn(),
      setJudgeApiKeyDirty: vi.fn(),
      markConnectionConfigDirty: vi.fn(),
      connectionConfigState: "idle",
      connectionConfigMessage: "",
      saveConnectionConfig: vi.fn(),
    },
    model: {
      profiles: [],
      vllmManagerUrl: "127.0.0.1:8500",
      modelEndpoint: "127.0.0.1:8100",
      selectedEndpointModel: "m1",
      endpointModels: ["m1"],
      currentModelInfo: null,
      currentModelLoading: false,
      currentModelError: "",
      currentModelPopoverOpen: false,
      modelTestState: "idle",
      modelTestMessage: "",
      setVllmManagerUrl: vi.fn(),
      setModelEndpoint: vi.fn(),
      setSelectedEndpointModel: vi.fn(),
      setCurrentModelPopoverOpen: vi.fn(),
      loadProfiles: vi.fn(),
      loadCurrentModel: vi.fn(),
      onEndpointModelChange: vi.fn(),
      testEndpointModel: vi.fn(),
      onModelManagerInput: vi.fn(),
      onModelEndpointInput: vi.fn(),
      applySavedConnection: vi.fn(),
      selectedModels,
      setSelectedModels: vi.fn(),
    },
    reuse: {
      runMode,
      onModeChange,
      reuseBases: [REUSE_BASE],
      selectedReuseBaseId: "",
      setSelectedReuseBaseId: vi.fn(),
      memorySpacesLoading: false,
      scopeAlbumHint: "",
      existingScopeId: "",
    },
    album: {
      selectedAlbum: "album3",
      setSelectedAlbum: vi.fn(),
      selectedQa: "compact-10q",
      setSelectedQa: vi.fn(),
    },
    start: {
      runMode,
      hasRunning: false,
      suiteRunning: false,
      selectedModels,
      modelEndpoint: "127.0.0.1:8100",
      vllmManagerUrl: "127.0.0.1:8500",
      selectedEndpointModel: "m1",
      existingScopeId: "",
      deleteScopeAfterRun: false,
      onDeleteScopeAfterRunChange: vi.fn(),
      onStart: vi.fn(),
      onStop: vi.fn(),
    },
    error: "",
  };
}

/** 受控 `runMode` 的宿主：切下拉真的改变 `ConfigSection` 收到的 `runMode`。 */
function Harness() {
  const [runMode, setRunMode] = useState("full");
  return <ConfigSection {...makeProps(runMode, (next) => setRunMode(next ?? "full"))} />;
}

function modeSelect(): HTMLSelectElement {
  return screen.getByRole("combobox", { name: /工作模式/ }) as HTMLSelectElement;
}

afterEach(cleanup);

describe("ConfigSection 工作模式切换（可见性 + 可选项）", () => {
  it("full：照片与 QA 数据集在、复用基座不在", () => {
    render(<Harness />);
    expect(screen.getByRole("combobox", { name: /^照片/ })).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: /^复用基座/ })).toBeNull();
    expect(screen.getByRole("combobox", { name: /^QA 数据集/ })).toBeTruthy();
    expect(
      screen.getByRole("option", { name: "A3 (1人 / 2图)" }),
    ).toBeTruthy();
  });

  it("切到 reuse：照片消失、复用基座出现且选项是真实的基座；QA 提示换成复用口径", () => {
    render(<Harness />);
    fireEvent.change(modeSelect(), { target: { value: "reuse" } });

    expect(screen.queryByRole("combobox", { name: /^照片/ })).toBeNull();
    const reuseSelect = screen.getByRole("combobox", { name: /^复用基座/ }) as HTMLSelectElement;
    expect(within(reuseSelect).getByRole("option", { name: "albumX · prof · scope" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: /^QA 数据集/ })).toBeTruthy();
    expect(screen.getByText("题目与对照随现有相册匹配；这里选择题量。")).toBeTruthy();
  });

  it("切到 build：照片在（带构建提示）、复用基座与 QA 数据集都消失", () => {
    render(<Harness />);
    fireEvent.change(modeSelect(), { target: { value: "build" } });

    expect(screen.getByRole("combobox", { name: /^照片/ })).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: /^复用基座/ })).toBeNull();
    expect(screen.queryByRole("combobox", { name: /^QA 数据集/ })).toBeNull();
    expect(screen.getByText("本次要导入的照片、视频范围；处理后相册保留。")).toBeTruthy();
  });
});

describe("ConfigSection 启动按钮", () => {
  it("条件不满足时禁用并给出 1.0 的原因文案；满足时可点", () => {
    const { rerender } = render(<ConfigSection {...makeProps("full", vi.fn())} />);
    // selectedModels={reg1}、端点与管理器都在 → 可启动。
    const startButton = screen.getByRole("button", { name: "启动评测" }) as HTMLButtonElement;
    expect(startButton.disabled).toBe(false);

    const blocked = makeProps("reuse", vi.fn());
    blocked.start.existingScopeId = "";
    blocked.reuse.existingScopeId = "";
    rerender(<ConfigSection {...blocked} />);
    const reuseButton = screen.getByRole("button", { name: "启动复用测评" }) as HTMLButtonElement;
    expect(reuseButton.disabled).toBe(true);
    expect(reuseButton.title).toBe("请先选择要复用的相册");
  });
});

describe("ConfigSection 无图基线选择", () => {
  it("直接列出当前相册、题集和模型匹配的已完成无图 run", () => {
    const props = makeProps("full", vi.fn()) as ConfigSectionProps & { baselineRuns: Run[] };
    props.baselineRuns = [
      {
        run_id: "20261103-130008-album3-max-video10-current-c1d797",
        album_id: "album3",
        qa_set: "compact-10q",
        model_name: "m1",
        graph_enabled: false,
        status: "completed",
        finished_at: "2026-11-03T13:00:00+08:00",
        qa_count: 10,
      },
    ];

    render(<ConfigSection {...props} />);

    const select = screen.getByRole("combobox", { name: /无图基线 run/ });
    expect(within(select).getByRole("option", { name: /20261103-130008/ })).toBeTruthy();
  });
});
