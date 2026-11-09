/**
 * 路由可达性门禁——后端 `scripts/check_reachability.py` 的前端版。
 *
 * **三条断言，缺一不可。** 第三条是关键：没有它，「页面文件存在」与「路由挂载了它」
 * 可以各自为真而互不相干——那正是本项目「声称在检查、其实没有」那一族。前两条只保证
 * 从导航出发能走到东西，不保证写了的东西能被走到。
 */

import { describe, expect, it } from "vitest";

import { NAV_ITEMS, SYSTEM_ITEMS } from "./nav";
import { ROUTES } from "./router";

// Vite 在构建期枚举文件。用 glob 而不是手写清单：手写清单会随新增页面一起腐烂，
// 而这条断言的整个意义就是「新加的页面别变成孤儿」。
const PAGE_FILES = Object.keys(import.meta.glob("../features/*/*Page.tsx"));

/** 体验入口刻意隐藏的旧页面：文件仍在（旧实例继续用），但不挂路由。 */
const HIDDEN_PAGES = new Set([
  "OverviewPage",
  "KnowledgePage",
  "SearchPage",
  "SettingsPage",
  "StoriesPage",
]);

/** 文件名 → 组件名。`features/library/LibraryPage.tsx` → `LibraryPage`。 */
function componentNameOf(file: string): string {
  const base = file.split("/").pop() ?? file;
  return base.replace(/\.tsx$/, "");
}

describe("路由可达性", () => {
  it("每个导航项都有对应路由", () => {
    const missing = [...NAV_ITEMS, ...SYSTEM_ITEMS]
      .map((item) => item.path)
      .filter((path) => !(path in ROUTES));

    expect(missing, "这些导航项点下去没有页面").toEqual([]);
  });

  it("每条路由都指向一个真实组件", () => {
    const broken = Object.entries(ROUTES)
      .filter(([, Component]) => typeof Component !== "function")
      .map(([path]) => path);

    expect(broken, "这些路由指向的不是组件").toEqual([]);
  });

  it("每个页面文件都被路由表引用（没有孤儿页面）", () => {
    // 按组件名比对：ROUTES 的值就是页面组件，`Component.name` 是它的函数名。
    const mounted = new Set(Object.values(ROUTES).map((Component) => Component.name));
    const orphans = PAGE_FILES.map(componentNameOf)
      .filter((name) => !HIDDEN_PAGES.has(name))
      .filter((name) => !mounted.has(name));

    expect(orphans, "这些页面文件写好了但没挂到任何路由上").toEqual([]);
  });

  it("隐藏页面不挂在路由上", () => {
    const mounted = new Set(Object.values(ROUTES).map((Component) => Component.name));
    const leaked = [...HIDDEN_PAGES].filter((name) => mounted.has(name));
    expect(leaked, "这些页面应保持隐藏").toEqual([]);
  });

  it("路由数与可见页面文件数一致——多一条少一条都要在这里现形", () => {
    // 前三条各自都可以通过而这一条不过：例如两个路由指向同一个组件、另一个页面成孤儿。
    expect(Object.keys(ROUTES).length).toBe(NAV_ITEMS.length + SYSTEM_ITEMS.length);
    const visible = PAGE_FILES.map(componentNameOf).filter((name) => !HIDDEN_PAGES.has(name));
    expect(visible.length).toBe(Object.keys(ROUTES).length);
  });
});
