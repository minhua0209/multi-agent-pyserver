-- 客户交流活动方案审核演示场景
-- Target database: MySQL 8.x
-- Prerequisites:
--   1. 已执行任务持久化和用户管理结构迁移。
--   2. agents 表包含 agent_type、metadata_json 字段。
-- Purpose:
--   导入 5 个 Mock Agent 节点、1 个人工节点、1 个审核用户和 1 个流程模板。
--   脚本使用固定 ID，可重复执行；再次执行会覆盖本场景的节点和模板配置。

START TRANSACTION;

SET @demo_created_at = '2026-07-23 00:00:00.000000';
SET @demo_created_at_iso = '2026-07-23T00:00:00Z';

-- ---------------------------------------------------------------------------
-- 1. 人工审核用户
-- ---------------------------------------------------------------------------

INSERT INTO users (
  id, name, phone, email, role, department, position, status, remark, created_at, updated_at
)
VALUES (
  'user_bb7df718739b',
  '王大锤',
  '',
  '',
  'user',
  '',
  '',
  'active',
  '客户交流活动方案审核演示场景操作人',
  @demo_created_at,
  CURRENT_TIMESTAMP(6)
)
ON DUPLICATE KEY UPDATE
  name = VALUES(name),
  status = 'active',
  remark = VALUES(remark),
  updated_at = CURRENT_TIMESTAMP(6);

-- ---------------------------------------------------------------------------
-- 2. Agent 节点
-- ---------------------------------------------------------------------------

-- 2.1 活动需求梳理节点
SET @agent_id = 'agent_8ae0c4c8b48b';
SET @agent_name = '演示-活动需求梳理节点';
SET @agent_description = '梳理活动目标、参与对象、时间安排和关键诉求，为后续方案生成提供结构化输入。';
SET @agent_capabilities = JSON_ARRAY('event_requirement_analysis', 'requirement_analysis');
SET @agent_execution_config = JSON_OBJECT(
  'system_prompt', '你是活动需求梳理节点。必须调用 event_requirement_mock 工具，并结合任务上下文用中文输出清晰的需求摘要。',
  'model_name', '',
  'temperature', NULL,
  'timeout_seconds', 60,
  'max_retries', 1,
  'max_tool_calls', 2
);
SET @agent_tools = JSON_ARRAY(
  JSON_OBJECT(
    'name', 'event_requirement_mock',
    'description', '获取演示活动的需求信息',
    'type', 'mock',
    'config', JSON_OBJECT(
      'response', '活动目标：维护重点客户关系并收集产品反馈；参与对象：30名重点客户和10名内部同事；建议时间：下月第二周周五下午；形式：线下交流会；核心环节：产品分享、客户圆桌、自由交流。'
    ),
    'input_schema', JSON_OBJECT('type', 'object', 'properties', JSON_OBJECT())
  )
);
SET @agent_metadata = JSON_OBJECT('demo_scene', 'event_plan_approval');
SET @agent_payload = JSON_OBJECT(
  'id', @agent_id,
  'name', @agent_name,
  'description', @agent_description,
  'agent_type', 'processing',
  'capabilities', JSON_EXTRACT(@agent_capabilities, '$'),
  'input_schema', JSON_OBJECT(),
  'output_schema', JSON_OBJECT(),
  'execution_config', JSON_EXTRACT(@agent_execution_config, '$'),
  'tools', JSON_EXTRACT(@agent_tools, '$'),
  'metadata', JSON_EXTRACT(@agent_metadata, '$'),
  'created_at', @demo_created_at_iso
);

INSERT INTO agents (
  id, payload, name, description, agent_type, capabilities_json,
  input_schema_json, output_schema_json, execution_config_json,
  tools_json, metadata_json, status, created_at, updated_at
)
VALUES (
  @agent_id, @agent_payload, @agent_name, @agent_description, 'processing', @agent_capabilities,
  JSON_OBJECT(), JSON_OBJECT(), @agent_execution_config,
  @agent_tools, @agent_metadata, 'active', @demo_created_at, CURRENT_TIMESTAMP(6)
)
ON DUPLICATE KEY UPDATE
  payload = VALUES(payload),
  name = VALUES(name),
  description = VALUES(description),
  agent_type = VALUES(agent_type),
  capabilities_json = VALUES(capabilities_json),
  input_schema_json = VALUES(input_schema_json),
  output_schema_json = VALUES(output_schema_json),
  execution_config_json = VALUES(execution_config_json),
  tools_json = VALUES(tools_json),
  metadata_json = VALUES(metadata_json),
  status = 'active',
  updated_at = CURRENT_TIMESTAMP(6);

