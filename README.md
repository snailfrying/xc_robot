# xc_rebot

`xc_rebot` 是一个面向四轮底盘机器人的同步式 ReAct Agent。项目目标是基于大模型的任务理解与单步决策能力，构建一条可控、可解释、可验证的底盘任务执行链路。

本项目聚焦以下场景：

- 底盘导航到已知点位
- 显式底盘动作执行（前进、后退、左转、右转、停止）
- 基于 RGB + 深度图的视觉探索、目标搜索与安全前进
- 基于最新状态与观测结果的单步 ReAct 决策
- 多步骤任务的顺序拆解与串行执行

## 1. 设计目标

- 以四轮底盘控制为核心业务边界，不扩展为泛机器人平台
- 采用严格单步闭环：`Observe -> Reason -> Act -> Observe`
- 以 LLM 负责任务理解、任务拆解和当前步决策
- 仅保留最小确定性规则，用于稳定处理显式动作与安全边界
- 执行器同步等待真实结果，避免“连续下发、多步猜测”

## 2. 核心架构

系统由四个主要层次组成：

### 2.1 Router

文件：`xc_rebot_agent/planner/router.py`

职责：

- 将用户目标拆解为有序子任务（subgoals）
- 识别已知点位并附加导航提示
- 识别显式底盘动作并附加确定性动作提示

当前确定性规则已收敛到最小范围，规则文件位于：

- `xc_rebot_agent/planner/chassis_intent_rules.py`

保留的规则仅包括：

- 顺序连接词拆分
- 显式停止意图识别
- 显式底盘动作识别：前进、后退、左转、右转
- 探索/搜索类意图优先进入 `scene_exploration`，避免被“前进/转动”等词误路由为纯底盘直控

### 2.2 Planner

文件：`xc_rebot_agent/planner/react_planner.py`

职责：

- 基于当前子任务、最新机器人状态、最新执行结果和可选视觉观测，输出单个原子动作
- 约束决策只针对“当前子任务”，不跨步规划后续子任务
- 根据任务类型选择对应的推理 profile

当前支持的 planner profile：

- `navigation_sequence`
- `motion_sequence`
- `scene_exploration`

三类 profile 的职责边界如下：

- `navigation_sequence`：已知点位导航，优先使用 `navigate(point_id)`
- `motion_sequence`：显式底盘命令，按安全约束执行一步一观测
- `scene_exploration`：视觉探索、找目标、靠近目标、探索式前进

Planner 输出采用结构化动作格式：

```json
{
  "name": "navigate",
  "args": {
    "point_id": "work"
  }
}
```

### 2.3 Executor

文件：`xc_rebot_agent/runtime/executor.py`

职责：

- 执行一个且仅一个原子动作
- 调用机器人 API
- 等待状态确认动作完成、终止或失败
- 将执行结果返回给下一轮 Planner

支持的动作类型：

- `navigate(point_id)`
- `move_forward(profile_name, optional distance_m)`
- `move_backward(profile_name, optional distance_m)`
- `turn_left(profile_name, optional angle_deg)`
- `turn_right(profile_name, optional angle_deg)`
- `stop(reason_key)`
- `finish_task()`

其中：

- `navigate` 用于已知地图点位导航
- `move_*` / `turn_*` 通过预定义 profile 执行同步脉冲动作，并支持距离/角度标量
- `finish_task` 不向机器人发送控制命令，仅表示当前子任务完成

### 2.4 Workflow

文件：`xc_rebot_agent/workflows/react_agent.py`

职责：

- 组织完整会话生命周期
- 调用 Router 生成任务计划
- 针对每个子任务运行 ReAct 单步循环
- 调用 Planner 决策、调用 Executor 执行、记录 Trace 与历史
- 在完成、阻断或异常时安全结束会话

该模块是系统编排层，不承担语义理解或动作策略本体。

## 3. 执行流程

一次标准任务执行流程如下：

1. 接收用户目标
2. 获取地图点位并进行别名增强
3. 通过 Router 生成有序任务计划
4. 选择当前子任务
5. 获取最新状态、最新执行结果和可选观测
6. 通过 Planner 输出单步原子动作
7. 通过 Executor 执行动作并等待真实结果
8. 将执行结果写入历史并进入下一轮
9. 当前子任务完成后再进入下一个子任务
10. 全部完成或发生阻断时结束会话

关键约束：

- 每轮只允许一个动作
- 未获得真实执行结果前不得进入下一轮规划
- 当前子任务未完成前不得跳转后续子任务
- 历史信息仅作辅助，不能覆盖最新状态与最新观测

## 4. 安全策略

### 4.1 最小确定性规则

项目不采用大规模关键词硬编码路由。确定性逻辑仅用于处理：

- 明确停止请求
- 明确底盘动作请求
- 明确顺序连接关系

其余任务理解和单步决策由 LLM 在结构化上下文下完成。

### 4.2 Fail-Closed

当 Planner 无法给出可靠下一步时，系统优先返回：

- `stop(reason_key)`

而不是继续猜测执行。

### 4.3 探索场景保守策略

对于 `scene_exploration`：

