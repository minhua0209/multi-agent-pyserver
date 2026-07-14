-- Multi-agent task persistence schema
-- Target database: MySQL 8.x
-- This script is additive and keeps existing data. It creates missing tables,
-- columns, and indexes for task requests, main tasks, rounds, subtasks,
-- events, snapshots, tool executions, and structured agent metadata.

CREATE TABLE IF NOT EXISTS schema_migrations (
  version varchar(128) PRIMARY KEY,
  description varchar(255) NOT NULL,
  applied_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DELIMITER $$

DROP PROCEDURE IF EXISTS add_column_if_missing $$
CREATE PROCEDURE add_column_if_missing(
  IN p_table_name varchar(64),
  IN p_column_name varchar(64),
  IN p_column_definition text
)
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = p_table_name
      AND column_name = p_column_name
  ) THEN
    SET @ddl = CONCAT('ALTER TABLE `', p_table_name, '` ADD COLUMN `', p_column_name, '` ', p_column_definition);
    PREPARE stmt FROM @ddl;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;
  END IF;
END $$

DROP PROCEDURE IF EXISTS add_index_if_missing $$
CREATE PROCEDURE add_index_if_missing(
  IN p_table_name varchar(64),
  IN p_index_name varchar(64),
  IN p_index_definition text
)
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = p_table_name
      AND index_name = p_index_name
  ) THEN
    SET @ddl = CONCAT('ALTER TABLE `', p_table_name, '` ADD ', p_index_definition);
    PREPARE stmt FROM @ddl;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;
  END IF;
END $$

DELIMITER ;