-- 2.2 预算与风险分析节点
SET @agent_id = 'agent_7f608c668057';
SET @agent_name = '演示-预算风险分析节点';
SET @agent_description = '分析活动预算构成、合规要求和主要执行风险。';
SET @agent_capabilities = JSON_ARRAY('budget_analysis', 'risk_analysis');
SET @agent_execution_config = JSON_OBJECT(
  'system_prompt', '你是预算与风险分析节点。必须调用 budget_risk_mock 工具，并结合任务上下文用中文输出预算和风险结论。',
  'model_name', '',
  'temperature', NULL,
  'timeout_seconds', 60,
  'max_retries', 1,
  'max_tool_calls', 2
);
SET @agent_tools = JSON_ARRAY(
  JSON_OBJECT(
    'name', 'budget_risk_mock',
    'description', '获取演示活动预算和风险信息',
    'type', 'mock',
    'config', JSON_OBJECT(
      'response', '预算上限：50000元；建议分配：场地12000元、餐饮18000元、物料8000元、差旅及机动12000元；主要风险：场地档期、客户到场率、临时费用超支；控制建议：提前锁定场地、活动前一周二次确认、保留10%机动预算。'
    ),
    'input_schema', JSON_OBJECT('type', 'object', 'properties', JSON_OBJECT())
  )
);
SET @agent_metadata = JSON_OBJECT('demo_scene', 'event_plan_approval');
SET @agent_payload = JSON_OBJECT(
  'id', @agent_id,
  'name', @agent_name,
  'description', @agent_description,
  'agent_type', 'processing',
  'capabilities', JSON_EXTRACT(@agent_capabilities, '$'),
  'input_schema', JSON_OBJECT(),
  'output_schema', JSON_OBJECT(),
  'execution_config', JSON_EXTRACT(@agent_execution_config, '$'),
  'tools', JSON_EXTRACT(@agent_tools, '$'),
  'metadata', JSON_EXTRACT(@agent_metadata, '$'),
  'created_at', @demo_created_at_iso
);

INSERT INTO agents (
  id, payload, name, description, agent_type, capabilities_json,
  input_schema_json, output_schema_json, execution_config_json,
  tools_json, metadata_json, status, created_at, updated_at
)
VALUES (
  @agent_id, @agent_payload, @agent_name, @agent_description, 'processing', @agent_capabilities,
  JSON_OBJECT(), JSON_OBJECT(), @agent_execution_config,
  @agent_tools, @agent_metadata, 'active', @demo_created_at, CURRENT_TIMESTAMP(6)
)
ON DUPLICATE KEY UPDATE
  payload = VALUES(payload), name = VALUES(name), description = VALUES(description),
  agent_type = VALUES(agent_type), capabilities_json = VALUES(capabilities_json),
  input_schema_json = VALUES(input_schema_json), output_schema_json = VALUES(output_schema_json),
  execution_config_json = VALUES(execution_config_json), tools_json = VALUES(tools_json),
  metadata_json = VALUES(metadata_json), status = 'active', updated_at = CURRENT_TIMESTAMP(6);