- 无新鲜视觉证据时禁止直接前进
- 有结构化视觉结果时，优先使用深度/障碍物/安全标志做运动约束
- 无结构化视觉服务时，允许退化为 VLM + RGB/深度图探索，而不是直接禁止探索
- 前进置信度不足时优先降级为保守转向扫描
- 搜索门、盒子等目标时，优先转向建立目标方向；方向明确且通路安全时优先较大步前进，再进行小幅微调
- 无充分新证据时不得直接宣告任务完成

该策略用于降低四轮底盘在弱感知条件下的盲动风险。

### 4.4 实机保护

`run_agent.py` 默认阻止真实机器人与外部服务调用。只有显式传入 `--allow-live` 参数后，才允许发起真实状态、导航、停止和运动请求。

## 5. 配置说明

主要配置文件：

- `config/defaults.toml`：运行时主配置
- `config/point_aliases.json`：点位别名映射

配置项包括但不限于：

- Router / Planner / Executor 参数
- 手动动作 profile
- 相机抓拍与深度图返回策略
- 结构化视觉服务配置（可选）
- 轮询与超时配置
- 置信度阈值
- 导航与停止相关执行参数

当前与视觉探索相关的关键配置：

- `planner.history_window = 3`：传入模型的最近历史仅保留 3 条
- `robot_api.capture.include_depth = true`：抓拍默认同时请求深度图
- `planner.allow_vlm_exploration = true`：允许探索任务使用视觉输入
- `planner.allow_vlm_motion = true`：允许显式底盘命令在执行前做视觉校验
- `vision.enabled = false`：默认关闭结构化视觉服务；线下联调时可接入 YOLO + 深度 + LLM 服务

## 6. 提示词与约束

相关文件：

- `xc_rebot_agent/planner/prompts.py`
- `xc_rebot_agent/planner/contracts.py`

这些文件定义了 Planner 的结构化输出约束、动作边界、完成条件与安全行为要求，是项目提示词逻辑的核心实现之一。

## 7. 目录结构

```text
xc_rebot/
├─ config/
├─ docs/
├─ tests/
├─ xc_docs/
├─ xc_rebot_agent/
│  ├─ clients/
│  ├─ planner/
│  ├─ runtime/
│  ├─ utils/
│  └─ workflows/
├─ run_agent.py
└─ README.md
```

## 8. 快速开始

### 8.1 环境准备

- Python 3.11 及以上
- 可访问的机器人控制服务
- 正确配置的环境变量与 `config/defaults.toml`

### 8.2 常用命令

```powershell
cd D:\hope\xc_rebot

python run_agent.py --allow-live --status
python run_agent.py --allow-live --list-points
python run_agent.py --allow-live --goal "navigate to work"
python run_agent.py --allow-live --interactive
python run_agent.py --allow-live --interactive --session-mode stateful
```

探索与目标靠近示例：

```powershell
$env:XC_ROBOT_BASE_URL="http://10.10.91.86:8080/api/v1"

python run_agent.py --allow-live --goal "寻找门，必要时小角度转动并缓慢前进，告诉我门在那"
python run_agent.py --allow-live --goal "去前方盒子的位置"
python run_agent.py --allow-live --goal "继续到前方盒子的地方"
```

相机与深度图联调说明：

- `10.10.91.86` 机型支持相机接口，抓拍可返回 RGB 与深度图
- 默认 `base_url` 仍可配置为其他底盘；若联调相机探索，请显式覆盖到 `10.10.91.86`
- 若仅使用 RGB + 深度图而未接入结构化视觉服务，系统仍可执行探索，但安全策略会更保守

## 9. 测试

执行编译检查：

```powershell
python -m compileall run_agent.py xc_rebot_agent tests
```

执行单元测试：

```powershell
python -m unittest discover -s tests -v
```

## 10. 关键文件

- `run_agent.py`：CLI 入口
- `xc_rebot_agent/workflows/react_agent.py`：主工作流编排器
- `xc_rebot_agent/planner/router.py`：任务拆解与路由
- `xc_rebot_agent/planner/react_planner.py`：单步规划器
- `xc_rebot_agent/planner/chassis_intent_rules.py`：最小动作规则层
- `xc_rebot_agent/planner/action_parser.py`：结构化动作解析与校验
- `xc_rebot_agent/runtime/executor.py`：同步动作执行器
- `xc_rebot_agent/session_memory.py`：交互式会话记忆

## 11. 相关文档

- `xc_docs/XC_REBOT_REACT_AGENT_ARCHITECTURE_ZH.md`
- `docs/agent_control_technical_architecture.md`
- `docs/market_survey_and_solution_recommendation_2026.md`

## 12. 当前实现特点

相较于传统关键词拼接式控制逻辑，当前版本具备以下特点：

- 使用 LLM 进行任务级理解与当前步决策
- 使用结构化动作对象串联规划与执行
- 保留最小硬规则处理稳定业务边界
- 强制单步同步执行，符合底盘控制闭环要求
- 对探索类动作引入更严格的安全约束
- 支持 RGB + 深度图抓拍，并为后续结构化视觉服务预留接口
- 显式动作支持距离/角度拆步执行，便于一步一观察、一动作一校验
- 会话记忆做轻量压缩，保留日志，但传入模型的最近上下文限制为 3 条，图像只使用最新一帧

---

如需继续扩展，建议优先从以下方向演进：

- 将观测结果进一步压缩为可判定的安全语义摘要
- 细化 `scene_exploration` 的完成判据与转导航切换条件
- 增加更多基于真实底盘回放数据的回归测试
