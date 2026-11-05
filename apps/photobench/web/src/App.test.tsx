import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ManifestSummary } from "./api/types.ts";

const { mockApi, mockPost } = vi.hoisted(() => ({ mockApi: vi.fn(), mockPost: vi.fn() }));
vi.mock("./api/client.ts", () => ({ api: mockApi, post: mockPost, BASE: "/api" }));

import { App } from "./App.tsx";

// 这个工程没开 vitest `globals`，testing-library 的自动 cleanup 不会注册——不显式
// 清理的话上一个用例挂上的 DOM 还在，`getByRole` 会因为找到两个按钮而抛错。
afterEach(cleanup);

function jsonResponse(status: number, body: unknown) {
  const text = JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => text,
    json: async () => JSON.parse(text),
  };
}

const MANIFEST: ManifestSummary = {
  album_id: "album3",
  album_name: "A3",
  face_count: 1,
  photo_count: 2,
  video_count: 0,
  qa_sets: ["compact-10q"],
};

const CONFIG = {
  runtime_config: {},
  vllm_targets: {},
  default_vllm_target_id: "",
  judge_model: "cfg-jm",
};

/** `loadRuns` 是否失败：init 的失败语义夹具（见下）。 */
let failRuns = false;

beforeEach(() => {
  failRuns = false;
  // jsdom 没实现 scrollIntoView；selectRun → scrollToRunDetail 的 rAF 会调它。
  Element.prototype.scrollIntoView = vi.fn();
  mockApi.mockReset();
  mockPost.mockReset();
  mockApi.mockImplementation((path: string) => {
    if (path === "/config") return Promise.resolve(CONFIG);
    if (path === "/manifests") return Promise.resolve({ manifests: [MANIFEST] });
    if (path.startsWith("/runs?")) {
      if (failRuns) return Promise.reject(new Error("runs boom"));
      return Promise.resolve({
        runs: [],
        page: 1,
        page_size: 20,
        total: 0,
        pages: 1,
        has_previous: false,
        has_next: false,
        active_count: 0,
      });
    }
    if (path.startsWith("/qa-dataset")) return Promise.resolve({ items: [], media_resolution: null });
    return Promise.resolve({});
  });
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.endsWith("/api/v1/judge-prompts")) return Promise.resolve(jsonResponse(200, { kinds: [] }));
      return Promise.reject(new Error(`unexpected fetch ${url}`));
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 夹具的口径：**切换后旧视图必须消失**。只断言「新视图在」是不够的——两个视图
// 同时挂上（比如把 v-if 写成恒真）也能让那种断言变绿。所以每个方向都断言「在」与
// 「不在」两半，两个口径给出不同答案。
describe("视图外壳", () => {
  it("默认显示评测运行；切到 QA 数据集浏览后评测运行消失", async () => {
    render(<App />);
    await screen.findByTestId("runs-view");

    expect(screen.queryByTestId("runs-view")).not.toBeNull();
    expect(screen.queryByTestId("qa-browser-view")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "QA 数据集浏览" }));

    expect(screen.queryByTestId("qa-browser-view")).not.toBeNull();
    expect(screen.queryByTestId("runs-view")).toBeNull();
  });

  it("能切回评测运行", async () => {
    render(<App />);
    await screen.findByTestId("runs-view");

    fireEvent.click(screen.getByRole("button", { name: "QA 数据集浏览" }));
    fireEvent.click(screen.getByRole("button", { name: "评测运行" }));

    expect(screen.queryByTestId("runs-view")).not.toBeNull();
    expect(screen.queryByTestId("qa-browser-view")).toBeNull();
  });

  it("加载态渲染 Loading，而不是任何一个视图", () => {
    render(<App initialLoading />);

    expect(screen.getByRole("status")).not.toBeNull();
    expect(screen.queryByTestId("runs-view")).toBeNull();
    expect(screen.queryByTestId("qa-browser-view")).toBeNull();
  });
});

// 1.0 `init()`（`:2751-2757`）把「loadJudgePrompts + /manifests + loadRuns(1) +
// loadProfiles()」放进同一个 Promise.all，**任一条失败都会跳过 `manifests.value = ...`**。
// 这两条用例就是钉住这个失败语义：成功时相册下拉出现，loadRuns 失败时不出现。
describe("init() 的失败语义（1.0 :2751-2757）", () => {
  it("成功：manifests 被赋值 → 相册下拉出现该 album", async () => {
    render(<App />);

    expect(await screen.findByRole("option", { name: "A3 (1人 / 2图)" })).not.toBeNull();
  });

  it("loadRuns 失败：manifests 不被赋值（相册下拉没有该 album）", async () => {
    failRuns = true;
    render(<App />);

    // 等 init 走完（错误被写进配置区）。
    await screen.findByText("runs boom");

    expect(screen.queryByRole("option", { name: "A3 (1人 / 2图)" })).toBeNull();
  });
});

