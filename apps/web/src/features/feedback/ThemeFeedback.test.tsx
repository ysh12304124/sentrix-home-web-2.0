import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
vi.mock("../../app/theme/themeEntry", () => ({ isThemeEntry: () => true }));
const auth = vi.hoisted(() => ({ identity: { account_id: "alice", display_name: "Alice", role: "user" }, adminOnly: false }));
vi.mock("../../app/AuthProvider", () => ({ useAuth: () => auth }));
import { ThemeProvider } from "../../app/theme/ThemeProvider";
import { ScopeProvider } from "../../app/ScopeProvider";
import { THEME_STORAGE_KEY } from "../../app/theme/themes";
import { FeedbackPanel } from "./FeedbackPanel";
import { clearFeedbackDraft, readFeedbackDraft } from "./draft";
import { clearAppearanceDraft, readAppearanceDraft } from "./appearanceDraft";

function panel() { return render(<ThemeProvider><ScopeProvider><FeedbackPanel onClose={() => undefined} appearanceFeedback /></ScopeProvider></ThemeProvider>); }
const ok = () => ({ ok: true, json: async () => ({ items: [] }) });
beforeEach(() => { window.localStorage.clear(); auth.identity.account_id = "alice"; vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok())); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("风格反馈", () => {
  it("只选偏好也可提交，附上真实当前风格与版本，成功后清理草稿", async () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "bloom");
    vi.mocked(fetch).mockImplementation(async (_url, init) => init?.method === "POST" ? { ok: true, json: async () => ({ id: "fb-test" }) } as Response : ok() as Response);
    panel(); fireEvent.change(screen.getByLabelText(/你更喜欢哪种/), { target: { value: "clear" } });
    fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));
    await screen.findByText("已收到，反馈编号 fb-test");
    const post = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === "POST")!;
    const payload = JSON.parse(post[1]!.body as string);
    expect(payload.kind).toBe("suggestion");
    expect(payload.body).toContain("当前风格：C 柔雾花语");
    expect(payload.body).toContain("偏好风格：A 清透影像");
    expect(payload.body).toContain("界面版本：ui-v1");
    expect(payload.body).toContain("仅提交风格偏好");
    expect(readAppearanceDraft("alice").preference).toBe("");
  });
  it("网络失败保留意见与偏好，原内容重试复用幂等键，刷新历史失败不误报提交失败", async () => {
    let attempts = 0; let submitted = false;
    vi.mocked(fetch).mockImplementation(async (_url, init) => {
      if (init?.method === "POST") {
        attempts += 1;
        if (attempts === 1) throw new Error("network unavailable");
        submitted = true; return { ok: true, json: async () => ({ id: "fb-retry" }) } as Response;
      }
      if (submitted) throw new Error("history unavailable");
      return ok() as Response;
    });
    panel();
    fireEvent.change(screen.getByLabelText("发生了什么"), { target: { value: "更喜欢清楚的文字" } });
    fireEvent.change(screen.getByLabelText(/你更喜欢哪种/), { target: { value: "warm" } });
    fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));
    await screen.findByText(/network unavailable/);
    expect(readFeedbackDraft("alice")).toBe("更喜欢清楚的文字");
    expect(readAppearanceDraft("alice").preference).toBe("warm");
    fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));
    await screen.findByText("已收到，反馈编号 fb-retry");
    const posts = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts).toHaveLength(2);
    expect(posts[0][1]!.headers).toEqual(posts[1][1]!.headers);
    expect(posts[0][1]!.body).toBe(posts[1][1]!.body);
  });
  it("关闭再打开恢复本账号草稿，其他账号不可见，清理同时删除风格偏好", async () => {
    const view = panel();
    fireEvent.change(screen.getByLabelText("发生了什么"), { target: { value: "这是 Alice 的意见" } });
    fireEvent.change(screen.getByLabelText(/你更喜欢哪种/), { target: { value: "none" } });
    view.unmount(); const reopened = panel();
    expect((screen.getByLabelText("发生了什么") as HTMLTextAreaElement).value).toBe("这是 Alice 的意见");
    expect((screen.getByLabelText(/你更喜欢哪种/) as HTMLSelectElement).value).toBe("none");
    reopened.unmount(); auth.identity.account_id = "bob"; panel();
    await waitFor(() => expect((screen.getByLabelText("发生了什么") as HTMLTextAreaElement).value).toBe(""));
    expect((screen.getByLabelText(/你更喜欢哪种/) as HTMLSelectElement).value).toBe("");
    clearFeedbackDraft("alice"); clearAppearanceDraft("alice"); expect(readAppearanceDraft("alice").preference).toBe("");
  });
});
