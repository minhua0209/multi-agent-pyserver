# TaskHub Runner 多实例设计

## 背景

当前 `taskhub-codex-runner` 只支持单实例运行。`start_runner.sh`、Python runner 和 Runner CLI 共同使用固定的 PID、日志、运行配置与 IPC 目录；直接为不同用户启动多个进程会产生端口冲突、PID 覆盖、日志混写、IPC 抢占和用户身份覆盖。

本设计支持在同一台开发机上为多个已存在的 TaskHub 用户启动独立 Runner Web 控制台，用于本机多用户审批流程模拟。

## 目标

- 每个实例绑定一个已存在的 TaskHub `user_id`。
- 每个实例使用显式指定且唯一的本机 UI 端口。
- 每个实例可以独立启动、查询状态和停止。
- 每个实例拥有独立 PID、日志、运行配置和 IPC 队列。
- 默认实例保持现有命令和运行目录兼容。
- 单个实例失败或停止不影响其他实例。
- TaskHub 后端短暂重启后，已启动 Runner 能继续重试而不是退出。

## 非目标

- 不自动创建 TaskHub 用户。
- 不把 Runner Web 控制台开放到局域网；默认继续监听 `127.0.0.1`。
- 不为 Web 控制台增加登录鉴权。
- 不引入 Docker、Supervisor 或其他进程管理器。
- 不同时维护多份全局 Codex Skill 身份配置。
- 不解决 TaskHub 服务端多 Runner 抢占同一人工子任务的租约问题；本次模拟要求每个实例使用不同 `user_id`。

## 当前约束

当前所有 Runner 共享以下路径：

```text
taskhub-codex-runner/runtime/runner.pid
taskhub-codex-runner/runtime/runner.log
taskhub-codex-runner/runtime/runner_runtime.json
taskhub-codex-runner/runtime/ipc/
```

此外，Runner 启动时会更新全局 Skill 文件：

```text
~/.codex/skills/taskhub-codex/taskhub_runtime.json
```

因此仅修改 UI 端口不足以实现可靠的多实例隔离。

## 实例标识

新增 `instance_id`。命名实例通过 `--instance <instance_id>` 指定。

实例名必须匹配：

```text
[A-Za-z0-9][A-Za-z0-9_-]{0,63}
```

该限制禁止路径分隔符、空白和 `..`，避免实例名逃逸运行目录。

未传入 `--instance` 时使用逻辑实例名 `default`，但继续使用现有单实例目录，确保向后兼容。

## 运行目录

默认实例保持现有布局：

```text
taskhub-codex-runner/runtime/
  runner.pid
  runner.log
  runner_runtime.json
  ipc/
    requests/
    responses/
```

命名实例使用独立目录：

```text
taskhub-codex-runner/runtime/instances/<instance_id>/
  runner.pid
  runner.log
  runner_runtime.json
  ipc/
    requests/
    responses/
```

`start_runner.sh` 负责解析实例并计算运行目录，再通过内部参数把目录传给 Python runner。Python 代码不再从固定全局路径推断当前实例。

## Runner 标识

未显式设置 `TASKHUB_RUNNER_ID` 时：

- 默认实例继续使用 `local-codex-runner`。
- 命名实例使用 `local-codex-runner-<instance_id>`。

显式设置的 `TASKHUB_RUNNER_ID` 保持最高优先级。

## 启动命令

保留现有 URL 和用户参数顺序，只新增实例参数：

```bash
./taskhub-codex-runner/start_runner.sh \
  http://127.0.0.1:8000 user_alice \
  --instance alice --ui --ui-port 8787 --background

./taskhub-codex-runner/start_runner.sh \
  http://127.0.0.1:8000 user_bob \
  --instance bob --ui --ui-port 8788 --background
```

规则：

