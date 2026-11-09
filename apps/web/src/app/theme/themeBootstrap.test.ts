/// <reference types="node" />
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";

const script = readFileSync(`${process.cwd()}/public/theme-init.js`, "utf8");
function boot(port: string, mode = "production", stored = "bloom") {
  const attributes: Record<string, string> = {};
  runInNewContext(script, {
    window: { location: { hostname: "127.0.0.1", port }, localStorage: { getItem: () => stored } },
    document: { currentScript: { dataset: { previewMode: mode } }, documentElement: { setAttribute: (key: string, value: string) => { attributes[key] = value; } } },
  });
  return attributes;
}
describe("首屏主题只作用于指定入口", () => {
  it("8210 在页面绘制前恢复已选主题", () => { expect(boot("8210")["data-ui-theme"]).toBe("bloom"); });
  it("8191、8220、8772 和其它生产端口完全不设置主题属性", () => {
    for (const port of ["8191", "8220", "8772", "4274", ""]) expect(boot(port)).toEqual({});
  });
  it("明确的开发预览可切换，已保存的无效主题回退 A", () => {
    expect(boot("4274", "development")["data-ui-theme"]).toBe("bloom");
    expect(boot("8210", "production", "invalid")["data-ui-theme"]).toBe("clear");
  });
});
