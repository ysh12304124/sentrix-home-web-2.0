/**
 * PhotoBench 的 HTTP 客户端。逐条移植 1.0 `App.vue:287-322` 的 `api()` / `post()`。
 *
 * 三处**不能想当然**的地方，都有依据：
 *
 *   1. `BASE = "/api/v1"`，与主 API 同前缀（T924，2026-10-07）。PhotoBench 是独立包，前缀在
 *      `apps/photobench/src/photobench/main.py` 的 `API_PREFIX` 单点定义；
 *      `/api/v1` 是主产品（`apps/api`）的，两者不能混。**也刻意不复用
 *      `apps/web/src/shared/api/client.ts`**——那个 `BASE="/api/v1"` 且按 sentrix 的
 *      `{code,message,detail}` 解析错误体；改它等于改坏主产品客户端。
 *   2. 错误体是 `{"error": "..."}`，不是 `{detail}`。见
 *      `apps/photobench/src/photobench/api/errors.py`。
 *   3. GET 默认重试 1 次、退避 250ms×attempt；`http >= 500` 与网络层失败（`TypeError`）
 *      与超时（`AbortError`）都可重试，业务错误（4xx）不重试。
 *
 * 调用方传**相对路径**（`"/runs"`），`BASE` 在这里拼一次——1.0 的调用点四处重复写
 * `"/api/v1/..."`，前缀一改就得全改；`media.ts` 也 import 这个 `BASE` 拼 `<img>` 地址。
 */

/** PhotoBench 自己的前缀（`main.py` 的 `API_PREFIX`）。 */
export const BASE = "/api/v1";

export interface ApiOptions extends RequestInit {
  /** 单次请求的超时（毫秒）。默认 10000，与 1.0 相同。 */
  timeoutMs?: number;
  /** 覆盖默认重试次数；GET 默认 1，其余默认 0。 */
  retries?: number;
}

/** 网络层错误：带一个 `retryable` 标记（1.0 也是这么挂在 Error 上的）。 */
export type RequestError = Error & { retryable?: boolean; status?: number };

/**
 * 发一个请求并解析 JSON。
 *
 * 用 `AbortController` 计时；超时抛 `AbortError`，最终对外统一成「请求超时：<path>」，
 * 与 1.0 的文案一致。
 */
export async function api<T = unknown>(path: string, options: ApiOptions = {}): Promise<T> {
  const { timeoutMs = 10000, retries, ...requestOptions } = options;
  const method = String(requestOptions.method || "GET").toUpperCase();
  const retryCount = retries ?? (method === "GET" ? 1 : 0);
  let lastError: unknown;
  for (let attempt = 0; attempt <= retryCount; attempt += 1) {
    const controller = new AbortController();
    const external = requestOptions.signal;
    if (external?.aborted) throw new DOMException("请求已取消", "AbortError");
    const cancel = () => controller.abort();
    external?.addEventListener("abort", cancel, { once: true });
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(`${BASE}${path}`, {
        headers: { "content-type": "application/json", ...(requestOptions.headers || {}) },
        ...requestOptions,
        signal: controller.signal,
      });
      const text = await response.text();
      let data: unknown = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        throw new Error(`响应不是有效 JSON：${path}`);
      }
      if (!response.ok) {
        const httpError = Object.assign(
          new Error((data as { error?: string } | null)?.error || `HTTP ${response.status}`),
          { retryable: response.status >= 500, status: response.status },
        );
        throw httpError;
      }
      return data as T;
    } catch (requestError) {
      if (external?.aborted) throw new DOMException("请求已取消", "AbortError");
      lastError = requestError;
      const candidate = requestError as RequestError;
      const retryable =
        candidate?.name === "AbortError" ||
        requestError instanceof TypeError ||
        candidate?.retryable;
      if (attempt >= retryCount || !retryable) break;
      await new Promise((resolve) => window.setTimeout(resolve, 250 * (attempt + 1)));
    } finally {
      window.clearTimeout(timer);
      external?.removeEventListener("abort", cancel);
    }
  }
  const failure = lastError as RequestError | undefined;
  throw Object.assign(new Error(
    failure?.name === "AbortError" ? `请求超时：${path}` : failure?.message || `请求失败：${path}`,
  ), { status: failure?.status });
}

/** JSON POST（1.0 `post` `App.vue:321`）。 */
export function post<T = unknown>(path: string, body: unknown): Promise<T> {
  return api<T>(path, { method: "POST", body: JSON.stringify(body) });
}
