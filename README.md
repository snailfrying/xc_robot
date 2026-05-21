# xc_rebot ReAct 底盘 Agent

这是一个面向四轮底盘的同步式 ReAct Agent。  
目标不是做“关键词路由脚本”，而是做一条干净、可解释、可回放的闭环：

- LLM 负责理解任务、拆解任务、基于最新结果决定当前一步；
- 硬规则只负责极少数必须稳定的边界；
- 执行器一次只执行一个原子动作，并等待真实状态确认；
- 不确定就停，不靠猜测推进底盘。

## 当前设计目标

- 面向业务：四轮底盘控制，不追求泛机器人能力
- 结构干净：最小硬规则 + LLM 主导推理
- 严格 ReAct：Observe -> Reason -> Act -> Observe
- 安全优先：所有动作都基于“这一轮最新状态/最新执行结果/最新观测”
- 顺序执行：多步任务必须按 subgoal 串行完成，不能跳步

## 核心架构

### 1. `router`

位置：`xc_rebot_agent/planner/router.py`

职责：

- 把用户目标拆成有序 subgoal
- 对可直接识别的点位任务挂上 map-point hint
- 对显式底盘动作挂上 deterministic action hint

当前 router 的硬规则已经收窄，只保留：

- 有序任务拆分
- 显式停止意图
- 显式底盘动作意图：前进 / 后退 / 左转 / 右转

对应规则文件：

- `xc_rebot_agent/planner/chassis_intent_rules.py`

注意：

- router 不负责真正“做决策”
- router 不应该膨胀成大而杂的语义系统
- 除了少数稳定边界，理解任务仍优先交给 LLM

### 2. `planner`

位置：`xc_rebot_agent/planner/react_planner.py`

职责：

- 针对“当前 subgoal”输出一个原子动作
- 只根据最新状态、最新执行结果、可选最新图像做当前步推理
- 在三个 profile 中选择当前推理模式：
  - `navigation_sequence`
  - `motion_sequence`
  - `scene_exploration`

当前 planner 输出已经是结构化动作优先：

```json
{
  "name": "navigate",
  "args": {
    "point_id": "work"
  }
}
```

同时保留 `action_expression`，仅用于 trace 和兼容观察，不再作为内部主逻辑载体。

### 3. `executor`

位置：`xc_rebot_agent/runtime/executor.py`

职责：

- 执行 exactly one atomic action
- 调用机器人 API
- 等待状态机确认动作完成或失败
- 把执行结果回传给下一轮 planner

支持的原子动作：

- `navigate(point_id)`
- `move_forward(profile_name)`
- `move_backward(profile_name)`
- `turn_left(profile_name)`
- `turn_right(profile_name)`
- `stop(reason_key)`
- `finish_task()`

其中：

- `navigate` 用于地图点导航
- `move_*` / `turn_*` 是通过配置好的 profile 执行的同步脉冲动作
- `finish_task` 不会发送实际机器人动作，只表示当前 subgoal 已完成

## `react_agent.py` 是干什么的

位置：`xc_rebot_agent/workflows/react_agent.py`

这是整套 Agent 的主工作流编排器，不是“智能本体”，而是把整条链路按 ReAct 方式串起来。

它负责：

- 初始化 session
- 获取地图点和机器人状态
- 调用 router 生成 `task_plan`
- 顺序执行每个 subgoal
- 每轮调用 planner 决策一步
- 调用 executor 执行一步
- 记录 history 和 session trace
- 在完成、阻断或报错时安全退出

可以把它理解成：

- `router` 决定任务如何分段
- `planner` 决定这一轮做什么
- `executor` 负责真的执行
- `react_agent.py` 保证整条链路严格按“一步一观察”的 ReAct 闭环运行

## 当前安全策略

### 1. 最小硬规则

系统不再依赖大量关键词堆砌的业务逻辑。  
现在只有这些 deterministic 边界：

- 显式 stop 请求
- 显式底盘原子动作
- 有序连接词拆分

其余任务理解、任务细分、探索判断，交给 LLM 和结构化上下文。

### 2. 结构化动作串联

现在内部优先传递：

```json
{
  "name": "...",
  "args": {}
}
```

这样比单纯拼接字符串更稳定，便于：

