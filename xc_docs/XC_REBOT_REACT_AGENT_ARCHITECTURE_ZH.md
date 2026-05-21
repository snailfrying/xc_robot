# XC ReAct 底盘代理架构说明

## 1. 目标

本实现面向 `xc_rebot` 当前项目，重点解决三件事：

1. 底盘移动和点位导航统一纳入一个可扩展代理；
2. 每个 planner 输出后的 action 都按同步模式执行；
3. 所有关键参数统一收敛到配置文件，避免在逻辑代码里散落实数。

## 2. 参考思路

实现方式明确参考了 `m3pro_rgbd_guidance` 的几个核心模式：

- `contracts + prompts`：把 planner 的行为规则和输出约束显式化；
- `llm_interface`：封装 OpenAI-compatible 视觉/文本调用；
- `action parser`：planner 只返回一个函数式 action，运行时再做严格校验；
- `serial executor`：一次只执行一个原子动作，执行完成后再进入下一轮；
- `centralized config`：所有超时、轮询、pulse、模型参数统一配置。

## 3. 当前目录

```text
xc_rebot/
|-- README.md
|-- run_agent.py
|-- config/
|   |-- defaults.toml
|   `-- point_aliases.json
|-- tests/
|   |-- test_action_parser.py
|   |-- test_goal_router.py
|   `-- test_point_resolver.py
|-- xc_docs/
|   |-- ROBOT_CONSUMER_API_ZH_OPTIMIZED.md
|   |-- XC智能小R H20-X5 通信协议.md
|   `-- XC_REBOT_REACT_AGENT_ARCHITECTURE_ZH.md
`-- xc_rebot_agent/
    |-- __init__.py
    |-- constants.py
    |-- errors.py
    |-- logging_utils.py
    |-- models.py
    |-- settings.py
    |-- clients/
    |   |-- __init__.py
    |   |-- llm_client.py
    |   `-- robot_api.py
    |-- planner/
    |   |-- __init__.py
    |   |-- action_parser.py
    |   |-- contracts.py
    |   |-- llm_interface.py
    |   |-- point_resolver.py
    |   |-- prompts.py
    |   |-- react_planner.py
    |   `-- router.py
    |-- runtime/
    |   |-- __init__.py
    |   |-- executor.py
    |   `-- observer.py
    |-- utils/
    |   |-- __init__.py
    |   `-- text_utils.py
    `-- workflows/
        |-- __init__.py
        `-- react_agent.py
```

## 4. 运行链路

### 4.0 安全前置

当前 CLI 默认禁止任何 live 接口访问。

- 不加 `--allow-live` 时：
  - 不允许查询 `/status`
  - 不允许查询 `/points`
  - 不允许发 `/move/*`
  - 不允许发 `/navigate`
  - 不允许发 `/stop`
- 只有操作者明确传入 `--allow-live`，才允许真正访问机器人或服务端。

这样可以保证默认开发态、审查态、纯代码态不会误触机器人。

### 4.1 CLI 入口

当前除了命令行单次执行，也支持交互式 CLI：

- 单次执行：
  - `python run_agent.py --allow-live --goal "去工作点"`
- 交互式：
  - `python run_agent.py --allow-live --interactive`
- 交互式保留历史：
  - `python run_agent.py --allow-live --interactive --session-mode stateful`

交互式 CLI 目前默认是 `stateless`：

- 每次输入一句目标
- 每次独立执行
- 不把上一句历史送给 agent

但已经提前铺好了 `stateful` 模式：

- 历史 turn 会保存在 `runtime_logs/interactive_session.jsonl`
- 切到 `stateful` 后，会把最近若干轮摘要作为 `session_memory` 喂给 planner
- 这样后续可以逐步演进成综合决策式 agent

### 4.2 路由层

`GoalRouter` 先做轻量决策：

- 命中停止关键词 -> `stop(...)`
- 命中点位 -> `navigate(point_id)`
- 命中单一底盘动作关键词 -> `move_forward(...) / turn_left(...)`
- 其他目标 -> 进入 VLM ReAct 场景探索

### 4.3 点位优先

`PointResolver` 会先：

- 拉取 `/points`
- 合并本地 `config/point_aliases.json`
- 做确定性匹配
- 必要时用 LLM 做一次文本点位归一

只要点位足够可信，就直接导航，不再做手动探索。

### 4.4 VLM 探索

当目标不是明确点位时：

1. 调 `/camera/capture`
2. 把图片转成 data URL
3. 调视觉模型
4. 严格要求只返回一个 action
5. 运行时校验 action 合法性

### 4.5 同步动作执行

`SynchronousActionExecutor` 保证串行：

- 手动动作：
  - 先 `POST /move/*`
  - 轮询 `GET /status` 确认进入 `manual`
  - 等待配置的 `pulse_sec`
  - 发送 `POST /stop`
  - 轮询确认退出 `manual`
- 导航动作：
  - 先 `POST /navigate`
  - 轮询确认进入 `navigating` 或直接终态
  - 再轮询直到 `succeeded / failed / stopped`
- 停止动作：
  - 发 `POST /stop`
  - 轮询直到不再 `manual` 且不再 `navigating`

