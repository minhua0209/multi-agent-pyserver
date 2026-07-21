# Confirmed Initial Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新确认任务在首次执行前持久化完整的 `initial_context` 快照。

**Architecture:** 在 `TaskService.confirm_task_details` 中完成确认字段和流程初始摘要处理后，对当前 `TaskContext` 做深拷贝并赋值给 `initial_context`。重跑与执行服务继续使用既有接口，不增加兼容历史数据的分支。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、pytest

## Global Constraints

- 只修复修改后新确认的任务。
- 不回填历史任务。
- `initial_context` 固化后保持不可变。

---

### Task 1: 确认后固化初始上下文

**Files:**
- Modify: `app/services/task_service.py`
- Test: `tests/test_workflows.py`
- Modify: `docs/iterations/迭代记录.md`

**Interfaces:**
- Consumes: `Task.context: TaskContext`
- Produces: `Task.initial_context: TaskContext`

- [x] **Step 1: 写入失败回归测试**

创建流程模板任务并以异步方式确认，断言确认响应中的 `initial_context.summary` 包含任务名称和任务诉求，且首次执行的 `context_snapshot` 与 `initial_context` 一致。

- [x] **Step 2: 验证测试失败**

Run: `.venv/bin/pytest tests/test_workflows.py::test_workflow_confirmation_snapshots_initial_context -q`

Expected: FAIL，当前 `initial_context.summary` 为空。

- [x] **Step 3: 实现最小修复**

在 `confirm_task_details` 生成流程初始摘要后、调用 `execution_service.create_initial` 前增加：

```python
task.initial_context = task.context.model_copy(deep=True)
```

- [x] **Step 4: 验证目标和全量测试**

Run: `.venv/bin/pytest tests/test_workflows.py::test_workflow_confirmation_snapshots_initial_context -q`

Expected: PASS。

Run: `.venv/bin/pytest -q`

Expected: 全量测试通过。

- [x] **Step 5: 更新迭代记录**

在 `docs/iterations/迭代记录.md` 记录确认后固化初始上下文的原因、范围和验证结果。