-- 2.3 活动方案汇总节点
SET @agent_id = 'agent_61171e9e983c';
SET @agent_name = '演示-活动方案汇总节点';
SET @agent_description = '汇总需求和预算风险分析，形成可供人工审核的完整活动执行方案。';
SET @agent_capabilities = JSON_ARRAY('event_plan_generation', 'context_synthesis');
SET @agent_execution_config = JSON_OBJECT(
  'system_prompt', '你是活动方案汇总节点。必须读取上游需求与预算风险结果，并调用 event_plan_mock 工具，输出包含目标、议程、预算、分工和风险预案的中文方案。',
  'model_name', '',
  'temperature', NULL,
  'timeout_seconds', 60,
  'max_retries', 1,
  'max_tool_calls', 2
);
SET @agent_tools = JSON_ARRAY(
  JSON_OBJECT(
    'name', 'event_plan_mock',
    'description', '生成演示活动方案骨架',
    'type', 'mock',
    'config', JSON_OBJECT(
      'response', '方案建议：活动时长3小时；议程为签到交流30分钟、产品分享40分钟、客户圆桌60分钟、茶歇交流40分钟、总结10分钟；总预算控制在50000元内；市场部负责邀约与物料，销售部负责客户确认，产品部负责分享和问题记录；保留候补场地及10%机动预算。'
    ),
    'input_schema', JSON_OBJECT('type', 'object', 'properties', JSON_OBJECT())
  )
);
SET @agent_metadata = JSON_OBJECT('demo_scene', 'event_plan_approval');
SET @agent_payload = JSON_OBJECT(
  'id', @agent_id, 'name', @agent_name, 'description', @agent_description,
  'agent_type', 'processing', 'capabilities', JSON_EXTRACT(@agent_capabilities, '$'),
  'input_schema', JSON_OBJECT(), 'output_schema', JSON_OBJECT(),
  'execution_config', JSON_EXTRACT(@agent_execution_config, '$'),
  'tools', JSON_EXTRACT(@agent_tools, '$'),
  'metadata', JSON_EXTRACT(@agent_metadata, '$'),
  'created_at', @demo_created_at_iso
);

INSERT INTO agents (
  id, payload, name, description, agent_type, capabilities_json,
  input_schema_json, output_schema_json, execution_config_json,
  tools_json, metadata_json, status, created_at, updated_at
)
VALUES (
  @agent_id, @agent_payload, @agent_name, @agent_description, 'processing', @agent_capabilities,
  JSON_OBJECT(), JSON_OBJECT(), @agent_execution_config,
  @agent_tools, @agent_metadata, 'active', @demo_created_at, CURRENT_TIMESTAMP(6)
)
ON DUPLICATE KEY UPDATE
  payload = VALUES(payload), name = VALUES(name), description = VALUES(description),
  agent_type = VALUES(agent_type), capabilities_json = VALUES(capabilities_json),
  input_schema_json = VALUES(input_schema_json), output_schema_json = VALUES(output_schema_json),
  execution_config_json = VALUES(execution_config_json), tools_json = VALUES(tools_json),
  metadata_json = VALUES(metadata_json), status = 'active', updated_at = CURRENT_TIMESTAMP(6);

-- 2.4 审核返工节点
SET @agent_id = 'agent_ecf45311ac54';
SET @agent_name = '演示-审核返工节点';
SET @agent_description = '根据人工驳回意见生成明确的方案修改清单和返工版本。';
SET @agent_capabilities = JSON_ARRAY('plan_rework', 'human_feedback_processing');
SET @agent_execution_config = JSON_OBJECT(
  'system_prompt', '你是方案返工节点。必须读取人工审核意见并调用 plan_rework_mock 工具，输出返工项、负责人和下一步建议。',
  'model_name', '',
  'temperature', NULL,
  'timeout_seconds', 60,
  'max_retries', 1,
  'max_tool_calls', 2
);
SET @agent_tools = JSON_ARRAY(
  JSON_OBJECT(
    'name', 'plan_rework_mock',
    'description', '生成演示返工处理结果',
    'type', 'mock',
    'config', JSON_OBJECT(
      'response', '已根据审核意见生成返工清单：调整预算明细、补充客户邀约节奏、增加雨天备用场地、明确各部门负责人；方案状态标记为待重新评审。'
    ),
    'input_schema', JSON_OBJECT('type', 'object', 'properties', JSON_OBJECT())
  )
);
SET @agent_metadata = JSON_OBJECT('demo_scene', 'event_plan_approval');
SET @agent_payload = JSON_OBJECT(
  'id', @agent_id, 'name', @agent_name, 'description', @agent_description,
  'agent_type', 'processing', 'capabilities', JSON_EXTRACT(@agent_capabilities, '$'),
  'input_schema', JSON_OBJECT(), 'output_schema', JSON_OBJECT(),
  'execution_config', JSON_EXTRACT(@agent_execution_config, '$'),
  'tools', JSON_EXTRACT(@agent_tools, '$'),
  'metadata', JSON_EXTRACT(@agent_metadata, '$'),
  'created_at', @demo_created_at_iso
);

