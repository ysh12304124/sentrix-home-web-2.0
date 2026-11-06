/**
 * 顶部视图切换。移植 1.0 `App.vue:2840-2843` 的 `<nav class="view-tabs">` 两个按钮。
 *
 * **没有 URL 路由语义**，所以这里只是一个受控的按钮组：1.0 用 `activeView` 一个 ref
 * 切两个视图（模板 `:2844` / `:3676` 两条 `v-if`），没有深链、没有前进后退、
 * 没有按 URL 恢复视图。加 react-router 是净增行为，不算迁移（计划 §2 裁决）。
 */

/** 两个顶层视图。1.0 `activeView` 的取值就是这两个字符串。 */
export type ActiveView = "runs" | "qa-browser" | "capacity";

export interface ViewTabsProps {
  activeView: ActiveView;
  onSelect: (view: ActiveView) => void;
}

export function ViewTabs({ activeView, onSelect }: ViewTabsProps) {
  return (
    <nav className="view-tabs">
      <button
        type="button"
        className={`view-tab${activeView === "runs" ? " active" : ""}`}
        onClick={() => onSelect("runs")}
      >
        评测运行
      </button>
      <button
        type="button"
        className={`view-tab${activeView === "qa-browser" ? " active" : ""}`}
        onClick={() => onSelect("qa-browser")}
      >
        QA 数据集浏览
      </button>
      <button type="button" className={`view-tab${activeView === "capacity" ? " active" : ""}`} onClick={() => onSelect("capacity")}>
        并发实验
      </button>
    </nav>
  );
}
