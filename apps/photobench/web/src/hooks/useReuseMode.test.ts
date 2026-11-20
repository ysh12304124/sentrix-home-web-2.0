import { act, renderHook } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useReuseMode } from "./useReuseMode.ts";
import type { ManifestSummary, MemorySpace, ReuseBase, Run } from "../api/types.ts";

function jsonResponse(status: number, body: unknown) {
  const text = JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => text,
    json: async () => JSON.parse(text),
  };
}

const space = (id: string, name = ""): MemorySpace => ({ id, name });

const base = (overrides: Partial<ReuseBase> = {}): ReuseBase => ({
  run_id: "r1",
  base_id: "b1",
  album_id: "album3",
  model_profile: "mp",
  scope_id: "s1",
  scope_name: "sn",
  created_at: "2026-01-01",
  source_run_ids: [],
  ready: true,
  ...overrides,
});

const manifest = (overrides: Partial<ManifestSummary> = {}): ManifestSummary => ({
  album_id: "album3",
  album_name: "A3",
  face_count: 1,
  photo_count: 2,
  video_count: 0,
  qa_sets: ["compact-10q"],
  ...overrides,
});

interface HarnessProps {
  manifests: ManifestSummary[];
  runs: Run[];
  sentrixUrl: string;
  judgeProviderId: string;
  judgeProviders: Array<Record<string, unknown>>;
}

function props(overrides: Partial<HarnessProps> = {}): HarnessProps {
  return {
    manifests: [],
    runs: [],
    sentrixUrl: "127.0.0.1:8091",
    judgeProviderId: "",
    judgeProviders: [],
    ...overrides,
  };
}

function renderReuse(initial: HarnessProps = props()) {
  const onDirty = vi.fn();
  const rendered = renderHook(
    (hookProps: HarnessProps) => {
      const [selectedAlbum, setSelectedAlbum] = useState("initial-album");
      const [selectedQa, setSelectedQa] = useState("compact-10q");
      const [judgeUrl, setJudgeUrl] = useState("");
      const [judgeModel, setJudgeModel] = useState("orig-model");
      const reuse = useReuseMode({
        manifests: hookProps.manifests,
        runs: hookProps.runs,
        sentrixUrl: hookProps.sentrixUrl,
        selectedQa,
        setSelectedAlbum,
        setSelectedQa,
        judgeProviderId: hookProps.judgeProviderId,
        judgeProviders: hookProps.judgeProviders,
        judgeModel,
        setJudgeUrl,
        setJudgeModel,
        onDirty,
      });
      return { reuse, selectedAlbum, selectedQa, judgeUrl, judgeModel };
    },
    { initialProps: initial },
  );
  return { ...rendered, onDirty };
}

type ReuseHandle = { current: { reuse: ReturnType<typeof useReuseMode> } };

/** 切到 reuse 模式会触发一次 loadMemorySpaces（1.0 `onModeChange`）；再显式等它落定。 */
async function enterReuseMode(result: ReuseHandle) {
  await act(async () => {
    result.current.reuse.onModeChange("reuse");
    await Promise.resolve();
  });
  await act(async () => {
    await result.current.reuse.loadMemorySpaces();
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useReuseMode.loadMemorySpaces", () => {
  it("裸 fetch，URL 带 sentrix_url，铺开 spaces 与 reuse_bases", async () => {
    const fetchStub = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { spaces: [space("s1", "album3")], reuse_bases: [base()] }));
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderReuse();

    await act(async () => {
      await result.current.reuse.loadMemorySpaces();
    });

    expect(String(fetchStub.mock.calls[0]![0])).toBe(
      "/api/v1/memory-spaces?sentrix_url=127.0.0.1%3A8091",
    );
    expect(result.current.reuse.memorySpaces.map((s) => s.id)).toEqual(["s1"]);
    expect(result.current.reuse.reuseBases.map((b) => b.base_id)).toEqual(["b1"]);
    expect(result.current.reuse.memorySpacesLoading).toBe(false);
  });

  it("失败时两个列表都清空（1.0 :2510 原样）", async () => {
    const fetchStub = vi.fn().mockRejectedValue(new Error("network down"));
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderReuse();

    await act(async () => {
      await result.current.reuse.loadMemorySpaces();
    });

    expect(result.current.reuse.memorySpaces).toEqual([]);
    expect(result.current.reuse.reuseBases).toEqual([]);
    expect(result.current.reuse.memorySpacesLoading).toBe(false);
  });
});

describe("watch(existingScopeId) 的 React 等价", () => {
  it("reuse 模式下改 existingScopeId → 推断出相册并写进 hint / selectedAlbum", async () => {
    const fetchStub = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { spaces: [space("s1", "无匹配名")], reuse_bases: [] }));
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderReuse(
      props({
        runs: [{ run_id: "r1", scope_id: "s1", mode: "full", album_id: "album3" }],
        manifests: [manifest({ album_id: "album3" })],
      }),
    );
    await enterReuseMode(result);

    act(() => result.current.reuse.setExistingScopeId("s1"));

    expect(result.current.selectedAlbum).toBe("album3");
    expect(result.current.reuse.scopeAlbumHint).toBe("题目对照数据集：album3");
  });

  it("不在 reuse 模式时改 existingScopeId → 两个下游值都不变", async () => {
    const { result } = renderReuse(
      props({
        runs: [{ run_id: "r1", scope_id: "s1", mode: "full", album_id: "album3" }],
        manifests: [manifest({ album_id: "album3" })],
      }),
    );

    act(() => result.current.reuse.setExistingScopeId("s1"));

    expect(result.current.selectedAlbum).toBe("initial-album");
    expect(result.current.reuse.scopeAlbumHint).toBe("");
  });
});

