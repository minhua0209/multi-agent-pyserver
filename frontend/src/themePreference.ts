export type AppTheme = "dark" | "light"

export const THEME_STORAGE_KEY = "taskhub-theme"


export function getThemeStorage(): Storage | null {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

export function resolveThemePreference(value?: string | null): AppTheme {
  return value === "light" ? "light" : "dark"
}

export function readThemePreference(storage: Pick<Storage, "getItem"> | null): AppTheme {
  if (!storage) return "dark"
  try {
    return resolveThemePreference(storage.getItem(THEME_STORAGE_KEY))
  } catch {
    return "dark"
  }
}

export function writeThemePreference(storage: Pick<Storage, "setItem"> | null, theme: AppTheme): void {
  if (!storage) return
  try {
    storage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // Theme switching remains available when storage is blocked.
  }
}

export function toggleTheme(theme: AppTheme): AppTheme {
  return theme === "dark" ? "light" : "dark"
}
