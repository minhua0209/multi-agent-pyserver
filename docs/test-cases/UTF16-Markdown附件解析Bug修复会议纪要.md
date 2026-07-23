# UTF-16 Markdown附件解析乱码Bug修复需求讨论会议纪要

本次会议于2026年7月22日召开，主要围绕任务中心上传UTF-16编码Markdown附件后出现中文乱码的问题展开讨论。会议确认该问题可以在当前项目中稳定复现，影响附件内容进入任务意图识别、任务上下文和后续执行的准确性。本次纪要只形成一个“修复UTF-16 Markdown附件解析乱码”的主任务，问题定位、代码修复、自动测试、本地Agent处理和报告生成均属于该主任务内部的顺序子任务，不应拆分成多个互相独立的主任务。

### 一、Bug现象与影响范围

当前项目通过`app/services/attachment_parser.py`解析`.md`、`.txt`和`.log`文本附件。现有解码顺序为`utf-8-sig`、`utf-8`、`gb18030`，没有在通用解码前识别UTF-16字节顺序标记。带有`FF FE`或`FE FF` BOM的UTF-16附件可能被错误地按GB18030解码，随后文本清理又会移除NUL字符，最终留下非空乱码。由于解析过程没有抛出异常，附件状态仍会显示为`parsed`，但任务意图识别和任务上下文接收到的内容已经失真。

本问题的最小复现输入是一份带BOM的UTF-16 Markdown文件，正文可使用“# UTF-16需求”和“用户上传后应正确显示中文”。修复前调用`parse_attachment_text("需求.md", data)`后，返回文本与原始正文不相等，并出现替换字符或不可读内容。期望行为是：带BOM的UTF-16 LE和UTF-16 BE文本附件都能还原为原始Unicode正文，现有UTF-8、UTF-8 BOM和GB18030附件行为保持不变。

该问题只涉及文本附件解码，不调整附件大小限制、50,000字符截断规则、DOCX或XLSX解析逻辑，也不改变任务API、数据模型、数据库结构和前端交互。

### 二、修复范围与技术要求

会议确认本次修复采用最小改动原则。生产代码仅允许修改`app/services/attachment_parser.py`，测试代码仅允许修改`tests/test_attachments.py`。本地执行者需要先阅读现有实现和测试，验证错误数据从`parse_attachment_text`进入`_decode_text`，再确认根因是UTF-16 BOM未被优先识别，而不是通过扩大容错范围或无条件猜测编码掩盖问题。

修复逻辑应在通用编码尝试之前检查UTF-16 BOM。只有数据以`FF FE`或`FE FF`开头时才使用`utf-16`解码，不对无BOM数据增加模糊编码猜测。原有`utf-8-sig`、`utf-8`、`gb18030`顺序及最终替换解码策略保持不变，避免扩大行为变化范围。

测试必须先于实现补充。新增测试至少覆盖一份带BOM的UTF-16 Markdown正文，断言解析结果与原始中文完全相等；建议使用参数化同时覆盖LE和BE两种字节序。测试还需要证明原有附件上传和上下文绑定用例继续通过。不得通过放宽断言、删除现有测试或仅判断结果非空来规避乱码问题。

本次任务不得修改无关文件，不得引入新的第三方依赖，不得访问外部服务，不得读取或输出API Key、数据库密码及其他敏感配置，也不得执行Git提交。若工作区已有与本任务无关的改动，本地执行者必须保留并避开这些改动。

### 三、本地Agent与Skill协同方式

本次任务不依赖预先注册的业务处理Agent ID。任务分发阶段必须创建一个人工修复子任务，并将处理人指定为用户ID`root`、名称“管理员”、角色`admin`。TaskHub Codex Runner以`root`身份轮询该人工节点，将任务正文、附件内容和上游上下文交给本地Codex处理；本地Codex完成代码检查、测试补充、最小修复和验证后，再由runner把处理结果回填任务中心。

本地Codex处理人工修复节点时，应先使用`superpowers:systematic-debugging`完成复现、数据流追踪和根因确认，再使用`superpowers:test-driven-development`执行“新增失败测试、确认失败、实现最小修复、确认通过”的过程。如果当前本地环境没有这些skill，本地Codex仍需按相同步骤执行，并在最终报告中说明skill不可用的情况。若从Codex侧发布或查询TaskHub任务，可以使用项目内置`taskhub-codex` skill；通过Web页面发布时不依赖该skill。

本地Codex只有在代码修改完成且目标测试通过后，才可以向runner返回`action=submit`和`decision=approved`。如果无法稳定复现、无法修改目标文件、测试仍然失败或发现需求与现状冲突，应返回`action=needs_human`或`action=failed`，不得使用模糊的通过结论。回填内容必须概括根因、实际修改文件和测试结果，供后续报告生成步骤使用。

