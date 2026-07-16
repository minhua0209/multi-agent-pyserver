-- 2026-07-16 用户管理与任务归属增量脚本
-- 目标数据库：MySQL 8.x

CREATE TABLE IF NOT EXISTS users (
  id VARCHAR(64) PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  phone VARCHAR(64) NULL,
  email VARCHAR(255) NULL,
  role VARCHAR(32) NOT NULL DEFAULT 'user',
  department VARCHAR(255) NULL,
  position VARCHAR(255) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'active',
  remark TEXT NULL,
  created_at DATETIME NULL,
  updated_at DATETIME NULL
);

INSERT INTO users (
  id,
  name,
  phone,
  email,
  role,
  department,
  position,
  status,
  remark,
  created_at,
  updated_at
)
SELECT
  'root',
  '管理员',
  '',
  '',
  'admin',
  '平台',
  '系统管理员',
  'active',
  '默认管理员',
  NOW(),
  NOW()
WHERE NOT EXISTS (
  SELECT 1 FROM users WHERE id = 'root'
);

ALTER TABLE task_requests
  ADD COLUMN created_by_user_id VARCHAR(64) NULL,
  ADD COLUMN created_by_user_name VARCHAR(255) NULL;

ALTER TABLE tasks
  ADD COLUMN created_by_user_id VARCHAR(64) NULL,
  ADD COLUMN created_by_user_name VARCHAR(255) NULL;
