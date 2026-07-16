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