- 默认实例启用 `--ui` 时仍默认使用端口 `8787`。
- 命名实例启用 `--ui` 时必须显式传入 `--ui-port`。
- UI 端口必须是 `1-65535` 的整数。
- UI 默认监听 `127.0.0.1`。
- 同一实例已运行时，重复启动直接返回当前 PID 和日志路径。
- 同一 `user_id` 已被其他运行实例使用时，新实例启动失败，避免重复领取和处理人工子任务。
- 不同实例使用同一端口时，新实例启动失败，已运行实例不受影响。
- 前台和后台模式都支持命名实例。

## 管理命令

默认实例继续支持原命令：

```bash
./taskhub-codex-runner/start_runner.sh status
./taskhub-codex-runner/start_runner.sh stop
```

命名实例使用：

```bash
./taskhub-codex-runner/start_runner.sh status alice
./taskhub-codex-runner/start_runner.sh stop alice
```

新增实例列表命令：

```bash
./taskhub-codex-runner/start_runner.sh list
```

`list` 输出以下非敏感字段：

- 实例名
- TaskHub 用户 ID
- Runner ID
- UI 地址
- 运行状态
- PID
- 日志路径

默认实例在列表中显示为 `default`。

停止实例前应确认 PID 仍对应当前实例的 Runner 进程。PID 文件存在但进程已退出，或 PID 已被其他进程复用时，只清理陈旧 PID 文件，不终止无关进程。

## Python Runner 配置

`RunnerConfig` 增加实例和运行目录信息。命令行增加内部可用参数：

```text
--instance <instance_id>
--runtime-dir <absolute-path>
```

`runtime_paths()` 根据当前运行目录返回 PID、日志、运行配置和 IPC 路径，禁止不同实例回退到共享 IPC。

每个实例的 `runner_runtime.json` 至少记录：

```json
{
  "instance_id": "alice",
  "server_url": "http://127.0.0.1:8000",
  "user_id": "user_alice",
  "runner_id": "local-codex-runner-alice",
  "ui_host": "127.0.0.1",
  "ui_port": 8787,
  "ipc_dir": "/project/taskhub-codex-runner/runtime/instances/alice/ipc"
}
```

运行配置不得写入 API Key、凭据或其他密钥。

## 用户校验

Runner 启动时继续通过 `/api/v1/users/current` 校验 `user_id`：

- 用户存在且响应 ID 一致时才能启动。
- 用户不存在或身份不匹配时启动失败。
- 启动脚本不自动创建用户，也不把展示名当作用户 ID。

## IPC 与 Runner CLI

每个 Runner 的命令 broker 只监听自身实例目录下的 IPC 队列。

默认实例继续兼容现有 Runner CLI 调用。命名实例可通过已有的 `--runtime-config` 参数显式选择：

```bash
python3 taskhub-codex-runner/runner_cli.py \
  --runtime-config taskhub-codex-runner/runtime/instances/alice/runner_runtime.json \
  list-tasks
```

Runner CLI 必须校验运行配置中的 `user_id` 与 broker 返回的当前用户一致，防止请求被错误实例处理。

## 全局 Codex Skill

全局 `taskhub-codex` Skill 只有一份 `taskhub_runtime.json`，不能同时代表多个用户。

为避免身份互相覆盖：

- 默认实例保留当前自动安装和更新 Skill 运行配置的行为。
- 命名实例启动时不自动写入全局 Skill 运行配置。
- 命名实例使用 `--install-skill` 时应明确报错，提示全局 Skill 只能绑定默认实例。
- 命名 Runner 自身处理人工子任务时仍可直接调用 Codex，不依赖全局 Skill 的 Runner CLI 身份。

多个独立 Codex 会话分别绑定不同 TaskHub 身份不属于本次范围；后续可基于实例级 wrapper 或会话级运行配置单独设计。

## 进程生命周期与错误处理

### 后台启动

启动脚本写入 PID 后执行有界的启动检查：

- 进程仍存活且 UI 成功绑定时报告启动成功。
- 用户校验失败、配置错误或端口占用导致进程退出时，清理无效 PID，并提示对应日志路径。
- 不删除其他实例的 PID、日志或运行配置。

