# Theme Toggle Design

## Goal

在现有 TaskHub 顶部工具栏增加深色/浅色主题切换按钮，使整套应用的 Ant Design 组件和自定义页面同步切换，并记住用户上次选择。

## Interaction

- 默认主题保持深色，避免改变现有用户首次访问体验。
- 深色模式显示太阳图标，提示“切换到浅色模式”；浅色模式显示月亮图标，提示“切换到深色模式”。
- 按钮使用图标、Tooltip 和 `aria-label`，不占用工具栏额外文字空间。
- 主题选择写入 `localStorage`，刷新后继续使用；存储不可用或值无效时回退深色。

## Architecture

- 新增 `frontend/src/themePreference.ts`，只负责主题值校验、读取、写入和切换。
- `frontend/src/main.tsx` 在 React 渲染前把已保存主题写入 `document.documentElement.dataset.theme`，减少首次渲染闪烁。
- `frontend/src/App.tsx` 持有主题状态，动态选择 Ant Design `darkAlgorithm` / `defaultAlgorithm`，并渲染切换按钮。
- `frontend/src/styles.css` 以 CSS 变量承载两套表面、文字、边框、阴影和流程画布颜色；组件布局不变。

## Verification

- 单元测试覆盖默认值、无效值、切换和存储异常。
- 样式契约覆盖两个 Ant Design 算法、主题按钮、浅色变量和壳层变量。
- 运行前端全量测试与生产构建。
- 在深色、浅色和移动端检查总览及发布页，确认无白底白字、文字重叠或横向溢出。

