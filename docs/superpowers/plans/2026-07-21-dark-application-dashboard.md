# Dark Application Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将整套 TaskHub 前端切换为深色主题，并交付使用真实任务数据的新版协同总览。

**Architecture:** 使用 Ant Design `darkAlgorithm` 处理组件主题，以 CSS 变量和少量分组覆盖统一现有自定义组件。将总览统计与时间筛选抽到纯函数模块，React 页面只负责状态和渲染，避免继续在大型 `App.tsx` 中堆积数据计算。

**Tech Stack:** React 19、TypeScript 5.9、Vite 8、Ant Design 6、Lucide、Vitest

## Global Constraints

- 不修改后端接口或持久化数据。
- 不编造 Token、费用、延迟或在线率。
- 不提交 Git。
- 保持其他页面业务流程和组件结构不变。

---

### Task 1: 总览数据口径与刷新策略

**Files:**
- Create: `frontend/src/overviewSummary.ts`
- Create: `frontend/src/overviewSummary.test.ts`
- Modify: `frontend/src/pageRefresh.ts`
- Modify: `frontend/src/pageRefresh.test.ts`

- [x] **Step 1: 写入失败测试**

覆盖全部/今日/近七日筛选、六种任务状态、风险任务排序、完成率、无效日期与七日趋势；补充进入 `overview` 时刷新 `tasks`、`humanSubtasks`、`agents` 的断言。

- [x] **Step 2: 验证测试失败**

Run: `npm --prefix frontend test -- overviewSummary.test.ts pageRefresh.test.ts`

Expected: FAIL，统计模块不存在且总览刷新目标为空。

- [x] **Step 3: 实现最小纯函数**

导出 `OverviewRange`、`filterOverviewTasks`、`buildOverviewSummary`、`buildOverviewTrend` 和 `overviewRiskTasks`，所有日期比较使用本地自然日边界。

- [x] **Step 4: 验证目标测试通过**

Run: `npm --prefix frontend test -- overviewSummary.test.ts pageRefresh.test.ts`

Expected: PASS。

### Task 2: 新版协同总览

**Files:**
- Modify: `frontend/src/App.tsx`

- [x] **Step 1: 使用纯函数接入筛选状态**

总览持有 `all / today / 7d` 范围状态，并从纯函数获取指标、趋势和风险任务。

- [x] **Step 2: 构建仪表盘结构**

增加八项指标、范围控制、任务状态分布、七日趋势、风险任务、最近任务和最近事件；继续使用现有任务/事件组件和发布入口。

- [x] **Step 3: 保持数据真实性**

Agent 仅展示数量；取消任务单独展示但不计入风险；无数据时显示明确空态。

### Task 3: 全局深色主题和响应式样式

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/styles.test.ts`

- [x] **Step 1: 写入失败样式契约**

断言应用使用 `theme.darkAlgorithm`，根变量为深色表面，并存在总览四列、平板两列和移动单列规则。

- [x] **Step 2: 验证样式测试失败**

Run: `npm --prefix frontend test -- styles.test.ts`

Expected: FAIL，当前仍为浅色变量和五列旧指标。

- [x] **Step 3: 启用 Ant Design 深色算法**

在 `ConfigProvider` 使用深色算法与深色 Layout、Menu、Card、Table、Modal 等 token。

- [x] **Step 4: 统一自定义组件表面**

调整全局变量、页面背景、工具栏、卡片、表格、弹窗、任务详情、人工确认、流程画布和管理页的硬编码浅色表面；保留状态色语义。

- [x] **Step 5: 完成总览响应式布局**

桌面四列指标，1024px 以下两列，767px 以下单列；趋势、任务和事件区域在窄屏按阅读顺序堆叠。

### Task 4: 完整验证

**Files:**
- Verify only

- [x] **Step 1: 运行目标测试**

Run: `npm --prefix frontend test -- overviewSummary.test.ts pageRefresh.test.ts styles.test.ts`

- [x] **Step 2: 运行前端全量测试**

Run: `npm --prefix frontend test`

- [x] **Step 3: 运行生产构建**

Run: `npm --prefix frontend run build`

- [x] **Step 4: 启动本地服务并截图**

分别以 1440×1000、1024×768、390×844 检查总览和至少一个表单/详情页面，确认无空白画面、文字重叠或横向溢出。

验证结果：前端 20 个测试文件、131 个用例通过；生产构建成功；三档总览截图与移动端发布表单检查通过，控制台无错误。
