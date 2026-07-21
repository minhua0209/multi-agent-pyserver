# 文件交付物确定性落盘设计

## 背景

当前任务契约只用自然语言描述交付物。规划器和执行 Agent 没有收到确认后的完整契约，普通模型输出只会登记为 `TEXT Artifact`；只有模型主动生成合法的 `file_write` 工具调用，文件才会写入磁盘。完成判定又只要求至少存在一个有效 Artifact，因此“页面上任务成功”并不等于“文档已经保存在 `agent_outputs`”。

本设计把文档交付拆成两个独立判断：

- 文档是否真实存在，由应用代码确定性写入和校验。
- 文档内容是否满足交付要求，由现有交付要求和成功标准评估。

## 目标

- 人工确认时明确本次交付是页面文本还是文件。
- 文件交付首期支持 Markdown 和纯文本。
- 文件正文由应用层写入统一的 `agent_outputs` 根目录，不依赖模型把长正文封装进工具调用 JSON。
- 只有当前 execution 存在非空、路径合法、checksum 有效的文件 Artifact 时，文件交付任务才允许成功。
- 模型或并行子任务失败时，保留已经完成的轮次、工具结果、文件和 Artifact。
- 任务重跑产生独立文件，不覆盖历史 execution 的交付物。

## 非目标

- 本期不生成 PDF 或 DOCX。
- 本期不支持一个任务声明多种不同格式的多个最终交付文件。
- 不通过关键词在运行时猜测交付类型。
- 不改变邮件、HTTP、数据库等外部副作用工具的语义。

## 契约模型

在 `TaskDraft`、`TaskContractInput` 和 `TaskContract` 增加以下字段：

```python
deliverable_kind: Literal["text", "file"] = "text"
deliverable_format: Literal["markdown", "text"] | None = None
deliverable_filename: str = ""
```

约束如下：

- `deliverable_kind=text` 时，`deliverable_format` 必须为空。
- `deliverable_kind=file` 时，`deliverable_format` 必须是 `markdown` 或 `text`。
- `deliverable_filename` 可为空；非空时只能是文件名，不能包含目录、绝对路径或 `..`。
- 系统根据格式强制扩展名：Markdown 使用 `.md`，纯文本使用 `.txt`；文件名没有扩展名时自动补齐，已有不匹配扩展名时直接拒绝。
- 后端默认 `deliverable_kind=text`，保证历史 JSON 和旧客户端仍可读取；新确认页始终展示并提交明确选择。
- 意图识别模型可以提供建议值，但只有人工确认后的 `TaskContract` 才是执行依据。

任务仍使用现有的 `deliverable_goal` 和 `deliverable_requirements` 描述内容要求，新增字段只描述物理交付形式。

## 前端确认

确认页在“交付物目标”之后增加：

- 交付方式：页面文本、文件。
- 文件格式：Markdown、纯文本；仅在文件模式展示。
- 文件名：可选；仅在文件模式展示。

提交前执行与后端一致的校验。详情页展示最终确认的交付方式、格式和文件名，避免用户只能从自然语言推断。

## 模型输入与输出协议

轮次规划器和子任务执行 Agent 都接收完整的 `task.contract`。

文件交付时，执行 Agent 遵循以下协议：

- 需要查询数据等辅助工具时，返回短小的 JSON `tool_calls` 请求。
- 工具结果足够后，直接返回完整 Markdown 或纯文本正文，不再放进 JSON 的 `output` 字符串，也不调用 `file_write` 传输最终正文。
- 应用沿用现有解析规则：JSON 对象表示工具请求，非 JSON 文本表示最终正文。

这样可以避免长 Markdown 中的换行、引号或控制字符破坏 JSON，也避免输出上限被 JSON 转义额外放大。

## 确定性落盘

新增独立的 `DeliverableMaterializer` 服务，职责只包括：

1. 解析统一输出根目录。
2. 校验并生成安全文件名。
3. 将完整正文原子写入目标文件。
4. 返回真实文件路径，交给 `ArtifactService` 登记。

统一根目录由 `AGENT_OUTPUT_DIR` 配置，默认解析为项目根目录下的 `runtime/agent_outputs`。最终路径结构为：

```text
runtime/agent_outputs/<task_id>/<execution_id>/<filename>
```

默认文件名使用任务 ID，避免从未经校验的标题生成路径。写入先落到同目录临时文件，再原子替换目标文件。空正文不会创建有效交付物。

