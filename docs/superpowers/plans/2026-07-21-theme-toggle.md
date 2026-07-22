# Theme Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 TaskHub 增加可持久化的深色/浅色主题切换按钮。

**Architecture:** 主题偏好由独立纯函数模块管理；入口文件在渲染前应用主题，应用组件动态配置 Ant Design 算法，CSS 变量提供两套自定义表面。保持默认深色与现有页面结构不变。

**Tech Stack:** React 19、TypeScript 5.9、Ant Design 6、Lucide、Vitest、CSS variables

---

### Task 1: 主题偏好模块

**Files:**
- Create: `frontend/src/themePreference.ts`
- Create: `frontend/src/themePreference.test.ts`

- [x] **Step 1: 写入失败测试**

```ts
expect(resolveThemePreference(null)).toBe("dark")
expect(resolveThemePreference("light")).toBe("light")
expect(toggleTheme("dark")).toBe("light")
expect(toggleTheme("light")).toBe("dark")
```

- [x] **Step 2: 验证测试失败**

Run: `npm --prefix frontend test -- themePreference.test.ts`

Expected: FAIL，因为 `themePreference.ts` 尚不存在。

- [x] **Step 3: 实现最小纯函数与容错存储**

```ts
export type AppTheme = "dark" | "light"
export const THEME_STORAGE_KEY = "taskhub-theme"
export function resolveThemePreference(value?: string | null): AppTheme
export function readThemePreference(storage: Pick<Storage, "getItem">): AppTheme
export function writeThemePreference(storage: Pick<Storage, "setItem">, theme: AppTheme): void
export function toggleTheme(theme: AppTheme): AppTheme
```

- [x] **Step 4: 验证目标测试通过**

Run: `npm --prefix frontend test -- themePreference.test.ts`

Expected: PASS。

### Task 2: 应用与按钮接入

**Files:**
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.test.ts`

- [x] **Step 1: 写入失败契约测试**

断言入口预应用主题，应用同时使用 `darkAlgorithm` 与 `defaultAlgorithm`，并存在带“切换到浅色模式/切换到深色模式”标签的图标按钮。

- [x] **Step 2: 验证契约测试失败**

Run: `npm --prefix frontend test -- styles.test.ts themePreference.test.ts`

Expected: FAIL，因为应用仍固定使用深色算法且没有主题按钮。

- [x] **Step 3: 接入主题状态**

在 `main.tsx` 渲染前读取偏好并设置 `document.documentElement.dataset.theme`；在 `App.tsx` 中切换算法、Menu 主题与按钮图标，并持久化选择。

- [x] **Step 4: 验证契约测试通过**

Run: `npm --prefix frontend test -- styles.test.ts themePreference.test.ts`

Expected: PASS。

### Task 3: 浅色变量与完整验证

**Files:**
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/styles.test.ts`

- [x] **Step 1: 写入浅色变量失败测试**

断言 `html[data-theme="light"]` 定义浅色表面，页面背景、导航、工具栏和流程画布通过变量切换。

- [x] **Step 2: 实现浅色主题变量**

增加浅色表面、文字、边框、阴影、画布和状态文字变量；将壳层硬编码深色改为变量，不修改布局。

- [x] **Step 3: 运行目标与全量测试**

Run: `npm --prefix frontend test -- themePreference.test.ts styles.test.ts`

Run: `npm --prefix frontend test`

- [x] **Step 4: 运行生产构建与视觉检查**

Run: `npm --prefix frontend run build`

分别检查桌面深色、桌面浅色、移动浅色的总览和发布页；检查控制台与横向溢出。

验证结果：深浅切换与刷新持久化正常；桌面和移动端文档宽度未溢出；发布页浅色表单正常；控制台无错误。

> 根据仓库约束，本计划不包含 Git 提交步骤。
