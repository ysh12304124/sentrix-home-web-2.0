import { describe, expect, it } from "vitest";
import { isThemeEntry } from "./themeEntry";

describe("仅在 8210 启用三主题", () => {
  it("生产构建只允许 8210，8191、8220、8772 和普通端口保持原样", () => {
    expect(isThemeEntry({ hostname: "192.168.0.153", port: "8210" }, false)).toBe(true);
    for (const port of ["8191", "8220", "8772", "8091", "", "80", "443", "4274"]) {
      expect(isThemeEntry({ hostname: "192.168.1.96", port }, false)).toBe(false);
    }
  });
  it("开发预览只允许明确的本机端口，局域网其它端口不开启", () => {
    expect(isThemeEntry({ hostname: "127.0.0.1", port: "4274" }, true)).toBe(true);
    expect(isThemeEntry({ hostname: "localhost", port: "4275" }, true)).toBe(true);
    expect(isThemeEntry({ hostname: "192.168.1.96", port: "4274" }, true)).toBe(false);
    expect(isThemeEntry({ hostname: "localhost", port: "8191" }, true)).toBe(false);
  });
});