只有工作流准备以 `SUCCEEDED` 结束时才物化最终文件；失败、阻塞或取消结果不会把错误消息写成正式交付文档。已经由子任务工具生成的文件仍按实际执行结果保留。

现有 Agent 自定义 `file_write.base_dir` 继续服务普通工具调用，但最终文件交付只使用统一输出根目录，避免工作目录或 Agent 配置导致交付文件散落。

## Artifact 与完成门禁

文件写入成功后登记当前 execution 的 `ArtifactKind.FILE`，包括：

- 文件 URI；
- 与落盘内容一致的正文快照，供交付要求模型评估；
- MIME 类型；
- SHA-256 checksum；
- 文件格式和正文长度元数据；
- 对应任务、execution 和确定性的 source identity。

完成判定增加文件交付门禁：

- 必须至少存在一个当前 execution 的有效 FILE Artifact；
- 文件必须位于统一输出根目录内；
- 文件必须存在且非空；
- 当前 checksum 必须与 Artifact 一致；
- 文件扩展名和 MIME 类型必须符合确认的格式。

任一物理条件不满足时，任务进入现有的 `BLOCKED` 完成状态并给出明确缺口，不能标记为成功。物理校验通过后，再使用现有交付物评估和成功标准判断内容质量。

交付要求模型只评估已经通过路径、存在性和 checksum 复核的文件 Artifact，并读取 Artifact 中与文件 checksum 对应的正文快照；文件被修改后必须先判定无效，不能继续使用旧正文通过内容评估。

最终文件正文优先使用已完成轮次合并后的 `task.context.summary`；如果没有轮次正文，再使用完成阶段的 `output`。完成页上的简短结论不替代文件正文。

## 异常与并行状态保存

Agent 模型错误不再从 `TaskGraph` 抛出并丢弃整个 working copy，而是转换为失败的 `SubTaskExecutionOutcome`。现有应用阶段统一执行以下操作：

- 登记该子任务已经成功执行的工具结果；
- 登记其他并行子任务已经完成的输出和文件；
- 追加本轮状态；
- 正常返回失败 Task，由 `TaskService` 保存完整 working copy。

因此，文件副作用和任务记录保持一致；失败执行仍可在历史 execution 中查看实际产出。意外的工作流级异常仍由 `TaskService` 兜底，但不会被当作成功。

## 重跑

重跑沿用新的 execution ID，因此输出目录天然隔离。历史 execution 的文件和 Artifact 保持只读，新 execution 不复用或覆盖旧文件。完成门禁只校验当前 active execution 的交付物。

## 测试与验收

后端测试覆盖：

- 契约字段校验和历史数据兼容；
- Markdown/TXT 文件真实落盘、非空、路径和 checksum 正确；
- 文件名路径穿越被拒绝；
- TEXT Artifact 不能满足文件交付门禁；
- 文件删除、篡改或格式不匹配时不能成功；
- 模型正文不经过长 JSON 即可落盘；
- follow-up 模型失败时保留已经写出的文件和 Artifact；
- 并行任务一项失败时保留其他成功产出；
- 重跑生成独立 execution 目录。

前端测试覆盖确认表单的默认值、条件字段、校验和 API payload。

最终端到端验收必须从页面创建并确认“拼多多产品分析报告”，真实调用模型，等待执行结束，并同时满足：

- 任务状态为成功；
- 当前 execution 包含有效 FILE Artifact；
- `runtime/agent_outputs/<task_id>/<execution_id>/` 下存在非空 `.md` 或 `.txt` 文件；
- 文件 checksum 与 Artifact 一致；
- 文件内容包含确认的主要交付要求。

## 影响范围

- 核心模型：`app/core/models.py`、`app/core/enums.py`
- 配置：`app/core/config.py`、`.env.example`、`README.md`
- 规划与模型协议：`app/core/model_client.py`、`app/planners/`
- 文件和 Artifact：新增 `app/services/deliverable_materializer.py`，修改 `app/services/artifact_service.py`
- 编排与完成门禁：`app/workflows/task_graph.py`、`app/services/completion_service.py`
- 前端确认和详情：`frontend/src/taskConfirmation.ts`、`frontend/src/TaskConfirmationModal.tsx`、`frontend/src/api/taskhub.ts`、详情视图相关文件
- 测试：对应后端 pytest 与前端 Vitest 测试

不新增 API 路由，不新增数据库列；任务完整模型仍通过现有 JSON payload 持久化。
