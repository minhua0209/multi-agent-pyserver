# 并行 Agent 子任务执行验证用例

## 用例文件

`tests/test_task_graph.py`

## 用例名称

`test_parallel_agent_subtasks_execute_concurrently_and_merge_context_in_plan_order`

## 验证目标

验证同一轮 `execution_mode=parallel` 时，多个无依赖 agent 子任务会并发执行，同时上下文合并仍按分发计划中的子任务顺序进行，而不是按线程完成顺序写入。

## 测试步骤

1. 创建一个处理 agent。
2. Mock 分发 agent，让它生成两个独立 agent 子任务：
   - `subtask_slow`
   - `subtask_fast`
3. 两个子任务执行函数都休眠 `0.2` 秒。
4. 运行 `TaskGraphRunner`。
5. 验证总耗时小于 `0.35` 秒，证明不是顺序执行。
6. 验证 `context.summary` 为：

```text
slow output
fast output
```

说明上下文按计划顺序合并。

## 最近验证结果

```text
1 passed
```
