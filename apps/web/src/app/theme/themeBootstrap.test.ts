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

it.each(["clear", "warm", "bloom"])("%s 主题选中人物标签保持可读对比", (theme) => {
  const styles = ["tokens", "family", "experience", "themes"].map((name) => {
    const element = document.createElement("style");
    element.textContent = readFileSync(`${process.cwd()}/src/styles/${name}.css`, "utf8");
    document.head.append(element);
    return element;
  });
  const shell = document.createElement("main");
  shell.className = "app-shell";
  shell.innerHTML = '<button class="family-tab active">人物</button>';
  document.body.append(shell);
  document.documentElement.dataset.uiTheme = theme;
  try {
    const root = getComputedStyle(document.documentElement);
    const style = getComputedStyle(shell.querySelector("button")!);
    const resolve = (value: string): string => {
      const variable = /^var\((--[\w-]+)\)$/.exec(value.trim());
      return variable ? resolve(root.getPropertyValue(variable[1])) : value.trim();
    };
    const luminance = (hex: string): number => {
      expect(hex).toMatch(/^#[\da-f]{3}([\da-f]{3})?$/i);
      const full = hex.length === 4 ? hex.slice(1).split("").map((x) => x + x).join("") : hex.slice(1);
      const channels = [0, 2, 4].map((offset) => {
        const c = parseInt(full.slice(offset, offset + 2), 16) / 255;
        return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
      });
      return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
    };
    const foreground = luminance(resolve(style.color));
    const background = luminance(resolve(style.background));
    expect((Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05)).toBeGreaterThanOrEqual(4.5);
  } finally {
    shell.remove();
    styles.forEach((style) => style.remove());
    delete document.documentElement.dataset.uiTheme;
  }
});