INSERT INTO agents (
  id, payload, name, description, agent_type, capabilities_json,
  input_schema_json, output_schema_json, execution_config_json,
  tools_json, metadata_json, status, created_at, updated_at
)
VALUES (
  @agent_id, @agent_payload, @agent_name, @agent_description, 'processing', @agent_capabilities,
  JSON_OBJECT(), JSON_OBJECT(), @agent_execution_config,
  @agent_tools, @agent_metadata, 'active', @demo_created_at, CURRENT_TIMESTAMP(6)
)
ON DUPLICATE KEY UPDATE
  payload = VALUES(payload), name = VALUES(name), description = VALUES(description),
  agent_type = VALUES(agent_type), capabilities_json = VALUES(capabilities_json),
  input_schema_json = VALUES(input_schema_json), output_schema_json = VALUES(output_schema_json),
  execution_config_json = VALUES(execution_config_json), tools_json = VALUES(tools_json),
  metadata_json = VALUES(metadata_json), status = 'active', updated_at = CURRENT_TIMESTAMP(6);

-- 2.5 执行通知生成节点
SET @agent_id = 'agent_8466e201b881';
SET @agent_name = '演示-执行通知生成节点';
SET @agent_description = '在方案审核通过后生成面向执行团队的通知和行动清单。';
SET @agent_capabilities = JSON_ARRAY('execution_notice', 'action_list_generation');
SET @agent_execution_config = JSON_OBJECT(
  'system_prompt', '你是执行通知节点。必须读取已通过方案和人工意见，调用 execution_notice_mock 工具，输出中文执行通知。',
  'model_name', '',
  'temperature', NULL,
  'timeout_seconds', 60,
  'max_retries', 1,
  'max_tool_calls', 2
);
SET @agent_tools = JSON_ARRAY(
  JSON_OBJECT(
    'name', 'execution_notice_mock',
    'description', '生成演示执行通知',
    'type', 'mock',
    'config', JSON_OBJECT(
      'response', '活动方案已审核通过。执行通知：市场部本周内完成场地和物料确认；销售部三个工作日内提交客户邀约名单；产品部下周三前完成分享材料；项目负责人每周同步一次风险与预算使用情况。'
    ),
    'input_schema', JSON_OBJECT('type', 'object', 'properties', JSON_OBJECT())
  )
);
SET @agent_metadata = JSON_OBJECT('demo_scene', 'event_plan_approval');
SET @agent_payload = JSON_OBJECT(
  'id', @agent_id, 'name', @agent_name, 'description', @agent_description,
  'agent_type', 'processing', 'capabilities', JSON_EXTRACT(@agent_capabilities, '$'),
  'input_schema', JSON_OBJECT(), 'output_schema', JSON_OBJECT(),
  'execution_config', JSON_EXTRACT(@agent_execution_config, '$'),
  'tools', JSON_EXTRACT(@agent_tools, '$'),
  'metadata', JSON_EXTRACT(@agent_metadata, '$'),
  'created_at', @demo_created_at_iso
);

INSERT INTO agents (
  id, payload, name, description, agent_type, capabilities_json,
  input_schema_json, output_schema_json, execution_config_json,
  tools_json, metadata_json, status, created_at, updated_at
)
VALUES (
  @agent_id, @agent_payload, @agent_name, @agent_description, 'processing', @agent_capabilities,
  JSON_OBJECT(), JSON_OBJECT(), @agent_execution_config,
  @agent_tools, @agent_metadata, 'active', @demo_created_at, CURRENT_TIMESTAMP(6)
)
ON DUPLICATE KEY UPDATE
  payload = VALUES(payload), name = VALUES(name), description = VALUES(description),
  agent_type = VALUES(agent_type), capabilities_json = VALUES(capabilities_json),
  input_schema_json = VALUES(input_schema_json), output_schema_json = VALUES(output_schema_json),
  execution_config_json = VALUES(execution_config_json), tools_json = VALUES(tools_json),
  metadata_json = VALUES(metadata_json), status = 'active', updated_at = CURRENT_TIMESTAMP(6);

