# Confirmed Initial Context Design

## Goal

任务完成人工确认后，将正式执行前的上下文固化到 `task.initial_context`，保证之后的新任务重跑能够恢复任务名称、任务诉求及流程启动信息。

## Scope

- 只影响修改后新确认的任务。
- 不回填历史任务的空 `initial_context`。
- 不改变正常执行期间 `task.context` 的逐轮更新逻辑。
- 不改变重跑接口，重跑仍从 `task.initial_context` 创建新执行。

## Data Flow

1. 人工确认任务名称、描述、合同和默认操作人。
2. 流程模板任务生成正式的 `task.context.summary`。
3. 将完整 `task.context` 深拷贝到 `task.initial_context`。
4. 创建首次 `TaskExecution`，随后开始任务执行。

## Error Handling

快照使用 Pydantic 模型的深拷贝，不引入新的失败分支。后续任务执行对 `task.context` 的修改不得影响 `task.initial_context`。

## Verification

- 流程模板任务确认后，`initial_context.summary` 包含任务名称和任务诉求。
- 首次执行记录的起始上下文与 `initial_context` 一致。
- 后续修改当前上下文不会改变 `initial_context`。
- 后端全量测试通过。
