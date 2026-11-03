/**
 * 体验入口的导航：恰六项。
 *
 * 旧概览、实体知识、资料检索、故事工作室与设备设置不再出现——它们要么是占位页，
 * 要么是维护入口。直接输入旧 URL 由前端显示“未开放”，后端网关的 403 仍是安全边界。
 */

export interface NavItem {
  readonly id: string;
  readonly path: string;
  readonly icon: string;
  readonly label: string;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { id: "memories", path: "/", icon: "◐", label: "回忆" },
  { id: "assistant", path: "/assistant", icon: "⌕", label: "提问" },
  { id: "timeline", path: "/timeline", icon: "↕", label: "时间线" },
  { id: "people", path: "/people", icon: "◎", label: "人物" },
  { id: "library", path: "/library", icon: "▦", label: "资料库" },
  { id: "imports", path: "/imports", icon: "↓", label: "添加资料" },
];

/** 管理入口只在管理员角色下显示；路由本身仍存在并由后端校验权限。 */
export const SYSTEM_ITEMS: readonly NavItem[] = [
  { id: "admin", path: "/admin", icon: "◌", label: "管理" },
];

export function activeNavItem(pathname: string): NavItem | undefined {
  const all = [...NAV_ITEMS, ...SYSTEM_ITEMS];
  return (
    all.find((item) => item.path === pathname) ??
    all.find((item) => item.path !== "/" && pathname.startsWith(`${item.path}/`))
  );
}
