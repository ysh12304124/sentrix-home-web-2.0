/**
 * 全屏弹层（引导/帮助/反馈）是否正在覆盖页面。
 *
 * 回忆页据此暂停自动翻页计时：引导打开期间不得在背后暗中换卡，关闭后再开始。
 */

import { createContext, useContext, type ReactNode } from "react";

const OverlayContext = createContext(false);

export function OverlayProvider({
  blocked,
  children,
}: {
  blocked: boolean;
  children: ReactNode;
}) {
  return <OverlayContext.Provider value={blocked}>{children}</OverlayContext.Provider>;
}

export function useOverlayBlocked(): boolean {
  return useContext(OverlayContext);
}
