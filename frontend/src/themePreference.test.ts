import { describe, expect, it, vi } from "vitest"

import {
  THEME_STORAGE_KEY,
  getThemeStorage,
  readThemePreference,
  resolveThemePreference,
  toggleTheme,
  writeThemePreference,
} from "./themePreference"


describe("theme preference", () => {
  it("defaults missing or invalid values to dark", () => {
    expect(resolveThemePreference(null)).toBe("dark")
    expect(resolveThemePreference(undefined)).toBe("dark")
    expect(resolveThemePreference("system")).toBe("dark")
  })

  it("accepts both supported themes", () => {
    expect(resolveThemePreference("dark")).toBe("dark")
    expect(resolveThemePreference("light")).toBe("light")
  })

  it("toggles between dark and light", () => {
    expect(toggleTheme("dark")).toBe("light")
    expect(toggleTheme("light")).toBe("dark")
  })

  it("reads and writes the persisted preference", () => {
    const getItem = vi.fn(() => "light")
    const setItem = vi.fn()

    expect(readThemePreference({ getItem })).toBe("light")
    writeThemePreference({ setItem }, "dark")

    expect(getItem).toHaveBeenCalledWith(THEME_STORAGE_KEY)
    expect(setItem).toHaveBeenCalledWith(THEME_STORAGE_KEY, "dark")
  })

  it("falls back safely when storage is unavailable", () => {
    expect(readThemePreference({ getItem: () => { throw new Error("blocked") } })).toBe("dark")
    expect(() => writeThemePreference({ setItem: () => { throw new Error("blocked") } }, "light")).not.toThrow()
  })

  it("handles a browser that rejects access to the localStorage property", () => {
    vi.stubGlobal("window", Object.create(null, {
      localStorage: { get: () => { throw new Error("blocked") } },
    }))

    expect(getThemeStorage()).toBeNull()
    vi.unstubAllGlobals()
  })
})
