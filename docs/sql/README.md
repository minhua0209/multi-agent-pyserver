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
