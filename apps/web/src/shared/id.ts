/**
 * 客户端 ID 生成。
 *
 * 局域网是 HTTP，不是安全上下文：部分浏览器（尤其 Safari）里 `crypto.randomUUID`
 * 可能“存在但调用抛 NotSupportedError”。裸调用会让上传/提问在发请求前就静默失败。
 */
export function newClientId(prefix = "id"): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return `${prefix}_${crypto.randomUUID()}`;
    }
  } catch {
    // 非安全上下文：继续走下面的降级路径。
  }
  try {
    if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
      return `${prefix}_${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }
  } catch {
    // 继续走 Math.random 兜底。
  }
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 12)}`;
}
