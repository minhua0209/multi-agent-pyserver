ALTER TABLE agents
  ADD COLUMN metadata_json TEXT NULL;

ALTER TABLE subtasks
  ADD COLUMN assignee_user_id VARCHAR(64) NULL,
  ADD COLUMN assignee_user_name VARCHAR(255) NULL,
  ADD COLUMN assignee_role VARCHAR(128) NULL;
