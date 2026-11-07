import { themeLabel, UI_VERSION, type ThemePreference, type UiTheme } from "../../app/theme/themes";

export function feedbackBody(body: string, theme: UiTheme, preference: ThemePreference, enabled = true): string {
  if (!enabled) return body.trim();
  const preferred = preference === "" ? "未选择" : preference === "none" ? "暂时没有偏好" : themeLabel(preference);
  return `【界面风格反馈】\n当前风格：${themeLabel(theme)}\n偏好风格：${preferred}\n界面版本：${UI_VERSION}\n\n用户意见：\n${body.trim() || "仅提交风格偏好"}`;
}
