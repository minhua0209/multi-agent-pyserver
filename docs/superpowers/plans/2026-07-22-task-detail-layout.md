# Task Detail Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 优化任务详情页的信息层级与交互，并修复自动规划执行轮次的内容裁切和嵌套滚动遮挡。

**Architecture:** 保持 `TaskDetailModal` 的数据流和纵向内容顺序，仅调整渲染标记与 CSS。节点输出使用原生 `details` 展开，执行轮次由详情主体统一滚动，避免引入新状态和新依赖。

**Tech Stack:** React 19、TypeScript、Ant Design、Lucide React、Vitest、CSS

---

### Task 1: 建立任务详情视觉与滚动契约

**Files:**
- Modify: `frontend/src/styles.test.ts`

- [x] **Step 1: 写入失败测试**

新增断言，验证：

```ts
expect(appSource).toContain('className="task-question-icon"')
expect(appSource).toContain('className="execution-section-header"')
expect(appSource).toContain('<details className="subtask-node-output">')
expect(cssRule(".execution-section")).toMatch(/max-height:\s*none/)
expect(cssRule(".execution-scroll")).toMatch(/overflow-y:\s*visible/)
expect(cssRule(".subtask-node-output > div")).toMatch(/white-space:\s*pre-wrap/)
```

- [x] **Step 2: 确认测试因功能缺失而失败**

Run: `cd frontend && npm test -- src/styles.test.ts`

Expected: 新增任务详情断言失败，原有断言继续通过。

### Task 2: 优化任务详情信息层级和图标

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

- [x] **Step 1: 调整标题和概览卡渲染**

在任务标题区增加状态标签；根据 `creator`、`goal`、`deliverable`、`completion` 渲染现有 Lucide 图标；为原始诉求和任务清单标题增加图标。

- [x] **Step 2: 增加执行分区标题**

为自动规划与手动编排区域增加统一的图标、说明和数量标签，保持原有分支逻辑不变。

- [x] **Step 3: 调整主题化样式**

四项概览使用独立卡片、主题变量、稳定网格和轻量 hover 反馈；摘要区取消固定行高，允许内容自然展开。

- [x] **Step 4: 运行目标测试**

Run: `cd frontend && npm test -- src/styles.test.ts`

Expected: 新增概览和分区标题断言通过。

### Task 3: 修复执行轮次遮挡并增加输出展开

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/styles.test.ts`

- [x] **Step 1: 改造节点输出**

把固定高度的输出块替换为原生 `details`，摘要展示压缩预览，展开后使用 `white-space: pre-wrap` 显示完整输出。

- [x] **Step 2: 分离人工节点操作**

节点容器统一使用 `article`，仅人工待处理节点渲染独立按钮，避免在整卡按钮中嵌套展开控件。

- [x] **Step 3: 取消嵌套纵向滚动**

将 `.execution-section` 设置为自然高度，将 `.execution-scroll` 改为仅在必要时处理横向溢出；展开节点输出时轮次自然撑开。

- [x] **Step 4: 增加响应式规则**

在手机宽度下让并行节点改为单列，并确保操作按钮和输出详情不会超出节点卡片。

- [x] **Step 5: 运行目标测试**

Run: `cd frontend && npm test -- src/styles.test.ts`

Expected: 全部任务详情样式契约通过。

### Task 4: 完整验证

**Files:**
- Verify only

- [x] **Step 1: 运行前端测试**

Run: `cd frontend && npm test`

Expected: 所有 Vitest 测试通过。

- [x] **Step 2: 运行生产构建**

Run: `cd frontend && npm run build`

Expected: TypeScript 与 Vite 构建退出码为 0。

- [x] **Step 3: 浏览器验证**

验证深色和浅色主题下的任务详情，检查桌面、平板、手机宽度；执行轮次不存在内部纵向滚动、节点内容重叠或页面横向溢出。

- [x] **Step 4: 检查改动范围**

Run: `git diff --check && git status --short`

Expected: 无空白错误，仅包含本次文件和用户已有未跟踪文件；不执行 Git 提交。
