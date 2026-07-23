# SQL 脚本记录

## 2026-07-14 任务持久化表结构

- 脚本文件：`docs/sql/2026-07-14-task-persistence-schema.sql`
- 目标数据库：MySQL 8.x
- 本次执行库：`demo_db`
- 迁移版本：`2026-07-14-task-persistence-schema`
- 执行方式：通过 `PyMySQL` 连接本地 Docker MySQL 容器执行。

### 创建/更新的表

- `schema_migrations`
- `agents`
- `workflow_templates`
- `task_requests`
- `tasks`
- `task_rounds`
- `subtasks`
- `task_events`
- `task_snapshots`
- `tool_executions`

### 说明

- 脚本是增量式的：已存在的表不会被删除。
- 旧版 `agents`、`tasks` 表如果已存在，会补充结构化字段。
- `schema_migrations` 会记录脚本执行版本。
- 当前脚本只负责表结构创建和补列，不负责把历史 `payload` 数据拆分回填到结构化列。

## 2026-07-16 用户管理与任务归属

- 脚本文件：`docs/sql/2026-07-16-user-management-schema.sql`
- 目标数据库：MySQL 8.x
- 迁移版本：`2026-07-16-user-management-schema`

### 创建/更新的表

- 新增 `users` 表，保存姓名、手机号、邮箱、角色、部门、岗位、状态、备注等用户资料。
- `task_requests` 增加 `created_by_user_id`、`created_by_user_name`。
- `tasks` 增加 `created_by_user_id`、`created_by_user_name`。

### 说明

- 脚本会插入默认管理员 `root / 管理员 / admin`，如果已存在则不重复插入。
- 当前 SQL 使用普通 `ALTER TABLE ... ADD COLUMN`，线上执行前需要结合实际数据库判断字段是否已经存在。

## 2026-07-23 客户交流活动方案审核演示场景

- 脚本文件：`docs/sql/演示场景.sql`
- 目标数据库：MySQL 8.x
- 本次执行库：`demo_db`
- 迁移版本：`2026-07-23-event-plan-approval-demo`

### 写入的数据

- 5 个使用 Mock 工具的处理 Agent 节点。
- 1 个绑定王大锤的人工审核节点。
- 1 个王大锤演示用户。
- 1 个“客户交流活动方案审核演示”流程模板。

### 流程能力

- 活动需求梳理和预算风险分析并行执行。
- 两个并行结果汇聚后生成活动方案。
- 活动方案暂停并交由王大锤人工审核。
- 条件节点根据人工审核意见选择通过或驳回分支。
- 通过后生成执行通知，驳回后生成返工方案，最终到达完成节点。

### 说明

- 脚本使用固定节点和流程 ID，可重复执行。
- 重复执行时更新本场景数据，不会重复插入同 ID 记录。
- Agent `payload` 中的数组和对象通过 `JSON_EXTRACT` 保持为原生 JSON，避免 MySQL 用户变量将其写成字符串。
- 已在本地 `demo_db` 执行验证，后端 API 可以正常读取导入的节点和流程定义。