// P2：`RunDetail` 的 `memory` 不接线时整个记忆面板不渲染。这条就是把接好之后
// 「面板确实在」钉住——删掉组合层对 `memory` 的传参，这条立刻红。
describe("记忆面板接线（P2 / 1.0 :3147）", () => {
  it("选中 run 后记忆层级面板渲染出来", async () => {
    mockApi.mockImplementation((path: string) => {
      if (path === "/config") return Promise.resolve(CONFIG);
      if (path === "/manifests") return Promise.resolve({ manifests: [MANIFEST] });
      if (path.startsWith("/runs?")) {
        return Promise.resolve({
          runs: [{ run_id: "runX", status: "done", mode: "reuse" }],
          page: 1,
          page_size: 20,
          total: 1,
          pages: 1,
          has_previous: false,
          has_next: false,
          active_count: 0,
        });
      }
      if (path.startsWith("/runs/runX/items")) {
        return Promise.resolve({ items: [], page: 1, page_size: 20, total: 0, pages: 1 });
      }
      if (path.startsWith("/runs/runX/reviews")) return Promise.resolve({ reviews: {} });
      if (path.startsWith("/runs/runX?include_history=true")) return Promise.resolve({ run_id: "runX", status: "done", mode: "reuse" });
      return Promise.resolve({});
    });

    const { container } = render(<App />);
    await screen.findByTestId("runs-view");

    // 点行 = 1.0 的 selectRun（行本身挂 onClick）。
    const row = container.querySelector(".run-row") as HTMLElement;
    expect(row).not.toBeNull();
    fireEvent.click(row);

    expect(await screen.findByText("记忆层级与有效证据链")).not.toBeNull();
  });
});

// 1.0 `:2758` / `:2761-2765`：init 发现 running/pending 的 run 就自动选中并开轮询。
describe("init() 的自动选中（1.0 :2758 / :2761-2765）", () => {
  it("列表里有 running run：自动选中并拉它的详情", async () => {
    mockApi.mockImplementation((path: string) => {
      if (path === "/config") return Promise.resolve(CONFIG);
      if (path === "/manifests") return Promise.resolve({ manifests: [MANIFEST] });
      if (path.startsWith("/runs?")) {
        return Promise.resolve({
          runs: [{ run_id: "runX", status: "running" }],
          page: 1,
          page_size: 20,
          total: 1,
          pages: 1,
          has_previous: false,
          has_next: false,
          active_count: 1,
        });
      }
      if (path.startsWith("/runs/runX/items")) {
        return Promise.resolve({ items: [], page: 1, page_size: 20, total: 0, pages: 1 });
      }
      if (path.startsWith("/runs/runX?include_history=true")) return Promise.resolve({ run_id: "runX", status: "running", mode: "reuse" });
      return Promise.resolve({});
    });

    render(<App />);

    await waitFor(() =>
      expect(mockApi.mock.calls.some(([path]) => path === "/runs/runX?include_history=true")).toBe(true),
    );
  });
});


describe("停止全部", () => {
  it("显示待收尾而不提前宣称退出，并能复查到停止", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let cleanupCalls = 0;
    const fallback = mockApi.getMockImplementation()!;
    mockApi.mockImplementation((path: string) => {
      if (path === "/active-work") return Promise.resolve({ cleanup_status: "pending" });
      if (path === "/cancel-active") return Promise.resolve({ cancelled: ["r"] });
      if (path === "/active-work/cleanup") return Promise.resolve({ cleanup_status: ++cleanupCalls > 1 ? "stopped" : "pending" });
      return fallback(path);
    });
    render(<App />);
    await screen.findByTestId("runs-view");
    fireEvent.click(screen.getByRole("button", { name: /停止全部/ }));
    expect(await screen.findByText(/已请求取消，正在等待后台任务退出/)).toBeTruthy();
    expect(screen.queryByText("已停止，后台任务已退出")).toBeNull();
    expect(await screen.findByText("已停止，后台任务已退出", {}, { timeout: 4000 })).toBeTruthy();
  });

  it("请求失败不会显示为停止成功", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const fallback = mockApi.getMockImplementation()!;
    mockApi.mockImplementation((path: string) => path === "/cancel-active" ? Promise.reject(new Error("connection lost")) : fallback(path));
    render(<App />);
    await screen.findByTestId("runs-view");
    fireEvent.click(screen.getByRole("button", { name: /停止全部/ }));
    expect(await screen.findByText("停止未确认：connection lost")).toBeTruthy();
    expect(screen.queryByText("已停止，后台任务已退出")).toBeNull();
  });
});
