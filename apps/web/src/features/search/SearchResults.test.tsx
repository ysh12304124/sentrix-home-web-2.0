import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return { ...actual, getAsset: vi.fn() };
});

import * as client from "../../shared/api/client";
import type { AssetSummary, SearchResponse } from "../../shared/api/client";
import { SearchResults } from "./SearchResults";

function asset(overrides: Partial<AssetSummary> & { id: string }): AssetSummary {
  return {
    file_name: "a.jpg",
    media_type: "image",
    mime_type: "image/jpeg",
    size_bytes: 1,
    status: "processed",
    captured_at: "2026-01-02T10:00:00+00:00",
    gps: null,
    ...overrides,
  };
}

function response(hitAsset: AssetSummary): SearchResponse {
  return {
    query: "桥",
    backend: "test",
    degraded: false,
    degraded_reason: "",
    authoritative_vectors: 1,
    items: [
      {
        asset: hitAsset,
        score: 0.1,
        channels: { visual_ann: 1 },
        raw_scores: { visual_ann: 0.91 },
      },
    ],
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("检索结果", () => {
  it("图片结果是按钮，点开显示大图与时间 / 地点", () => {
    render(
      <SearchResults
        response={response(
          asset({ id: "a1", file_name: "bridge.jpg", gps: { latitude: 31.23, longitude: 121.47 } }),
        )}
        onClose={() => {}}
      />,
    );

    fireEvent.click(screen.getByTitle("查看详情"));

    const dialog = screen.getByRole("dialog", { name: "资产详情" });
    expect(within(dialog).getByRole("img")).toBeTruthy();
    expect(within(dialog).getByText("bridge.jpg")).toBeTruthy();
    expect(within(dialog).getByText(/2026.*01.*02/)).toBeTruthy();
    expect(within(dialog).getByText("31.230000, 121.470000")).toBeTruthy();
  });

  it("命中视频帧时在源视频上定位到 source_timestamp_sec", async () => {
    vi.mocked(client.getAsset).mockResolvedValue(
      asset({ id: "v1", file_name: "trip.mp4", media_type: "video", mime_type: "video/mp4" }),
    );
    render(
      <SearchResults
        response={response(
          asset({
            id: "frame1",
            file_name: "frame.jpg",
            parent_asset_id: "v1",
            source_timestamp_sec: 12.5,
          }),
        )}
        onClose={() => {}}
      />,
    );

    const video = screen.getByLabelText("检索命中的视频") as HTMLVideoElement;
    expect(video.src).toContain("/api/v1/assets/v1/preview");
    Object.defineProperty(video, "currentTime", { value: 0, writable: true });
    fireEvent.loadedMetadata(video);
    expect(video.currentTime).toBe(12.5);

    fireEvent.click(screen.getByTitle("查看详情"));
    await waitFor(() => expect(client.getAsset).toHaveBeenCalledWith("v1"));
    const dialog = await screen.findByRole("dialog", { name: "资产详情" });
    expect(within(dialog).getByText("trip.mp4")).toBeTruthy();
  });
});
