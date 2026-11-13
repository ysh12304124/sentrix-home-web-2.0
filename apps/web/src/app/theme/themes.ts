export const THEME_STORAGE_KEY = "sentrix.ui.theme.v1";
export const UI_VERSION = "ui-v1";
export const THEMES = [
  { id: "clear", code: "A", name: "清透影像", description: "雾白与青绿，清晰轻盈，让照片成为主角。" },
  { id: "warm", code: "B", name: "温暖相册", description: "暖白与陶土色，像翻开一本生活相册。" },
  { id: "bloom", code: "C", name: "柔雾花语", description: "柔雾与灰玫瑰，为日常添一点细腻。" },
] as const;
export type UiTheme = typeof THEMES[number]["id"];
export type ThemePreference = UiTheme | "none" | "";
export function isUiTheme(value: unknown): value is UiTheme {
  return THEMES.some((theme) => theme.id === value);
}
export function readTheme(): UiTheme {
  try {
    const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isUiTheme(saved) ? saved : "clear";
  } catch { return "clear"; }
}
export function themeLabel(theme: UiTheme): string {
  const item = THEMES.find((item) => item.id === theme)!;
  return `${item.code} ${item.name}`;
}
