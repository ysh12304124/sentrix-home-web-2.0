import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./client.ts";

// 手写 stub，不引 msw：这里要验的是 client 自己的三条行为，夹具越少越不容易
// 把「stub 的行为」当成「client 的行为」。
function jsonResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api 客户端", () => {
  it("超时会中断请求并报「请求超时」", async () => {
    // fetch 永不 resolve，只在 abort 时 reject —— 超时路径才会被走到。
    const fetchStub = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener("abort", () =>
            reject(Object.assign(new Error("aborted"), { name: "AbortError" })),
          );
        }),
    );
    vi.stubGlobal("fetch", fetchStub);

    await expect(api("/runs", { timeoutMs: 5, retries: 0 })).rejects.toThrow("请求超时：/runs");
  });

  it("GET 遇到 500 会重试一次，第二次成功时返回结果", async () => {
    const fetchStub = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(500, { error: "boom" }))
      .mockResolvedValueOnce(jsonResponse(200, { total: 0 }));
    vi.stubGlobal("fetch", fetchStub);

    const data = await api<{ total: number }>("/runs");

    expect(fetchStub).toHaveBeenCalledTimes(2);
    expect(data.total).toBe(0);
  });

  it("POST 默认不重试，500 直接失败（与 GET 的答案不同）", async () => {
    const fetchStub = vi.fn().mockResolvedValue(jsonResponse(500, { error: "boom" }));
    vi.stubGlobal("fetch", fetchStub);

    await expect(api("/runs/1/cancel", { method: "POST" })).rejects.toThrow("boom");
    expect(fetchStub).toHaveBeenCalledTimes(1);
  });

  it("错误体按 {error} 解析，而不是 HTTP 状态码", async () => {
    const fetchStub = vi
      .fn()
      .mockResolvedValue(jsonResponse(400, { error: "album_id and qa_set are required" }));
    vi.stubGlobal("fetch", fetchStub);

    await expect(api("/qa-dataset")).rejects.toThrow("album_id and qa_set are required");
  });

  it("响应不是有效 JSON 时报明确错误，且不重试", async () => {
    const fetchStub = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => "<html>not json</html>",
    });
    vi.stubGlobal("fetch", fetchStub);

    await expect(api("/runs")).rejects.toThrow("响应不是有效 JSON：/runs");
    expect(fetchStub).toHaveBeenCalledTimes(1);
  });
});

it("调用方取消会终止底层请求且不重试，也不误报超时", async () => {
  const caller = new AbortController();
  const fetchStub = vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
    init.signal?.addEventListener("abort", () => reject(new DOMException("cancelled", "AbortError")));
  }));
  vi.stubGlobal("fetch", fetchStub);
  const result = api("/runs/r", { signal: caller.signal });
  caller.abort();
  await expect(result).rejects.toMatchObject({ name: "AbortError", message: "请求已取消" });
  expect(fetchStub).toHaveBeenCalledTimes(1);
});


it("保留HTTP状态供已接受任务的初始化阶段识别404，GET404不重试", async () => {
  const fetchStub = vi.fn().mockResolvedValue(jsonResponse(404, { error: "'new-run'" }));
  vi.stubGlobal("fetch", fetchStub);
  await expect(api("/runs/new-run")).rejects.toMatchObject({ status: 404, message: "'new-run'" });
  expect(fetchStub).toHaveBeenCalledTimes(1);
});