-- 2.6 王大锤人工审核节点
SET @agent_id = 'agent_9711351f3ecf';
SET @agent_name = '演示-活动方案审核节点';
SET @agent_description = '人工审批节点，审批人：王大锤';
SET @agent_capabilities = JSON_ARRAY('human_approval');
SET @agent_execution_config = JSON_OBJECT(
  'system_prompt', '',
  'model_name', '',
  'temperature', NULL,
  'timeout_seconds', 60,
  'max_retries', 0,
  'max_tool_calls', 5
);
SET @agent_tools = JSON_ARRAY();
SET @agent_metadata = JSON_OBJECT(
  'assignee_user_id', 'user_bb7df718739b',
  'assignee_user_name', '王大锤',
  'assignee_role', 'user',
  'demo_scene', 'event_plan_approval'
);
SET @agent_payload = JSON_OBJECT(
  'id', @agent_id, 'name', @agent_name, 'description', @agent_description,
  'agent_type', 'human', 'capabilities', JSON_EXTRACT(@agent_capabilities, '$'),
  'input_schema', JSON_OBJECT(), 'output_schema', JSON_OBJECT(),
  'execution_config', JSON_EXTRACT(@agent_execution_config, '$'),
  'tools', JSON_EXTRACT(@agent_tools, '$'),
  'metadata', JSON_EXTRACT(@agent_metadata, '$'),
  'created_at', @demo_created_at_iso
);

INSERT INTO agents (
  id, payload, name, description, agent_type, capabilities_json,
  input_schema_json, output_schema_json, execution_config_json,
  tools_json, metadata_json, status, created_at, updated_at
)
VALUES (
  @agent_id, @agent_payload, @agent_name, @agent_description, 'human', @agent_capabilities,
  JSON_OBJECT(), JSON_OBJECT(), @agent_execution_config,
  @agent_tools, @agent_metadata, 'active', @demo_created_at, CURRENT_TIMESTAMP(6)
)
ON DUPLICATE KEY UPDATE
  payload = VALUES(payload), name = VALUES(name), description = VALUES(description),
  agent_type = VALUES(agent_type), capabilities_json = VALUES(capabilities_json),
  input_schema_json = VALUES(input_schema_json), output_schema_json = VALUES(output_schema_json),
  execution_config_json = VALUES(execution_config_json), tools_json = VALUES(tools_json),
  metadata_json = VALUES(metadata_json), status = 'active', updated_at = CURRENT_TIMESTAMP(6);

-- ---------------------------------------------------------------------------
-- 3. 流程模板
-- ---------------------------------------------------------------------------

SET @workflow_id = 'workflow_f7ad2abbef6f';
SET @workflow_name = '客户交流活动方案审核演示';
SET @workflow_description = '演示流程模板下的并行资料准备、结果汇聚、人工审核、智能条件分支及后续自动执行。';

