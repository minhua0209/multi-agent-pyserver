# TaskHub Codex Runner

轻量本地 runner，用于把 TaskHub 人工节点托管给本地 Codex 处理。

当前版本复用已有接口：

- `GET /api/v1/subtasks/human?assignee_user_id=...`
- `POST /api/v1/subtasks/{subtask_id}/result`

它不会把任务中心的普通 Agent 子任务分发给本地 Codex，而是拉取某个审批人的人工待办，由本地 Codex 生成人工处理意见，再回填为人工节点结果。

## 运行

复制配置：

```bash
cp config.example.json config.json
```

推荐用启动脚本显式传入任务中心地址：

```bash
./start_runner.sh http://192.168.170.18:8000 王大锤 --once
```

常驻运行：

```bash
./start_runner.sh http://192.168.170.18:8000 王大锤
```

启动 Web 控制台：

```bash
./start_runner.sh http://192.168.170.18:8000 王大锤 --ui
```

控制台地址：

```text
http://127.0.0.1:8787
```

后台运行 Web 控制台：

```bash
./start_runner.sh http://192.168.170.18:8000 王大锤 --ui --background
```

后台运行状态和停止：

```bash
./start_runner.sh status
./start_runner.sh stop
```

后台运行文件固定写入 runner 目录下：

```text
taskhub-codex-runner/runtime/runner.pid
taskhub-codex-runner/runtime/runner.log
```

只安装 Skill：

```bash
./start_runner.sh http://192.168.170.18:8000 王大锤 --install-skill
```

覆盖更新已安装 Skill：

```bash
./start_runner.sh http://192.168.170.18:8000 王大锤 --install-skill --update-skill
```

启动一次轮询：

```bash
python taskhub_codex_runner.py --config config.json --once
```

常驻轮询：

```bash
python taskhub_codex_runner.py --config config.json
```

也可以用环境变量覆盖配置。`TASKHUB_SERVER_URL` 应该在启动 runner 时由外部明确指定，表示当前 runner/Codex 能访问到的任务中心地址：

```bash
TASKHUB_SERVER_URL=http://127.0.0.1:8000 \
TASKHUB_USER_ID=王大锤 \
TASKHUB_RUNNER_ID=local-codex-runner \
TASKHUB_CODEX_COMMAND="codex exec" \
python taskhub_codex_runner.py --once
```

如果 TaskHub 对 runner 暴露的是局域网地址，就这样启动：

```bash
TASKHUB_SERVER_URL=http://192.168.170.18:8000 \
python taskhub_codex_runner.py --once
```

Skill 不应该自行猜测任务中心地址，也不应该保存 TaskHub 的真实 `server_url`。Runner 启动或安装 Skill 时，会把 Codex 需要知道的最小信息写入已安装 Skill 目录：

```text
~/.codex/skills/taskhub-codex/taskhub_runtime.json
```

内容示例：

```json
{
  "user_id": "王大锤",
  "runner_id": "local-codex-runner",
  "runner_cli_path": "/当前机器/当前项目/taskhub-codex-runner/runner_cli.py"
}
```

`runner_cli_path` 是 runner 启动或安装 Skill 时动态写入的当前机器真实路径。项目移动后，重新启动 runner 或重新安装 Skill 即可刷新该路径。

TaskHub 真实地址写入 runner 自己的本地运行配置：

```text
taskhub-codex-runner/runtime/runner_runtime.json
```

内容示例：

```json
{
  "server_url": "http://192.168.170.18:8000",
  "user_id": "王大锤",
  "runner_id": "local-codex-runner"
}
```

其他 Codex 会话访问任务中心时，应优先读取 Skill runtime 里的 `runner_cli_path`，通过 runner CLI 间接访问 TaskHub。CLI 会读取 runner 自己的 `runner_runtime.json`，因此 Skill 不需要知道 TaskHub 地址，也不需要访问宿主机上的 runner HTTP 端口。

默认启动时会检查并安装项目内置 Skill：

```text
source: taskhub-codex-runner/skill/taskhub-codex
target: ~/.codex/skills/taskhub-codex
```

如果目标目录已存在，默认不覆盖。需要覆盖时使用 `--update-skill` 或将配置 `auto_update_skill` 设置为 `true`。

## 调试

