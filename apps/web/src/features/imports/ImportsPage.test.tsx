import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return {
    ...actual,
    importAsset: vi.fn(),
    listAssets: vi.fn(),
    listMemorySpaces: vi.fn(),
    getVideo: vi.fn(),
    reprocessVideo: vi.fn(),
  };
});

import * as client from "../../shared/api/client";
import { ScopeProvider } from "../../app/ScopeProvider";
import { ToastProvider } from "../../shared/ui/Toast";
import { ImportsPage, describeImportResult } from "./ImportsPage";

function okResponse(overrides: Partial<client.ImportResponse> = {}): client.ImportResponse {
  return {
    asset_id: "asset-1",
    observation_id: null,
    media_type: "image",
    status: "processed",
    deduplicated: false,
    indexed: true,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <ToastProvider>
      <ScopeProvider>
        <MemoryRouter>
          <ImportsPage />
        </MemoryRouter>
      </ScopeProvider>
    </ToastProvider>,
  );
}

function chooseFiles(container: HTMLElement, files: File[]) {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files } });
}

beforeEach(() => {
  vi.mocked(client.listMemorySpaces).mockResolvedValue([]);
  vi.mocked(client.listAssets).mockResolvedValue([]);
  vi.mocked(client.getVideo).mockResolvedValue({
    video: {
      id: "video-1",
      file_name: "clip.mp4",
      status: "processed",
      captured_at: null,
      processing_seconds: 1,
      probe_error: "",
    },
    scenes: [],
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("describeImportResult", () => {
  it("把视频元数据与场景数如实带出来", () => {
    expect(
      describeImportResult(
        okResponse({ media_type: "video", video_probed: true, video_scenes: 3, indexed: false }),
      ),
    ).toBe("已读取视频元数据 · 已切分 3 个场景");
  });

  it("去重优先于其他状态", () => {
    expect(describeImportResult(okResponse({ deduplicated: true }))).toBe("已存在，跳过");
  });

  it("图片导入成功但没有向量时不谎称已完成索引", () => {
    expect(describeImportResult(okResponse({ indexed: false }))).toBe("已导入，但未写视觉向量");
  });
});

describe("ImportsPage 批量与重试", () => {
  it("一次选两个文件会并发提交，失败项保留并可单独重试", async () => {
    vi.mocked(client.importAsset)
      .mockResolvedValueOnce(okResponse({ asset_id: "a-1" }))
      .mockRejectedValueOnce(new Error("上传失败"))
      .mockResolvedValueOnce(okResponse({ asset_id: "a-2" }));

    const { container } = renderPage();
    chooseFiles(container, [
      new File(["one"], "one.jpg", { type: "image/jpeg" }),
      new File(["two"], "two.jpg", { type: "image/jpeg" }),
    ]);

    // 两个文件都立刻提交，而不是等第一个完成（并发批）。
    await waitFor(() => expect(client.importAsset).toHaveBeenCalledTimes(2));

    await screen.findByText("重试失败项（1）↻");
    expect(screen.getByText("导入完成：1 个成功，1 个失败")).toBeTruthy();
    expect(screen.getByText("上传失败")).toBeTruthy();

    fireEvent.click(screen.getByText("重试失败项（1）↻"));

    await waitFor(() => expect(client.importAsset).toHaveBeenCalledTimes(3));
    await screen.findByText("重试完成：1 个文件");
    expect(screen.queryByText(/重试失败项/)).toBeNull();
  });

  it("失败的视频记录走 /videos/{id}/reprocess，而不是重新上传", async () => {
    vi.mocked(client.listAssets).mockResolvedValue([
      {
        id: "video-9",
        file_name: "broken.mp4",
        media_type: "video",
        mime_type: "video/mp4",
        size_bytes: 10,
        status: "failed",
        captured_at: null,
        gps: null,
      },
    ]);
    vi.mocked(client.reprocessVideo).mockResolvedValue({
      accepted: true,
      asset_id: "video-9",
      status: "processed",
    });

    renderPage();

    const button = await screen.findByText("重试视频处理");
    fireEvent.click(button);

    await waitFor(() => expect(client.reprocessVideo).toHaveBeenCalledWith("video-9"));
    await screen.findByText("视频「broken.mp4」已重新处理");
    // 重试不是重新上传：没有文件被再传一遍。
    expect(client.importAsset).not.toHaveBeenCalled();
  });

  it("流水线展示视频场景数与关键帧数", async () => {
    vi.mocked(client.listAssets).mockResolvedValue([
      {
        id: "video-1",
        file_name: "clip.mp4",
        media_type: "video",
        mime_type: "video/mp4",
        size_bytes: 10,
        status: "processed",
        captured_at: null,
        gps: null,
      },
      {
        id: "frame-1",
        file_name: "frame.jpg",
        media_type: "image",
        mime_type: "image/jpeg",
        size_bytes: 10,
        status: "processed",
        captured_at: null,
        gps: null,
        derived_kind: "video_keyframe",
      },
    ]);
    vi.mocked(client.getVideo).mockResolvedValue({
      video: {
        id: "video-1",
        file_name: "clip.mp4",
        status: "processed",
        captured_at: null,
        processing_seconds: 1,
        probe_error: "",
      },
      scenes: [
        {
          id: "s-1",
          scope_id: "",
          title: "场景",
          event_type: "video_scene",
          time_start: null,
          time_end: null,
          place: "",
          activity: "",
          summary: "",
          confidence: 1,
          status: "active",
          source_type: "video_scene",
          source_asset_id: "video-1",
          source_scene_index: 0,
          source_start_sec: 0,
          source_end_sec: 1,
          speech_segments: [],
          keyframe_timestamps_sec: [],
        },
      ],
    });

    renderPage();

    await screen.findByText("1 个场景");
    expect(screen.getByText("1 张关键帧 · 1 张已理解")).toBeTruthy();
  });
});
