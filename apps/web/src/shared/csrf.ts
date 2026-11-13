/**
 * 两个入口共用同一浏览器：8210 用 `sentrix_control_*`，8220 用 `sentrix_admin_*`。
 * CSRF 必须按当前入口的 cookie 名精确取值；用正则同时匹配两个命名空间会把另一个
 * 会话的 token 发过来，服务端只会回 "csrf token missing or invalid"。
 */

let cookieNamespace = "sentrix_control";

export function setCookieNamespace(value: string): void {
  if (value !== "") cookieNamespace = value;
}

export function csrfToken(): string {
  if (typeof document === "undefined") return "";
  const target = `${cookieNamespace}_csrf`;
  for (const part of document.cookie.split("; ")) {
    const separator = part.indexOf("=");
    if (separator < 0) continue;
    if (part.slice(0, separator) === target) {
      return decodeURIComponent(part.slice(separator + 1));
    }
  }
  return "";
}
