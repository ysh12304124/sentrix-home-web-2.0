import { useEffect } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./themeEntry", () => ({ isThemeEntry: vi.fn(() => true) }));
const auth = vi.hoisted(() => ({ identity: { account_id: "alice", display_name: "体验用户", role: "user" }, adminOnly: false, logout: vi.fn(), markOnboardingSeen: vi.fn() }));
vi.mock("../AuthProvider", () => ({ useAuth: () => auth }));
vi.mock("../../shared/api/client", async (original) => ({ ...await original<typeof import("../../shared/api/client")>(), fetchHealth: vi.fn().mockResolvedValue({ status: "ok" }) }));
import { isThemeEntry } from "./themeEntry";
import { ThemeProvider, useTheme } from "./ThemeProvider";
import { THEME_STORAGE_KEY } from "./themes";
import { AppShell } from "../AppShell";
import { ScopeProvider } from "../ScopeProvider";
import { ToastProvider } from "../../shared/ui/Toast";

let mounts = 0;
function Page() {
  useEffect(() => { mounts += 1; }, []);
  return <label>尚未发送的问题<input defaultValue="" /></label>;
}
function shell() {
  return render(<MemoryRouter><ToastProvider><ThemeProvider><ScopeProvider><Routes><Route element={<AppShell />}><Route index element={<Page />} /></Route></Routes></ScopeProvider></ThemeProvider></ToastProvider></MemoryRouter>);
}
beforeEach(() => {
  window.localStorage.clear(); mounts = 0; auth.adminOnly = false; vi.mocked(isThemeEntry).mockReturnValue(true);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("三套外观与原页面状态", () => {
  it("恢复已保存风格，连续切换不卸载页面或清空输入，Esc 关闭并返回焦点", async () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "warm");
    shell();
    const input = screen.getByLabelText("尚未发送的问题") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "上次旅行的照片" } });
    const trigger = screen.getAllByRole("button", { name: "外观" }).at(-1)!;
    trigger.focus(); fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "界面风格" });
    fireEvent.click(within(dialog).getByRole("button", { name: /C · 柔雾花语/ }));
    expect(document.documentElement.dataset.uiTheme).toBe("bloom");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("bloom");
    expect(input.value).toBe("上次旅行的照片"); expect(mounts).toBe(1);
    fireEvent.click(within(dialog).getByRole("button", { name: /A · 清透影像/ }));
    expect(document.documentElement.dataset.uiTheme).toBe("clear");
    expect(mounts).toBe(1);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "界面风格" })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
  it("无效值回退 A，跨标签页同步，并响应清除设置", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "unknown"); shell();
    expect(document.documentElement.dataset.uiTheme).toBe("clear");
    fireEvent(window, new StorageEvent("storage", { key: THEME_STORAGE_KEY, newValue: "warm" }));
    expect(document.documentElement.dataset.uiTheme).toBe("warm");
    fireEvent(window, new StorageEvent("storage", { key: "unrelated", newValue: "bloom" }));
    expect(document.documentElement.dataset.uiTheme).toBe("warm");
    fireEvent(window, new StorageEvent("storage", { key: null, newValue: null }));
    expect(document.documentElement.dataset.uiTheme).toBe("clear");
  });
  it("本机存储失败时仍切换，且告知不能保存", () => {
    shell();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    fireEvent.click(screen.getAllByRole("button", { name: "外观" }).at(-1)!);
    fireEvent.click(screen.getByRole("button", { name: /B · 温暖相册/ }));
    expect(document.documentElement.dataset.uiTheme).toBe("warm");
    expect(screen.getByRole("status").textContent).toContain("本机暂无法保存");
  });
  it("独立管理员入口不启用用户主题", () => {
    auth.adminOnly = true;
    function Probe() { return <span>{useTheme().enabled ? "用户主题" : "管理界面"}</span>; }
    render(<ThemeProvider><Probe /></ThemeProvider>);
    expect(screen.getByText("管理界面")).toBeTruthy();
    expect(document.documentElement.dataset.uiTheme).toBeUndefined();
  });
  it("从外观面板进入体验建议，切换风格本身不提交反馈", async () => {
    shell();
    fireEvent.click(screen.getAllByRole("button", { name: "外观" }).at(-1)!);
    fireEvent.click(screen.getByRole("button", { name: /C · 柔雾花语/ }));
    fireEvent.click(screen.getByRole("button", { name: "反馈界面感受" }));
    expect((screen.getByLabelText("类型") as HTMLSelectElement).value).toBe("suggestion");
    expect((screen.getByLabelText(/你更喜欢哪种/) as HTMLSelectElement).value).toBe("");
    await waitFor(() => expect(screen.getByText(/当前界面：C 柔雾花语/)).toBeTruthy());
    expect(vi.mocked(fetch).mock.calls.filter(([url, init]) => url === "/control/v1/feedback" && init?.method === "POST")).toHaveLength(0);
  });
});

it("非 8210 入口隐藏外观并保留原反馈表单和正文", async () => {
  vi.mocked(isThemeEntry).mockReturnValue(false);
  vi.mocked(fetch).mockImplementation(async (_url, init) => init?.method === "POST" ? { ok: true, json: async () => ({ id: "existing-feedback" }) } as Response : { ok: true, json: async () => ({ items: [] }) } as Response);
  shell();
  expect(document.documentElement.dataset.uiTheme).toBeUndefined();
  expect(screen.queryByRole("button", { name: "外观" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "反馈" }));
  expect(screen.queryByLabelText(/你更喜欢哪种/)).toBeNull();
  const field = screen.getByLabelText("发生了什么") as HTMLTextAreaElement;
  expect(field.maxLength).toBe(4000);
  fireEvent.change(field, { target: { value: "原反馈正文" } });
  fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));
  await screen.findByText("已收到，反馈编号 existing-feedback");
  const post = vi.mocked(fetch).mock.calls.find(([url, init]) => url === "/control/v1/feedback" && init?.method === "POST")!;
  expect(JSON.parse(post[1]!.body as string).body).toBe("原反馈正文");
});
