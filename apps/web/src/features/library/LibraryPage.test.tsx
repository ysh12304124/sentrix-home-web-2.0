import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { listAssetPage, listMediaDeletions, retryMediaCleanup } from "../../shared/api/client";
import { LibraryPage } from "./LibraryPage";

vi.mock("../../app/ScopeProvider", () => ({
  useScope: () => ({ scopeId: "scope-x", spaces: [{ id: "scope-x", name: "测试相册" }] }),
}));
vi.mock("../../shared/api/client", () => ({
  listAssetPage: vi.fn(),
  listMediaDeletions: vi.fn(),
  retryMediaCleanup: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("shows persisted cleanup failures after reload and refreshes the record after retry", async () => {
  const record = {
    asset_id: "photo-1", scope_id: "scope-x", file_name: "photo.jpg", media_type: "image",
    deleted_at: "2026-11-11T01:00:00Z", asset_count: 1, files_done: true,
    vectors_done: false, attempts: 1, last_error: "vector: unavailable",
  };
  vi.mocked(listAssetPage).mockResolvedValue({
    items: [], total: 0, offset: 0, limit: 60, counts: { all: 0 },
  });
  vi.mocked(listMediaDeletions)
    .mockResolvedValueOnce([record])
    .mockResolvedValueOnce([{ ...record, vectors_done: true, attempts: 2, last_error: "" }]);
  vi.mocked(retryMediaCleanup).mockResolvedValue({ warnings: [] });

  render(<LibraryPage />);
  await waitFor(() => expect(screen.getByText("photo.jpg")).toBeTruthy());
  expect(listMediaDeletions).toHaveBeenCalledWith("scope-x");
  expect(screen.getByText("vector: unavailable")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "重试外部清理" }));
  await waitFor(() => expect(listMediaDeletions).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.getByRole("status").textContent).toContain("外部清理已完成"));
  expect(retryMediaCleanup).toHaveBeenCalledTimes(1);
});