SET @workflow_definition = JSON_OBJECT(
  'nodes', JSON_ARRAY(
    JSON_OBJECT(
      'id', 'start',
      'type', 'start',
      'title', '开始',
      'description', '读取任务诉求并初始化流程上下文。',
      'agent_id', NULL,
      'config', JSON_OBJECT(
        'context_inputs', JSON_ARRAY('task.content', 'source_type', 'request_metadata'),
        'context_outputs', JSON_ARRAY('context.summary')
      )
    ),
    JSON_OBJECT(
      'id', 'requirement_analysis',
      'type', 'agent',
      'title', '活动需求梳理',
      'description', '梳理活动目标、参与对象、时间安排和关键诉求。',
      'agent_id', 'agent_8ae0c4c8b48b',
      'config', JSON_OBJECT(
        'context_inputs', JSON_ARRAY('context.summary', 'task.content'),
        'context_outputs', JSON_ARRAY('subtask.output', 'context.summary')
      )
    ),
    JSON_OBJECT(
      'id', 'budget_risk_analysis',
      'type', 'agent',
      'title', '预算与风险分析',
      'description', '分析预算构成、合规要求和主要执行风险。',
      'agent_id', 'agent_7f608c668057',
      'config', JSON_OBJECT(
        'context_inputs', JSON_ARRAY('context.summary', 'task.content'),
        'context_outputs', JSON_ARRAY('subtask.output', 'context.summary')
      )
    ),
    JSON_OBJECT(
      'id', 'plan_synthesis',
      'type', 'agent',
      'title', '活动方案汇总',
      'description', '汇总需求和预算风险结果，形成可供审核的活动执行方案。',
      'agent_id', 'agent_61171e9e983c',
      'config', JSON_OBJECT(
        'context_inputs', JSON_ARRAY('context.summary', 'subtask.output'),
        'context_outputs', JSON_ARRAY('subtask.output', 'context.summary')
      )
    ),
    JSON_OBJECT(
      'id', 'human_review',
      'type', 'human',
      'title', '王大锤审核活动方案',
      'description', '请审核活动目标、议程、预算、部门分工和风险预案，并填写明确的通过或驳回意见。',
      'agent_id', 'agent_9711351f3ecf',
      'config', JSON_OBJECT(
        'assignee_user_id', 'user_bb7df718739b',
        'assignee_user_name', '王大锤',
        'assignee_role', 'user',
        'handoff_instruction', '请审核上游生成的活动方案。若方案可执行，请选择通过并填写意见；若需要修改，请选择驳回并说明具体修改项。',
        'context_inputs', JSON_ARRAY('context.summary', 'subtask.output'),
        'context_outputs', JSON_ARRAY('result_metadata.decision', 'human_comment', 'context.summary'),
        'required_metadata', JSON_ARRAY('decision')
      )
    ),
    JSON_OBJECT(
      'id', 'review_decision',
      'type', 'condition',
      'title', '审核结果判断',
      'description', '根据王大锤的人工审核决定和审核意见判断后续流向。',
      'agent_id', NULL,
      'config', JSON_OBJECT(
        'condition_description', '结合最近一次人工审核结果和意见判断方案是否通过。人工明确通过、同意或可以执行时选择 approved；人工明确驳回、不同意或要求修改时选择 rejected。',
        'condition_options', JSON_ARRAY(
          JSON_OBJECT('value', 'approved', 'content', '人工审核通过、同意执行或确认方案可以继续推进'),
          JSON_OBJECT('value', 'rejected', 'content', '人工审核驳回、不同意执行或明确要求修改返工')
        ),
        'context_inputs', JSON_ARRAY('context.summary', 'subtask.output', 'human_comment'),
        'context_outputs', JSON_ARRAY('result_metadata.decision', 'context.summary')
      )
    ),
    JSON_OBJECT(
      'id', 'execution_notice',
      'type', 'agent',
      'title', '生成执行通知',
      'description', '审核通过后生成执行通知、负责人安排和行动清单。',
      'agent_id', 'agent_8466e201b881',
      'config', JSON_OBJECT(
        'context_inputs', JSON_ARRAY('context.summary', 'subtask.output', 'human_comment'),
        'context_outputs', JSON_ARRAY('subtask.output', 'context.summary')
      )
    ),
    JSON_OBJECT(
      'id', 'plan_rework',
      'type', 'agent',
      'title', '生成返工方案',
      'description', '审核驳回后根据人工意见生成修改清单和返工版本。',
      'agent_id', 'agent_ecf45311ac54',
      'config', JSON_OBJECT(
        'context_inputs', JSON_ARRAY('context.summary', 'subtask.output', 'human_comment'),
        'context_outputs', JSON_ARRAY('subtask.output', 'context.summary')
      )
    ),
    JSON_OBJECT(
      'id', 'end',
      'type', 'end',
      'title', '完成',
      'description', '汇总流程上下文并生成最终结果。',
      'agent_id', NULL,
      'config', JSON_OBJECT(
        'context_inputs', JSON_ARRAY('context.summary', 'subtask.output'),
        'context_outputs', JSON_ARRAY('final_output')
      )
    )
  ),
  'edges', JSON_ARRAY(
    JSON_OBJECT('from', 'start', 'to', 'requirement_analysis', 'condition', JSON_OBJECT()),
    JSON_OBJECT('from', 'start', 'to', 'budget_risk_analysis', 'condition', JSON_OBJECT()),
    JSON_OBJECT('from', 'requirement_analysis', 'to', 'plan_synthesis', 'condition', JSON_OBJECT()),
    JSON_OBJECT('from', 'budget_risk_analysis', 'to', 'plan_synthesis', 'condition', JSON_OBJECT()),
    JSON_OBJECT('from', 'plan_synthesis', 'to', 'human_review', 'condition', JSON_OBJECT()),
    JSON_OBJECT('from', 'human_review', 'to', 'review_decision', 'condition', JSON_OBJECT()),
    JSON_OBJECT(
      'from', 'review_decision',
      'to', 'execution_notice',
      'condition', JSON_OBJECT('type', 'decision', 'value', 'approved')
    ),
    JSON_OBJECT(
      'from', 'review_decision',
      'to', 'plan_rework',
      'condition', JSON_OBJECT('type', 'decision', 'value', 'rejected')
    ),
    JSON_OBJECT('from', 'execution_notice', 'to', 'end', 'condition', JSON_OBJECT()),
    JSON_OBJECT('from', 'plan_rework', 'to', 'end', 'condition', JSON_OBJECT())
  )
);

