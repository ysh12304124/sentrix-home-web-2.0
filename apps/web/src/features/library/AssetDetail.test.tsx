import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (original) => ({
  ...await original<typeof import("../../shared/api/client")>(),
  getVideo: vi.fn().mockResolvedValue({ scenes: [] }),
}));

import type { AssetSummary, ObservationPayload } from "../../shared/api/client";
import { AssetDetail } from "./AssetDetail";

function asset(overrides: Partial<AssetSummary> & { id: string }): AssetSummary {
  return {
    file_name: "photo.jpg",
    media_type: "image",
    mime_type: "image/jpeg",
    size_bytes: 1024,
    status: "processed",
    captured_at: "2026-01-02T10:00:00+00:00",
    gps: { latitude: 31.23, longitude: 121.47 },
    ...overrides,
  };
}

const observation: ObservationPayload = {
  id: "obs1",
  asset_id: "a1",
  source_type: "image",
  captured_at: "2026-01-02T10:00:00+00:00",
  caption: "桥边有一排红色的花",
  asset: { id: "a1", file_name: "photo.jpg", media_type: "image", path: "/tmp/photo.jpg" },
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("资产详情", () => {
  it("图片走展示 URL，时间与地点都显示", () => {
    render(<AssetDetail asset={asset({ id: "a1" })} onClose={() => {}} />);
    const image = screen.getByRole("img") as HTMLImageElement;
    expect(image.src).toContain("/api/v1/assets/a1/preview");
    expect(screen.getByText(/2026.*01.*02/)).toBeTruthy();
    expect(screen.getByText("31.2300, 121.4700")).toBeTruthy();
    expect(screen.getByRole("link", { name: "打开原文件" }).getAttribute("href")).toBe("/api/v1/assets/a1/file");
  });

  it("视频走 /preview，并在元数据就绪时定位到命中秒数", () => {
    render(
      <AssetDetail
        asset={asset({ id: "v1", file_name: "clip.mp4", media_type: "video", mime_type: "video/mp4" })}
        initialTimeSec={12.5}
        onClose={() => {}}
      />,
    );
    const video = screen.getByLabelText("视频") as HTMLVideoElement;
    expect(video.src).toContain("/api/v1/assets/v1/preview");
    Object.defineProperty(video, "currentTime", { value: 0, writable: true });
    fireEvent.loadedMetadata(video);
    expect(video.currentTime).toBe(12.5);
  });

  it("音频渲染 audio 控件并走 /preview", () => {
    render(
      <AssetDetail
        asset={asset({ id: "s1", file_name: "voice.m4a", media_type: "audio", mime_type: "audio/mp4" })}
        onClose={() => {}}
      />,
    );
    const audio = screen.getByLabelText("音频") as HTMLAudioElement;
    expect(audio.src).toContain("/api/v1/assets/s1/preview");
  });

  it("文本资产把 /file 的内容读出来", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, text: async () => "这是一段导入的笔记" }),
    );
    render(
      <AssetDetail
        asset={asset({ id: "t1", file_name: "note.txt", media_type: "text", mime_type: "text/plain" })}
        onClose={() => {}}
      />,
    );
    await waitFor(() => expect(screen.getByText("这是一段导入的笔记")).toBeTruthy());
  });

  it("有 Observation 就列出；没有时省略整理记录区", () => {
    const { rerender } = render(
      <AssetDetail asset={asset({ id: "a1" })} observations={[observation]} onClose={() => {}} />,
    );
    expect(screen.getByText("桥边有一排红色的花")).toBeTruthy();

    rerender(<AssetDetail asset={asset({ id: "a1" })} onClose={() => {}} />);
    expect(screen.queryByText("整理记录")).toBeNull();
    expect(screen.queryByText("桥边有一排红色的花")).toBeNull();
    expect(screen.getByRole("link", { name: "打开原文件" })).toBeTruthy();
  });

  it("确认删除后调用 API 并把结果交给资料库列表", async () => {
    const onDeleted = vi.fn();
    const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ asset_id: "a1", deleted: true, warnings: [] }),
    });
    vi.stubGlobal("confirm", confirm);
    vi.stubGlobal("fetch", fetch);
    render(<AssetDetail asset={asset({ id: "a1" })} onClose={() => {}} onDeleted={onDeleted} />);

    fireEvent.click(screen.getByRole("button", { name: "删除媒体及关联记忆" }));
    expect(fetch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "删除媒体及关联记忆" }));

    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith("a1", []));
    expect(fetch).toHaveBeenCalledWith("/api/v1/assets/a1", {
      method: "DELETE",
      credentials: "same-origin",
      headers: { "x-csrf-token": "" },
    });
  });
});