任务中心收到本地runner的成功结果后，应继续完成上下文合并和最终报告生成。整个流程的必选交互是“TaskHub人工节点 -> 本地Codex Runner -> 本地Codex -> TaskHub结果回填”，不得用普通mock文本直接跳过本地处理节点。

### 四、测试验证与完成判定

本地执行者首先运行新增的单个回归测试，确认它在修复前因为UTF-16正文不相等而失败。完成最小修复后，再运行同一个测试并确认通过。随后执行以下目标测试，验证附件解析和任务上下文绑定没有回归：

```bash
.venv/bin/pytest -q tests/test_attachments.py
```

任务完成需要同时满足以下条件：带BOM的UTF-16 LE Markdown正文可以完整还原；带BOM的UTF-16 BE Markdown正文可以完整还原；原有UTF-8附件上传、DOCX解析、XLSX解析和任务上下文绑定测试继续通过；改动仅限约定的生产代码和测试文件；本地runner已回填实际处理结果；最终Markdown报告已经由系统受管交付机制生成。

若新增测试或现有附件测试存在失败，主任务不得标记为成功。失败结果需要保留实际命令、失败测试名称和错误摘要，并进入人工处理，而不是生成一份声称已经修复的报告。由于本次改动只针对BOM分支，不要求引入通用字符集检测库，也不要求处理没有BOM的UTF-16文件。

### 五、报告内容与输出要求

最终交付物必须是一份Markdown文件，文件名固定为`UTF16-Markdown附件解析Bug修复报告.md`。报告由任务中心的受管文件交付机制写入当前任务和当前执行对应的输出目录，不要求业务Agent调用`file_write`工具，也不得在任务文档中写死本机绝对输出路径。

报告正文必须包含问题摘要、修复前复现结果、根因分析、修改文件及修改说明、本地Agent与skill交互记录、执行命令及测试结果、影响范围、残余风险、回滚建议和最终结论。交互记录至少写明人工子任务ID或标题、本地runner处理人`root`、实际使用的skill、runner回填决策以及本地Codex是否完成了代码修改。测试记录必须列出实际运行的命令和通过数量，不得只写“测试正常”。

人工确认任务清单时，应使用以下合同。`deliverable_requirements`保持为空，避免重复建立报告校验项；成功标准集中为一项可审核标准；最终报告生成后无需再次进行人工验收，以便示例流程自动闭环。

```json
{
  "title": "修复UTF16 Markdown附件解析乱码",
  "description": "根据附件纪要完成UTF-16 BOM文本附件解析Bug的定位、测试、最小修复和报告输出，修复节点必须交给root本地Codex Runner处理。",
  "execution_mode": "async",
  "default_assignee_user_id": "root",
  "default_assignee_user_name": "管理员",
  "default_assignee_role": "admin",
  "contract": {
    "goal": "修复带BOM的UTF-16 Markdown附件在任务中心被静默解析为乱码的问题，并保持现有附件解析行为兼容。",
    "deliverable_goal": "交付一份包含根因、代码改动、本地Agent与skill交互记录及测试证据的Markdown修复报告。",
    "deliverable_kind": "file",
    "deliverable_format": "markdown",
    "deliverable_filename": "UTF16-Markdown附件解析Bug修复报告.md",
    "deliverable_requirements": [],
    "success_criteria": [
      {
        "id": "criterion_bug_fixed_and_reported",
        "description": "UTF-16 LE和BE回归测试及现有附件测试全部通过，且报告明确记录本地Codex Runner处理结果、skill使用情况、修改文件、测试证据和最终结论。"
      }
    ],
    "requires_human_acceptance": false
  }
}
```

### 六、任务发起与后续工作安排

发起任务时，应先将本纪要以UTF-8编码的`.md`附件上传到任务中心，再使用不超过50个字符的任务名称“修复UTF16 Markdown附件解析乱码”。任务诉求建议使用：“请将附件中的Bug修复事项作为一个主任务执行。必须创建root人工修复节点，由本地Codex Runner完成真实代码修复和测试；测试通过后生成约定的Markdown报告。”附件上传后需要把返回的附件ID绑定到任务请求，不能只在任务诉求中填写本地文件路径。

执行前应确认模型服务可用于意图识别、轮次规划和完成判定，并确认TaskHub Codex Runner已经使用实际可访问的TaskHub地址和用户ID`root`启动。本文档不保存、不猜测也不固定TaskHub服务地址。任务创建后仍需在人工确认阶段核对任务名称、任务描述、默认处理人和上述交付合同，确认后再以异步方式执行。

会议最后明确，本示例的重点不是扩大附件解析能力，而是验证一条可审查的真实软件开发闭环：Markdown会议纪要成功进入任务上下文，系统生成root人工修复节点，本地Codex Runner调用本地Codex和相关skill完成小范围代码修复，pytest提供可复核证据，任务中心汇总上下文并生成受管Markdown报告。各环节均有明确输入、输出和失败处理方式，满足条件后主任务应进入成功状态并保留最终报告产物。