- 校验动作合法性
- 做 guardrail
- 回放 trace
- 后续扩展更多动作类型

### 3. 探索场景保守前进

针对四轮底盘，`scene_exploration` 增加了保守护栏：

- 没有新鲜视觉证据时，不允许直接 `move_forward`
- 前进置信度不够时，优先降级成保守转向扫描
- 没有足够新证据支持完成时，不允许随意 `finish_task`

这保证了探索不是“盲目前冲”，而是先看、再动、再看。

### 4. Fail-closed

如果 planner 无法给出可靠下一步，系统优先：

- `stop(reason_key)`

而不是靠猜测继续推进底盘。

## ReAct 执行流程

一次完整 session 大致如下：

1. 读取用户目标 `goal_text`
2. 拉取地图点并做点位增强
3. router 生成有序 `task_plan`
4. 选中当前 subgoal
5. 获取最新状态 / 最新执行结果 / 可选最新观测
6. planner 产出一个原子动作
7. executor 同步执行并等待真实结果
8. 将结果回灌到下一轮
9. 当前 subgoal 完成后再进入下一个 subgoal
10. 全部完成或遇到 stop / error 时结束

关键原则：

- 一次只走一步
- 每步后必须等真实结果
- 当前 subgoal 不完成，不能跳下一个
- 历史记录只能做辅助，不能覆盖最新状态

## 配置与提示词

### 主要配置

- `config/defaults.toml`
  - timing
  - polling
  - confidence threshold
  - manual motion profiles
  - routing / planner / executor 参数

### 点位别名

- `config/point_aliases.json`

### 提示词与 contract

- `xc_rebot_agent/planner/prompts.py`
- `xc_rebot_agent/planner/contracts.py`

这里定义了 planner 的行为边界，包括：

- 当前 subgoal 只能推进一步
- completion 必须基于最新证据
- scene exploration 不能在证据弱时盲目前进
- motion profile 必须使用配置中的合法 profile 名称

## 关键文件

- `run_agent.py`：CLI 入口
- `xc_rebot_agent/workflows/react_agent.py`：主工作流编排器
- `xc_rebot_agent/planner/router.py`：任务拆分与 hint 挂接
- `xc_rebot_agent/planner/chassis_intent_rules.py`：最小显式底盘规则
- `xc_rebot_agent/planner/react_planner.py`：单步规划器
- `xc_rebot_agent/planner/action_parser.py`：结构化动作解析与校验
- `xc_rebot_agent/runtime/executor.py`：同步执行器
- `xc_rebot_agent/session_memory.py`：交互式会话记忆
- `config/defaults.toml`：统一运行配置
- `xc_docs/XC_REBOT_REACT_AGENT_ARCHITECTURE_ZH.md`：补充架构说明

## Quick Start

```powershell
cd D:\hope\xc_rebot
python run_agent.py --allow-live --status
python run_agent.py --allow-live --list-points
python run_agent.py --allow-live --goal "navigate to work"
python run_agent.py --allow-live --interactive
python run_agent.py --allow-live --interactive --session-mode stateful
```

## Live Safety Guard

- `run_agent.py` 默认阻止真实机器人和真实服务访问
- 只有显式传入 `--allow-live` 才会发起真实状态、导航、停止、运动等调用
- 这样做是为了在调试、审查代码、查看 CLI 行为时保持安全

## Interactive CLI

- `--interactive`：进入逐轮交互
- `--session-mode stateless`：每轮互相独立
- `--session-mode stateful`：把最近会话压缩后回灌给 agent

状态式交互记忆文件：

- `runtime_logs/interactive_session.jsonl`

## 当前这版相较旧逻辑的核心变化

- 删除了过宽的“语义硬规则层”
- 去掉了多余的 planner profile selector 逻辑
- 改成结构化动作优先
- 强化了 scene exploration 的底盘安全约束
- 保留字符串 action 仅用于 trace / 兼容，不再作为主判断依据

---

如果你接下来继续优化，建议优先看这几个点：

- `react_agent.py` 的主循环是否还可再瘦身
- `react_planner.py` 的探索完成判据是否还需更严格
- 观测结果是否应先做“安全语义摘要”再喂给 planner
