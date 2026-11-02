/**
 * 路由表：六项体验入口 + 管理员页。
 *
 * 登录不是路由：未登录时由 AuthProvider 直接渲染登录屏，不进入壳层。
 */

import type { ComponentType } from "react";

import { AdminPage } from "../features/admin/AdminPage";
import { AssistantPage } from "../features/assistant/AssistantPage";
import { ImportsPage } from "../features/imports/ImportsPage";
import { LibraryPage } from "../features/library/LibraryPage";
import { MemoryPage } from "../features/memories/MemoryPage";
import { PeoplePage } from "../features/people/PeoplePage";
import { TimelinePage } from "../features/timeline/TimelinePage";
import { AppShell } from "./AppShell";

export const ROUTES: Readonly<Record<string, ComponentType>> = {
  "/": MemoryPage,
  "/imports": ImportsPage,
  "/assistant": AssistantPage,
  "/people": PeoplePage,
  "/library": LibraryPage,
  "/timeline": TimelinePage,
  "/admin": AdminPage,
};

export const SHELL = AppShell;

/** 旧书签/手输 URL 的落地页：不暴露内部信息，也不假装功能存在。 */
export function NotAvailable() {
  return (
    <section className="view-root">
      <h1>该页面未开放</h1>
      <p>体验入口只包含回忆、导入、问答、人物、资料库和时间线。</p>
      <a href="/">返回回忆</a>
    </section>
  );
}
