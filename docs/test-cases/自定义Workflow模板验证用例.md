# 自定义 Workflow 模板验证用例

## 用例文件

`tests/test_workflows.py`

## 验证目标

验证系统支持新增和更新自定义 workflow 模板，并且任务可以通过 `metadata.execution_mode=workflow_template` 指定模板执行。模板定义持久化，编译/执行过程不影响原有动态分发模式。

## 覆盖用例

### `test_create_and_update_workflow_template_persists_definition`

验证点：

- `POST /api/v1/workflows` 可以创建模板。
- 模板保存完整 `definition.nodes` 和 `definition.edges`。
- `PUT /api/v1/workflows/{workflow_id}` 会在原模板上覆盖更新，不新增版本。
- `GET /api/v1/workflows/{workflow_id}` 能读取更新后的定义。

### `test_workflow_template_task_runs_agent_then_pauses_on_human_node`

验证点：

- 任务请求 metadata 指定：

```json
{
  "execution_mode": "workflow_template",
  "workflow_id": "workflow_xxx"
}
```

- 人工确认后，系统按模板节点执行：
  - agent 节点自动执行；
  - human 节点挂起等待人工输入。
- 人工提交 human 子任务结果后，任务继续执行并最终完成。
- 任务上下文包含 agent 输出和人工输出。

## 数据持久化

- 设置 `DATABASE_URL` 时，模板写入 `workflow_templates` 表。
- 不设置 `DATABASE_URL` 时，模板写入 `app/data/workflows.json`。
- compiled graph 不持久化，只在运行时基于模板定义构建。

## 最近验证结果

```text
2 passed, 1 warning
```
