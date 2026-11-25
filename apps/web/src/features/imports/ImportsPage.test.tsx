import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { importDocumentPackage, listImportJobs } from "../../shared/api/client";
import { ImportsPage } from "./ImportsPage";

const uploads = vi.hoisted(() => ({
  entries: [], jobs: [], progress: undefined, assetTotal: 0,
  setImportData: vi.fn(), uploadNotices: [], resumeNotice: "",
  uploadRevision: 0, receiveSeqBase: null, startUploads: vi.fn(),
}));
vi.mock("../../app/ScopeProvider", () => ({ useScope: () => ({ scopeId: "scope-mail" }) }));
vi.mock("./UploadsProvider", () => ({ useUploads: () => uploads }));
vi.mock("../../shared/api/client", () => ({
  importDocumentPackage: vi.fn(), listImportJobs: vi.fn().mockResolvedValue([]),
  fetchImportProgress: vi.fn().mockResolvedValue({}),
  listAssetPage: vi.fn().mockResolvedValue({ total: 0 }),
}));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("三阶段导入页保留邮件入口，导入成功后刷新当前相册", async () => {
  vi.mocked(importDocumentPackage).mockResolvedValue({ total: 2, failed: 0, items: [] });
  render(<ImportsPage />);
  await waitFor(() => expect(uploads.setImportData).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("heading", { name: "添加资料" })).toBeTruthy();
  expect(screen.getByText("拖入照片或视频")).toBeTruthy();
  const file = new File(['[{"body":"邮件正文"}]'], "mail.json", { type: "application/json" });
  fireEvent.change(screen.getByLabelText("选择邮件或文档"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "导入文档" }));
  await waitFor(() => expect(importDocumentPackage).toHaveBeenCalledWith(file, "scope-mail"));
  await waitFor(() => expect(listImportJobs).toHaveBeenCalledTimes(2));
  expect(screen.getByRole("status").textContent).toContain("共 2 条，成功 2 条，失败 0 条");
});