### 运行期间后端中断

Runner 完成首次用户校验并启动后，轮询期间的临时网络异常不应终止进程：

- 将错误写入实例状态和日志。
- 按现有轮询间隔继续重试。
- 后续请求成功时清除临时错误状态。
- `--once` 模式仍在错误时返回失败，不进入无限重试。

### UI 端口冲突

Web 控制台必须在 Runner 进入主轮询前完成绑定。绑定失败应使当前实例启动失败，不能留下表面存活但无 UI 的进程。

## 本机访问

示例实例地址：

```text
alice: http://127.0.0.1:8787
bob:   http://127.0.0.1:8788
```

`0.0.0.0` 只作为服务端监听地址使用，不作为浏览器访问地址。本设计默认不使用 `0.0.0.0` 暴露 Runner UI。

## 测试设计

### Python 单元测试

在 `taskhub-codex-runner/test_taskhub_codex_runner.py` 中覆盖：

- 默认实例继续解析到原 `runtime/` 目录。
- 命名实例解析到 `runtime/instances/<instance_id>/`。
- 两个实例生成不同的运行配置和 IPC 路径。
- 默认与命名实例生成正确的 Runner ID。
- 非法实例名被拒绝。
- 命名实例不调用全局 Skill 运行配置写入。
- Runner CLI 使用显式运行配置连接正确 IPC。
- 后端轮询临时失败后继续运行并再次轮询。
- `--once` 模式遇到轮询错误时返回失败。

### 启动脚本测试

增加聚焦的脚本测试，覆盖：

- 命名实例启用 UI 但未指定端口时失败。
- 非法端口和非法实例名被拒绝。
- 两个运行实例不能使用相同的 `user_id`。
- `status <instance>` 读取正确 PID。
- `stop <instance>` 不影响其他实例。
- `list` 同时展示默认实例和命名实例。
- 陈旧 PID 不会导致无关进程被终止。
- 后台启动早期失败时清理 PID 并返回非零状态。

### 回归验证

运行：

```bash
pytest -q taskhub-codex-runner/test_taskhub_codex_runner.py
pytest -q taskhub-codex-runner/test_start_runner.py
bash -n taskhub-codex-runner/start_runner.sh
```

### 本机验收

使用两个已存在的 TaskHub 用户分别启动 `alice` 和 `bob`：

1. `http://127.0.0.1:8787` 和 `http://127.0.0.1:8788` 均返回页面。
2. 两个页面显示不同的用户 ID 和 Runner ID。
3. `list` 显示两个实例均为运行状态。
4. 停止 `alice` 后，`bob` 页面仍可访问。
5. 重启 8000 后端后，`bob` 保持运行并在后端恢复后继续轮询。
6. 各实例日志只包含自身用户和运行事件。

## 影响文件

- `taskhub-codex-runner/start_runner.sh`
- `taskhub-codex-runner/taskhub_codex_runner.py`
- `taskhub-codex-runner/README.md`
- `taskhub-codex-runner/test_taskhub_codex_runner.py`
- `taskhub-codex-runner/test_start_runner.py`（新增）

`runner_cli.py` 已支持 `--runtime-config`，除非实现过程中发现缺少实例路径传递能力，否则不修改。

## 兼容性

- 未使用 `--instance` 的现有启动、状态和停止命令保持有效。
- 默认实例继续使用原 PID、日志、运行配置和 IPC 路径。
- 默认实例继续管理全局 Codex Skill 配置。
- 现有配置文件字段保持有效，不要求迁移。
- 命名实例功能为增量能力，不改变 TaskHub API。

## 完成标准

实现完成必须同时满足：

- 两个不同用户的命名实例可以在不同端口稳定运行。
- 实例 PID、日志、运行配置和 IPC 完全隔离。
- 管理命令只能作用于指定实例。
- 默认单实例使用方式无行为回归。
- 后端短暂重启不会导致常驻 Runner 退出。
- 自动化测试和本机双实例验收通过。