INSERT INTO workflow_templates (
  id, name, description, definition_json, status, created_at, updated_at
)
VALUES (
  @workflow_id,
  @workflow_name,
  @workflow_description,
  @workflow_definition,
  'active',
  @demo_created_at,
  CURRENT_TIMESTAMP(6)
)
ON DUPLICATE KEY UPDATE
  name = VALUES(name),
  description = VALUES(description),
  definition_json = VALUES(definition_json),
  status = 'active',
  updated_at = CURRENT_TIMESTAMP(6);

-- ---------------------------------------------------------------------------
-- 4. 迁移记录
-- ---------------------------------------------------------------------------

INSERT INTO schema_migrations (version, description)
VALUES (
  '2026-07-23-event-plan-approval-demo',
  'Customer event plan approval demo agents, reviewer and workflow template'
)
ON DUPLICATE KEY UPDATE
  description = VALUES(description),
  applied_at = CURRENT_TIMESTAMP(6);

COMMIT;

-- 验证查询：
-- SELECT id, name, agent_type, status FROM agents
-- WHERE JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.demo_scene')) = 'event_plan_approval';
-- SELECT id, name, status FROM workflow_templates WHERE id = 'workflow_f7ad2abbef6f';
-- SELECT id, name, status FROM users WHERE id = 'user_bb7df718739b';

-- 演示任务：
-- 任务名称：重点客户交流活动方案
-- 任务描述：公司下个月想办一场客户交流活动，大概邀请30位客户，预算控制在5万元以内。
--           帮我把活动怎么安排、钱怎么花、各部门要做什么，以及可能遇到的问题整理清楚。
--           方案做好后先让王大锤看一下，如果没问题就通知大家开始准备；
--           如果有问题，就按照他的意见修改方案。
-- 发布任务时选择流程模板：客户交流活动方案审核演示

-- 可选回滚（仅在确认这些固定 ID 未被其他场景引用后执行）：
-- START TRANSACTION;
-- DELETE FROM workflow_templates WHERE id = 'workflow_f7ad2abbef6f';
-- DELETE FROM agents WHERE id IN (
--   'agent_8ae0c4c8b48b',
--   'agent_7f608c668057',
--   'agent_61171e9e983c',
--   'agent_ecf45311ac54',
--   'agent_8466e201b881',
--   'agent_9711351f3ecf'
-- );
-- DELETE FROM schema_migrations WHERE version = '2026-07-23-event-plan-approval-demo';
-- COMMIT;
-- 王大锤用户可能被其他业务使用，回滚默认不删除 users 表中的用户记录。
