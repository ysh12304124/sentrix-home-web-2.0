import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SourceGrid, SourceMetrics } from "./SourceGrid.tsx";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("来源原文与引用", () => {
  it("从题包读取 GT 邮件，展示完整原文", async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, text: async () => "Subject: Invoice\nTotal $432.07" });
    vi.stubGlobal("fetch", fetcher);
    const { container } = render(<SourceGrid albumId="atm-hard" items={[{ source_type: "email", source_id: "email1" }]} />);
    expect(fetcher).not.toHaveBeenCalled();
    const details = container.querySelector("details")!;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    await waitFor(() => expect(screen.getByText(/Subject: Invoice/)).toBeTruthy());
    expect(fetcher).toHaveBeenCalledWith("/api/v1/albums/atm-hard/documents/email1.txt");
    details.open = false;
    fireEvent(details, new Event("toggle"));
    details.open = true;
    fireEvent(details, new Event("toggle"));
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("折叠邮件只占一行标题和时间，不在摘要中堆叠ID、片段和原文", () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const { container } = render(<SourceGrid albumId="atm" items={[{ source_type:"email",source_id:"long-email-id",title:"酒店确认",source_timestamp:"2026-11-19T10:00:00Z",excerpt:"金额799.74",summary:"酒店摘要" }]} />);
    const summary = container.querySelector("summary")!;
    expect(summary.textContent).toContain("邮件 · 酒店确认");
    expect(summary.querySelector("time")).toBeTruthy();
    expect(summary.textContent).not.toMatch(/long-email-id|799.74|酒店摘要/);
    expect(container.querySelector("details")?.open).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("引用展示真实片段；原文通过资产代理读取", async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, text: async () => "Full original email" });
    vi.stubGlobal("fetch", fetcher);
    const { container } = render(<SourceGrid albumId="atm" items={[{ asset_id: "asset1", source_id: "email1", title: "Invoice", excerpt: "$432.07" }]} />);
    expect(screen.getByText("$432.07")).toBeTruthy();
    const details = container.querySelector("details")!;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    await waitFor(() => expect(screen.getByText("Full original email")).toBeTruthy());
    expect(fetcher).toHaveBeenCalledWith("/api/v1/assets/asset1/file");
  });
  it("候选、阅读与引用的指标分别显示", () => {
    render(<SourceMetrics metrics={{ candidates: { status: "available", matched: 2, gt: 3, recall: 2/3, precision: 1 }, reads: { status: "unavailable", matched: 0, gt: 3 }, citations: { status: "available", matched: 0, gt: 3, recall: 0, precision: 0 } }} />);
    expect(screen.getByText("66.7%")).toBeTruthy();
    expect(screen.getByText("最终引用")).toBeTruthy();
    expect(screen.getAllByText("未记录")).toHaveLength(2);
  });
});