CREATE TABLE IF NOT EXISTS agents (
  id varchar(64) PRIMARY KEY
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CALL add_column_if_missing('agents', 'payload', 'json NULL');
CALL add_column_if_missing('agents', 'name', 'varchar(255) NULL');
CALL add_column_if_missing('agents', 'description', 'text NULL');
CALL add_column_if_missing('agents', 'capabilities_json', 'json NULL');
CALL add_column_if_missing('agents', 'tools_json', 'json NULL');
CALL add_column_if_missing('agents', 'status', 'varchar(32) NOT NULL DEFAULT ''active''');
CALL add_column_if_missing('agents', 'created_at', 'datetime(6) NULL');
CALL add_column_if_missing('agents', 'updated_at', 'datetime(6) NULL');
CALL add_index_if_missing('agents', 'idx_agents_status', 'INDEX idx_agents_status (status)');

CREATE TABLE IF NOT EXISTS task_requests (
  id varchar(64) PRIMARY KEY
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CALL add_column_if_missing('task_requests', 'source_type', 'varchar(32) NULL');
CALL add_column_if_missing('task_requests', 'content', 'longtext NULL');
CALL add_column_if_missing('task_requests', 'metadata_json', 'json NULL');
CALL add_column_if_missing('task_requests', 'status', 'varchar(32) NOT NULL DEFAULT ''running''');
CALL add_column_if_missing('task_requests', 'created_at', 'datetime(6) NULL');
CALL add_column_if_missing('task_requests', 'updated_at', 'datetime(6) NULL');
CALL add_index_if_missing('task_requests', 'idx_task_requests_status', 'INDEX idx_task_requests_status (status)');
CALL add_index_if_missing('task_requests', 'idx_task_requests_created_at', 'INDEX idx_task_requests_created_at (created_at)');

CREATE TABLE IF NOT EXISTS tasks (
  id varchar(64) PRIMARY KEY
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CALL add_column_if_missing('tasks', 'payload', 'json NULL');
CALL add_column_if_missing('tasks', 'request_id', 'varchar(64) NULL');
CALL add_column_if_missing('tasks', 'title', 'varchar(255) NULL');
CALL add_column_if_missing('tasks', 'description', 'longtext NULL');
CALL add_column_if_missing('tasks', 'status', 'varchar(32) NOT NULL DEFAULT ''running''');
CALL add_column_if_missing('tasks', 'current_node', 'varchar(64) NULL');
CALL add_column_if_missing('tasks', 'assigned_agent_id', 'varchar(64) NULL');
CALL add_column_if_missing('tasks', 'loop_count', 'int NOT NULL DEFAULT 0');
CALL add_column_if_missing('tasks', 'max_loop_count', 'int NOT NULL DEFAULT 10');
CALL add_column_if_missing('tasks', 'context_summary', 'longtext NULL');
CALL add_column_if_missing('tasks', 'final_output', 'longtext NULL');
CALL add_column_if_missing('tasks', 'draft_json', 'json NULL');
CALL add_column_if_missing('tasks', 'created_at', 'datetime(6) NULL');
CALL add_column_if_missing('tasks', 'updated_at', 'datetime(6) NULL');
CALL add_index_if_missing('tasks', 'idx_tasks_request_id', 'INDEX idx_tasks_request_id (request_id)');
CALL add_index_if_missing('tasks', 'idx_tasks_status_node', 'INDEX idx_tasks_status_node (status, current_node)');
CALL add_index_if_missing('tasks', 'idx_tasks_agent_status', 'INDEX idx_tasks_agent_status (assigned_agent_id, status)');

CREATE TABLE IF NOT EXISTS task_rounds (
  id varchar(64) PRIMARY KEY
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CALL add_column_if_missing('task_rounds', 'task_id', 'varchar(64) NULL');
CALL add_column_if_missing('task_rounds', 'round_index', 'int NULL');
CALL add_column_if_missing('task_rounds', 'execution_mode', 'varchar(32) NULL');
CALL add_column_if_missing('task_rounds', 'reason', 'text NULL');
CALL add_column_if_missing('task_rounds', 'context_before', 'longtext NULL');
CALL add_column_if_missing('task_rounds', 'context_after', 'longtext NULL');
CALL add_column_if_missing('task_rounds', 'plan_json', 'json NULL');
CALL add_column_if_missing('task_rounds', 'created_at', 'datetime(6) NULL');
CALL add_column_if_missing('task_rounds', 'updated_at', 'datetime(6) NULL');
CALL add_index_if_missing('task_rounds', 'idx_task_rounds_task_id', 'INDEX idx_task_rounds_task_id (task_id)');
CALL add_index_if_missing('task_rounds', 'uk_task_rounds_task_round', 'UNIQUE INDEX uk_task_rounds_task_round (task_id, round_index)');

CREATE TABLE IF NOT EXISTS subtasks (
  id varchar(64) PRIMARY KEY
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CALL add_column_if_missing('subtasks', 'task_id', 'varchar(64) NULL');
CALL add_column_if_missing('subtasks', 'round_id', 'varchar(64) NULL');
CALL add_column_if_missing('subtasks', 'round_index', 'int NULL');
CALL add_column_if_missing('subtasks', 'title', 'varchar(255) NULL');
CALL add_column_if_missing('subtasks', 'description', 'longtext NULL');
CALL add_column_if_missing('subtasks', 'status', 'varchar(32) NOT NULL DEFAULT ''running''');
CALL add_column_if_missing('subtasks', 'current_node', 'varchar(64) NULL');
CALL add_column_if_missing('subtasks', 'assigned_agent_id', 'varchar(64) NULL');
CALL add_column_if_missing('subtasks', 'assignee_type', 'varchar(32) NOT NULL DEFAULT ''agent''');
CALL add_column_if_missing('subtasks', 'retry_count', 'int NOT NULL DEFAULT 0');
CALL add_column_if_missing('subtasks', 'max_retry_count', 'int NOT NULL DEFAULT 3');
CALL add_column_if_missing('subtasks', 'output', 'longtext NULL');
CALL add_column_if_missing('subtasks', 'error_message', 'text NULL');
CALL add_column_if_missing('subtasks', 'tool_calls_json', 'json NULL');
CALL add_column_if_missing('subtasks', 'tool_results_json', 'json NULL');
CALL add_column_if_missing('subtasks', 'started_at', 'datetime(6) NULL');
CALL add_column_if_missing('subtasks', 'finished_at', 'datetime(6) NULL');
CALL add_column_if_missing('subtasks', 'created_at', 'datetime(6) NULL');
CALL add_column_if_missing('subtasks', 'updated_at', 'datetime(6) NULL');
CALL add_index_if_missing('subtasks', 'idx_subtasks_task_round', 'INDEX idx_subtasks_task_round (task_id, round_index)');
CALL add_index_if_missing('subtasks', 'idx_subtasks_agent_status', 'INDEX idx_subtasks_agent_status (assigned_agent_id, status)');
CALL add_index_if_missing('subtasks', 'idx_subtasks_status', 'INDEX idx_subtasks_status (status)');

CREATE TABLE IF NOT EXISTS task_events (
  id bigint PRIMARY KEY AUTO_INCREMENT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CALL add_column_if_missing('task_events', 'task_id', 'varchar(64) NULL');
CALL add_column_if_missing('task_events', 'subtask_id', 'varchar(64) NULL');
CALL add_column_if_missing('task_events', 'event_type', 'varchar(64) NULL');
CALL add_column_if_missing('task_events', 'node_name', 'varchar(64) NULL');
CALL add_column_if_missing('task_events', 'message', 'text NULL');
CALL add_column_if_missing('task_events', 'payload_json', 'json NULL');
CALL add_column_if_missing('task_events', 'created_at', 'datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)');
CALL add_index_if_missing('task_events', 'idx_task_events_task_time', 'INDEX idx_task_events_task_time (task_id, created_at)');
CALL add_index_if_missing('task_events', 'idx_task_events_subtask_time', 'INDEX idx_task_events_subtask_time (subtask_id, created_at)');

CREATE TABLE IF NOT EXISTS task_snapshots (
  id bigint PRIMARY KEY AUTO_INCREMENT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CALL add_column_if_missing('task_snapshots', 'task_id', 'varchar(64) NULL');
CALL add_column_if_missing('task_snapshots', 'subtask_id', 'varchar(64) NULL');
CALL add_column_if_missing('task_snapshots', 'round_id', 'varchar(64) NULL');
CALL add_column_if_missing('task_snapshots', 'snapshot_type', 'varchar(64) NULL');
CALL add_column_if_missing('task_snapshots', 'node_name', 'varchar(64) NULL');
CALL add_column_if_missing('task_snapshots', 'snapshot_json', 'json NULL');
CALL add_column_if_missing('task_snapshots', 'created_at', 'datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)');
CALL add_index_if_missing('task_snapshots', 'idx_task_snapshots_task_time', 'INDEX idx_task_snapshots_task_time (task_id, created_at)');
CALL add_index_if_missing('task_snapshots', 'idx_task_snapshots_subtask_time', 'INDEX idx_task_snapshots_subtask_time (subtask_id, created_at)');

CREATE TABLE IF NOT EXISTS tool_executions (
  id bigint PRIMARY KEY AUTO_INCREMENT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CALL add_column_if_missing('tool_executions', 'task_id', 'varchar(64) NULL');
CALL add_column_if_missing('tool_executions', 'subtask_id', 'varchar(64) NULL');
CALL add_column_if_missing('tool_executions', 'agent_id', 'varchar(64) NULL');
CALL add_column_if_missing('tool_executions', 'tool_name', 'varchar(128) NULL');
CALL add_column_if_missing('tool_executions', 'tool_type', 'varchar(64) NULL');
CALL add_column_if_missing('tool_executions', 'arguments_json', 'json NULL');
CALL add_column_if_missing('tool_executions', 'success', 'tinyint(1) NOT NULL DEFAULT 0');
CALL add_column_if_missing('tool_executions', 'result_text', 'longtext NULL');
CALL add_column_if_missing('tool_executions', 'error_message', 'text NULL');
CALL add_column_if_missing('tool_executions', 'started_at', 'datetime(6) NULL');
CALL add_column_if_missing('tool_executions', 'finished_at', 'datetime(6) NULL');
CALL add_index_if_missing('tool_executions', 'idx_tool_executions_subtask', 'INDEX idx_tool_executions_subtask (subtask_id)');
CALL add_index_if_missing('tool_executions', 'idx_tool_executions_task', 'INDEX idx_tool_executions_task (task_id)');

INSERT INTO schema_migrations (version, description)
VALUES ('2026-07-14-task-persistence-schema', 'Structured task, subtask, event, snapshot, tool execution persistence schema')
ON DUPLICATE KEY UPDATE applied_at = VALUES(applied_at);

DROP PROCEDURE IF EXISTS add_column_if_missing;
DROP PROCEDURE IF EXISTS add_index_if_missing;