describe("watch(selectedReuseBaseId) 的 React 等价", () => {
  it("选了基座 → 推进 existingScopeId / album，并把 hint 覆盖成推断结果", async () => {
    const fetchStub = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { spaces: [space("s1", "album3")], reuse_bases: [base()] }));
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderReuse(props({ manifests: [manifest({ album_id: "album3" })] }));
    await enterReuseMode(result);

    act(() => result.current.reuse.setSelectedReuseBaseId("b1"));

    expect(result.current.reuse.existingScopeId).toBe("s1");
    expect(result.current.selectedAlbum).toBe("album3");
    expect(result.current.reuse.selectedReuseBase?.base_id).toBe("b1");
    // 基座 watch 先写 hint，随后 existingScopeId watch 覆盖成「题目对照数据集」（1.0 顺序）。
    expect(result.current.reuse.scopeAlbumHint).toBe("题目对照数据集：album3");
    // 2026-10-15 修：1.0 的 ``qa_sets`` 是对象、2.0 是数组，照搬 ``Object.keys``
    // 会得到 ``["0"]`` 而把 ``selectedQa`` 写成 ``"0"`` —— 一选复用基座题集就废，
    // 而那是复用这条路唯一的入口。现在两种形状都认，题集名原样保留。
    expect(result.current.selectedQa).toBe("compact-10q");
  });

  it("不在 reuse 模式时选基座 → existingScopeId 不变", async () => {
    const { result } = renderReuse(props({ manifests: [manifest()] }));

    act(() => result.current.reuse.setSelectedReuseBaseId("b1"));

    expect(result.current.reuse.existingScopeId).toBe("");
    expect(result.current.selectedAlbum).toBe("initial-album");
    expect(result.current.selectedQa).toBe("compact-10q");
  });

  it("基座带 album 但清单里没有对应 manifest → 不误改 selectedQa", async () => {
    const fetchStub = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { spaces: [], reuse_bases: [base({ album_id: "albumX" })] }));
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderReuse(props({ manifests: [] }));
    await enterReuseMode(result);

    act(() => result.current.reuse.setSelectedReuseBaseId("b1"));

    expect(result.current.selectedAlbum).toBe("albumX");
    expect(result.current.selectedQa).toBe("compact-10q");
  });
});

describe("watch(judgeProviderId) 的 React 等价", () => {
  it("provider 变化 → 覆盖 judgeUrl / judgeModel 并标脏", () => {
    const { result, onDirty, rerender } = renderReuse(
      props({ judgeProviders: [{ id: "p1", url: "http://judge", model: "jm1" }] }),
    );

    rerender(props({ judgeProviders: [{ id: "p1", url: "http://judge", model: "jm1" }], judgeProviderId: "p1" }));

    expect(result.current.judgeUrl).toBe("http://judge");
    expect(result.current.judgeModel).toBe("jm1");
    expect(onDirty).toHaveBeenCalledTimes(1);
  });

  it("id 不变时重渲染不重复标脏（防循环）", () => {
    const providers = [{ id: "p1", url: "http://judge", model: "jm1" }];
    const { result, onDirty, rerender } = renderReuse(props({ judgeProviders: providers, judgeProviderId: "" }));

    rerender(props({ judgeProviders: providers, judgeProviderId: "p1" }));
    expect(onDirty).toHaveBeenCalledTimes(1);

    rerender(props({ judgeProviders: [...providers], judgeProviderId: "p1" }));
    expect(onDirty).toHaveBeenCalledTimes(1);
    expect(result.current.judgeUrl).toBe("http://judge");
  });

  it("provider 没有 url 时不动 judgeUrl；model 缺失时保留原 judgeModel", () => {
    const { result, onDirty, rerender } = renderReuse(
      props({ judgeProviders: [{ id: "p1", url: "http://judge", model: "" }] }),
    );

    rerender(props({ judgeProviders: [{ id: "p1", url: "http://judge", model: "" }], judgeProviderId: "p1" }));

    expect(result.current.judgeModel).toBe("orig-model");
    expect(result.current.judgeUrl).toBe("http://judge");
    expect(onDirty).toHaveBeenCalledTimes(1);
  });
});

describe("inferAlbumForScope", () => {
  it("先查 run 历史，再按相册名子串（长 id 优先）", () => {
    const { result } = renderReuse(
      props({
        runs: [{ run_id: "r1", scope_id: "s1", mode: "build", album_id: "album3" }],
        manifests: [manifest({ album_id: "album3" }), manifest({ album_id: "album3-14" })],
      }),
    );

    expect(result.current.reuse.inferAlbumForScope(space("s1", "album3-14"))).toBe("album3");
    expect(result.current.reuse.inferAlbumForScope(space("s9", "album3-14"))).toBe("album3-14");
    expect(result.current.reuse.inferAlbumForScope(space("s9", "no-match"))).toBeNull();
    expect(result.current.reuse.inferAlbumForScope(null)).toBeNull();
  });
});