只打印传给 Codex 的 prompt，不提交结果：

```bash
python taskhub_codex_runner.py --config config.json --once --dry-run
```

如果想人工确认 Codex 结果后再提交，可以把配置中的 `auto_submit` 改为 `false`。脚本会打印待提交 payload。

如果使用 `--ui`，Codex 无法自动处理的人工节点不会再要求在终端输入，而是挂到 Web 控制台里。控制台可以查看 Codex 建议，并手动选择“通过并回填”或“驳回并回填”。

## Runner CLI

Codex Skill 默认通过 runner CLI 调用任务中心能力。这样可以避免 Codex 沙箱访问不到宿主机 `127.0.0.1` runner 进程的问题。

发布任务：

```bash
python3 "{runner_cli_path}" publish-task \
  --title "50字以内任务名称" \
  --content "用户的完整任务诉求"
```

复杂 payload 可以写入文件：

```bash
python3 "{runner_cli_path}" publish-task --payload-file /tmp/taskhub-payload.json
```

确认任务：

```bash
python3 "{runner_cli_path}" confirm-task \
  --task-id task_xxx \
  --title "确认后的任务清单标题" \
  --description "确认后的任务清单明细" \
  --execution-mode async
```

查询和取消：

```bash
python3 "{runner_cli_path}" list-tasks
python3 "{runner_cli_path}" get-task --task-id task_xxx
python3 "{runner_cli_path}" cancel-task --task-id task_xxx
```

CLI 输出 JSON。发布任务成功时会输出适合 Codex 展示的任务清单：

```json
{
  "ok": true,
  "request_id": "req_xxx",
  "tasks": [
    {
      "task_id": "task_xxx",
      "submitted_title": "用户提交标题",
      "draft_title": "识别出的任务清单",
      "draft_description": "- 查询客户需求\n- 管理员确认"
    }
  ]
}
```

如果 TaskHub 返回错误，CLI 会输出：

```json
{
  "ok": false,
  "error": "TaskHub API failed..."
}
```

Skill 看到 `ok=false` 后必须直接停止，不改写诉求、不重试。

## 本地 HTTP 控制台代理

Web 控制台仍保留本地 HTTP 发布代理，主要用于调试或页面调用：

```http
POST http://127.0.0.1:8787/api/tasks/requests
```

Codex Skill 默认不依赖这个 HTTP 代理。

## Codex 输出格式

Runner 要求 Codex 最终只输出 JSON：

```json
{
  "action": "submit",
  "decision": "approved",
  "output": "已审核方案，建议通过。"
}
```

`action` 可选：

- `submit`：Codex 判断可以直接提交。
- `needs_human`：Codex 无法判断，runner 会在当前终端要求人工选择通过、驳回或暂不处理。
- `failed`：Codex 执行失败，runner 不自动回填。

可选决策：

- `approved`：人工确认通过，任务中心继续后续流程。
- `rejected`：人工驳回，任务中心按流程模板进入返工分支。
- `need_more_info`：信息不足，当前按人工结果回填，后续可扩展为专门状态。

## 终端人工兜底

当 Codex 返回 `needs_human`、`failed` 或 `decision=need_more_info` 时，runner 不会直接回填任务中心，而是在运行 runner 的终端里提示：

```text
请输入：
1 = 通过
2 = 驳回
3 = 暂不处理
```

只有选择 `1` 或 `2` 并输入处理意见后，runner 才会回填任务中心。

## 后续可扩展

当前是单文件 MVP。后续如果任务中心补充专用托管接口，可以升级为：

- claim：服务端托管锁，避免多个本地 runner 重复处理。
- heartbeat：长任务心跳。
- logs：执行过程同步到 Dashboard。
- token：本地 runner 鉴权。

## Codex Skill 描述

目录下提供了一份可移植的 Skill 描述：

```text
skill/taskhub-codex/SKILL.md
```

这份 Skill 用于指导 Codex：

- 通过 TaskHub 接口发布任务。
- 发布后展示意图识别出来的 draft 任务清单，并在 Codex 对话里反问用户确认、修改或取消。
- 查询任务状态。
- 确认任务执行。
- 处理本地 runner 拉取到的人工待办。
- 将 Codex 生成的人工处理意见回填到 TaskHub。
