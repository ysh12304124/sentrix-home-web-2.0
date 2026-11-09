import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import { isThemeEntry } from "./themeEntry";
import { useAuth } from "../AuthProvider";
import { isUiTheme, readTheme, THEME_STORAGE_KEY, type UiTheme } from "./themes";

interface ThemeValue {
  theme: UiTheme;
  selectTheme: (theme: UiTheme) => void;
  storageError: boolean;
  enabled: boolean;
}
const ThemeContext = createContext<ThemeValue>({ theme: "clear", selectTheme: () => undefined, storageError: false, enabled: false });

export function ThemeProvider({ children }: { children: ReactNode }) {
  const { adminOnly, identity } = useAuth();
  const [theme, setTheme] = useState<UiTheme>(readTheme);
  const [storageError, setStorageError] = useState(false);
  const enabled = isThemeEntry() && !adminOnly && identity?.role !== "admin";
  useLayoutEffect(() => {
    if (enabled) document.documentElement.dataset.uiTheme = theme;
    else delete document.documentElement.dataset.uiTheme;
    return () => { delete document.documentElement.dataset.uiTheme; };
  }, [enabled, theme]);
  useEffect(() => {
    if (!enabled) return;
    const sync = (event: StorageEvent) => {
      if (event.storageArea !== null && event.storageArea !== window.localStorage) return;
      if (event.key !== THEME_STORAGE_KEY && event.key !== null) return;
      setTheme(isUiTheme(event.newValue) ? event.newValue : "clear");
      setStorageError(false);
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, [enabled]);
  const selectTheme = useCallback((next: UiTheme) => {
    if (!enabled) return;
    setTheme(next);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
      setStorageError(false);
    } catch { setStorageError(true); }
  }, [enabled]);
  const value = useMemo(() => ({ theme, selectTheme, storageError, enabled }), [theme, selectTheme, storageError, enabled]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
export function useTheme(): ThemeValue { return useContext(ThemeContext); }
