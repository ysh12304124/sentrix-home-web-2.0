import { isUiTheme, type ThemePreference } from "../../app/theme/themes";
import { feedbackDraftKey } from "./draft";

export function readAppearanceDraft(accountId: string): { preference: ThemePreference; kind: string } {
  try {
    const value = JSON.parse(window.localStorage.getItem(`${feedbackDraftKey(accountId)}.appearance`) ?? "{}");
    return { preference: isUiTheme(value.preference) || value.preference === "none" ? value.preference : "", kind: ["problem", "inaccurate", "suggestion"].includes(value.kind) ? value.kind : "" };
  } catch { return { preference: "", kind: "" }; }
}
export function saveAppearanceDraft(accountId: string, preference: ThemePreference, kind: string): void {
  try { window.localStorage.setItem(`${feedbackDraftKey(accountId)}.appearance`, JSON.stringify({ preference, kind })); }
  catch { /* Draft storage never blocks feedback. */ }
}

export function clearAppearanceDraft(accountId: string): void {
  try { window.localStorage.removeItem(`${feedbackDraftKey(accountId)}.appearance`); }
  catch { /* Storage availability does not affect the current page. */ }
}