也就是说，下一个 planner step 的确会等上一个 action 完整结束后才开始。

## 5. 参数规范

所有关键运行参数统一放在 `config/defaults.toml`：

- 机器人 API 地址、超时、轮询周期
- capture 返回模式
- LLM 地址、key、model、超时
- planner 最大步数、置信度阈值
- point resolution 阈值
- 手动动作 pulse / settle
- stop 轮询超时
- 日志目录和轮转参数

同时支持：

- `.env`
- `XC_*` 环境变量
- `PPIO_*` 环境变量回退

## 6. 日志规范

### 6.1 常规日志

写入：

- 控制台
- `runtime_logs/xc_rebot_agent.log`

记录内容包括：

- session start / end
- route 决策
- LLM 请求开始 / 结束
- robot HTTP 请求开始 / 结束
- capture 成功
- action 执行阶段
- 异常和超时

当前日志已经按组件拆分 `logger name`，联调时能直接看出是哪个节点报的：

- `xc_rebot_react_agent.workflow.react_agent`
- `xc_rebot_react_agent.client.robot_api`
- `xc_rebot_react_agent.client.llm`
- `xc_rebot_react_agent.planner.point_resolver`
- `xc_rebot_react_agent.planner.scene`
- `xc_rebot_react_agent.runtime.observer`
- `xc_rebot_react_agent.runtime.executor`

也就是说，下午到实验室联调时，一眼就能区分：

- 是 API 调用失败
- 是点位解析失败
- 是 VLM planner 失败
- 是抓拍失败
- 是同步状态等待超时

### 6.2 Session trace

如果启用 `session_trace_enabled`，会把结构化事件写入：

- `runtime_logs/session_trace.jsonl`

事件类型包括：

- `session_start`
- `observation_ready`
- `planner_decision`
- `execution_result`
- `planner_phase_error`
- `execution_error`
- `session_end`

### 6.3 执行链路日志重点

底盘动作和导航的同步等待日志会明确打印：

- `executor step start`
- `manual action start`
- `navigation start`
- `stop action start`
- `wait status begin`
- `wait status progress`
- `wait status done`
- `wait status timeout`
- `executor step done`

因此如果现场卡住，基本可以直接从日志判断卡在：

1. 命令没发出去
2. 底盘状态没切到 `manual`
3. 导航没进入 `navigating`
4. 导航没进入终态
5. `stop` 后状态没有收敛

### 6.4 交互式会话文件

交互 CLI 额外会写一个会话历史文件：

- `runtime_logs/interactive_session.jsonl`

每条记录至少包含：

- `shell_session_id`
- `mode`
- `goal_text`
- `fed_to_agent`
- `summary`

其中：

- `fed_to_agent=false` 表示虽然记了历史，但这一轮是独立执行
- `fed_to_agent=true` 表示这一轮历史已经参与后续 planner 决策

## 7. 错误处理规范

和 `ROBOT_CONSUMER_API_ZH_OPTIMIZED.md` 对齐：

- `RobotApiError`：HTTP、业务 code 非 0
- `RobotProtocolError`：返回 JSON schema 不符合约定
- `PlannerError`：模型输出无效、点位非法、JSON 不合法
- `ObservationError`：抓拍或图片落地失败
- `ActionExecutionError`：同步等待超时、导航失败、状态不收敛

建议后续扩展时继续遵守：

1. 先区分是 API 错、协议错、planner 错还是执行错；
2. 日志里要带 step、action、status；
3. timeout 一律从配置读；
4. 业务层不要直接吞异常。

## 8. 当前扩展点

后续可以继续加：

- 更强的多轮目标理解
- 基于视觉识别的点位触发导航
- WebSocket 状态流
- 更完整的 point alias / area / type 语义匹配
- dry-run / replay / mock robot

## 9. 当前入口

```powershell
python run_agent.py --allow-live --status
python run_agent.py --allow-live --list-points
python run_agent.py --allow-live --goal "去工作点"
python run_agent.py --allow-live --goal "向前探索一下门口"
```

这版先保证核心逻辑正确、封装清晰、配置统一、同步动作严格，再往上叠更复杂的场景能力。


## 10. 2026-05 sequential ReAct update

This update changes the chassis agent from a mostly direct-routed flow into a stricter ordered ReAct loop.

- The router only splits the user goal into ordered subgoals and adds a matched map-point hint when one is reliable.
- The planner decides the next atomic action from the newest API status, the newest execution result, optional newest image evidence, and the current ordered subgoal.
- The executor remains fully synchronous: one API action is sent, the code waits for API/status confirmation, then the next planner turn starts.
- Multi-step goals such as `navigate to work, then return home` are no longer collapsed into one direct point match from the whole sentence.
- Stop versus continue is now owned by the planner decision and the newest result, instead of relying only on hard-coded direct routing.

Recommended reasoning contract for future changes:

1. newest status/result is the source of truth;
2. current subgoal only, never skip later subgoals early;
3. known map point first when it already satisfies the subgoal;
4. if no safe next action is justified, finish or stop instead of guessing;
5. keep all timing, polling, profile names, and thresholds in config only.
