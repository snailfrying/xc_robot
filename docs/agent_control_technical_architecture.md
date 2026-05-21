# Agent 控制的移动操作机器人技术架构文档

## 1. 文档目标

本文档重新基于当前仓库的真实代码能力，设计一套更贴近现状、也更方便后续扩展为 Agent 控制机器人的技术架构。

这次架构约束明确如下：

- Agent 模式采用 `ReAct` 思路，但 **ReAct 是一种 Agent 推理/行动模式，不是编程语言**
- 后端与 Agent 相关服务统一使用 `Python`
- 前端不使用 React，改为 **简单 HTML 页面**
- 重点不是做一个华丽前端，而是先把底盘、机械臂、任务编排三者理顺

本文档重点回答这些问题：

1. 当前机器人由哪些项目组成
2. 当前真实能支持哪些任务
3. 底盘建图、导航、RViz 在系统中的角色是什么
4. 为什么要分执行层、决策层、应用层
5. 各层之间的映射关系是什么
6. 如何用 Python Agent 统一调度底盘和机械臂
7. 如何保持代码结构清晰、职责解耦

---

## 2. 当前系统由什么组成

当前仓库里，和整机能力直接相关的主要是两个运行栈加一个参考栈：

- `XC-AGV-2.0`
- `Xc-openarm-2.0`
- `openarmx_ros2-6.0_basic`

其中推荐的理解方式是：

| 项目 | 定位 | 实际作用 |
|---|---|---|
| `XC-AGV-2.0` | 底盘运行栈 | 差速底盘驱动、里程计、IMU、雷达、EKF、SLAM、Nav2、底盘对外 HTTP/WebSocket 接口 |
| `Xc-openarm-2.0` | 机械臂运行栈 | 双臂 bringup、MoveIt、Commander、重力/科里奥利补偿、抓取脚本、位姿转换 |
| `openarmx_ros2-6.0_basic` | 机械臂参考栈 | 机械臂基础版参考实现，不建议作为最终运行主栈 |

因此，当前真正应该作为整机能力基座的是：

```text
XC-AGV-2.0 + Xc-openarm-2.0
```

而不是三个项目同时并列运行。

---

## 3. 当前机器人真实能支持哪些任务

这一部分非常重要。架构设计不能脱离当前代码实际能力。

## 3.1 当前已经具备的底盘能力

来自 `XC-AGV-2.0`，当前已经具备：

- 差速底盘速度控制
- 轮式里程计
- IMU 数据采集
- EKF 融合
- 激光雷达数据采集
- SLAM 建图
- 基于地图的自主导航
- 点位导航
- HTTP / WebSocket 对外控制接口

因此底盘当前支持的任务包括：

1. 手动遥控移动
2. 在已知地图上导航到目标点
3. 建图并保存地图
4. 查询当前导航状态
5. 紧急停车

## 3.2 当前已经具备的机械臂能力

来自 `Xc-openarm-2.0`，当前已经具备：

- 双臂硬件 bringup
- `ros2_control` 控制器
- MoveIt 双臂轨迹规划
- Commander 话题控制接口
- 双臂关节目标控制
- 双臂末端位姿目标控制
- 夹爪开合控制
- `pick_and_place.py` 这类顺序动作脚本
- `camera_to_base.py` 这类坐标变换辅助工具

因此机械臂当前支持的任务包括：

1. 回到命名姿态，例如 `home`
2. 指定关节角到位
3. 指定末端位姿到位
4. 夹爪打开 / 半闭合 / 闭合
5. 用脚本顺序执行预定义抓取动作

## 3.3 当前整机组合后能支持的任务

把底盘和机械臂组合起来，当前代码层面已经可以支持这些“可落地”的整机任务：

### 任务 A：导航到工位

流程：

- 上层调用底盘导航接口
- 机器人移动到指定点位
- 返回到位结果

### 任务 B：到位后执行预定义机械臂动作

流程：

- 先导航到工位
- 再调用机械臂命名动作、关节动作或位姿动作
- 完成抓取或放置

### 任务 C：固定工位的搬运流程

流程：

- 从 A 点导航到抓取位
- 机械臂抓取
- 导航到 B 点
- 机械臂放置

### 任务 D：带简单视觉辅助的抓取

前提是相机和检测算法可用。

流程：

- 获取相机识别结果
- 使用 `camera_to_base.py` 或等价逻辑进行位姿变换
- 机械臂移动到目标位姿执行抓取

## 3.4 当前还不具备或不完整的能力

当前仓库还没有完整实现以下内容：

- 一个统一的整机任务编排中心
- 一个统一的底盘 + 双臂整机模型
- 一键式整机仿真
- 完整的感知抓取闭环
- 多任务队列调度
- 强约束的行为树或技能树系统

因此当前更准确的定位是：

```text
已经具备底盘子系统 + 机械臂子系统 + 基本任务串联条件
但还没有形成真正统一的 Agent 机器人平台
```

---

## 4. 为什么要分三层

为了让 Agent 可以稳定控制机器人，必须把系统拆成：

- 执行层
- 决策层
- 应用层

原因很简单：

- 底盘和机械臂属于“怎么动”的问题
- Agent 属于“做什么、先做什么、失败怎么办”的问题
- 页面属于“给人看、给人点、给人管”的问题

如果不分层，最后就会变成：

- 页面直接调 ROS
- Agent 直接发 topic
- 业务逻辑散落在脚本中
- 出错时无法定位责任边界

所以三层不是为了形式，而是为了后期能维护、能调试、能扩展。

---

## 5. 三层架构重新定义

## 5.1 总体结构

```text
┌──────────────────────────────────────────┐
│                应用层                    │
│  HTML 控制台 / 任务页面 / 状态查看 / 日志 │
└──────────────────────────────────────────┘
                    │
                    │ HTTP / WebSocket
                    ▼
┌──────────────────────────────────────────┐
│                决策层                    │
│ Python Agent / ReAct / Task Orchestrator │
│ 技能封装 / 状态机 / 任务规划             │
└──────────────────────────────────────────┘
                    │
                    │ Python 能力调用接口
                    ▼
┌──────────────────────────────────────────┐
│                执行层                    │
│ 底盘适配器 / 机械臂适配器 / 感知适配器    │
│ Nav2 / MoveIt / ros2_control / 驱动      │
└──────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────┐
│              硬件与设备层                │
│ 底盘 / 电机 / 雷达 / IMU / 双臂 / 夹爪    │
└──────────────────────────────────────────┘
```

## 5.2 一个关键补充：RViz 不属于核心三层

RViz 很重要，但它 **不是核心控制链路的一层**。

更准确地说，RViz 是：

- 一个开发调试工具
- 一个运维可视化工具
- 一个执行层状态的观测窗口

所以在架构上，RViz 应该被视为：

```text
执行层的调试/可观测性工具
```

而不是业务系统的正式前端。

也就是说：

- 机器人没有 RViz 也可以执行任务
- 但工程师没有 RViz 会很难调试建图、导航、TF、机械臂轨迹

---

## 6. 执行层设计

执行层负责“动作真的发生”。

它直接面向 ROS 2、MoveIt、Nav2、驱动和硬件。

## 6.1 执行层职责

- 管理底盘驱动和机械臂驱动
- 暴露统一的底盘控制能力
- 暴露统一的机械臂控制能力
- 汇总底盘与机械臂状态
- 提供停止、取消、超时等控制

## 6.2 执行层在当前项目中的映射

| 执行层模块 | 当前项目对应 |
|---|---|
| 底盘驱动 | `my_robot_hardware` |
| 底盘控制器 | `diff_drive_controller` |
| 底盘定位融合 | `robot_localization` |
| 底盘建图 | `slam_toolbox` |
| 底盘导航 | `nav2_bringup` + `nav2_mppi_controller` |
| 底盘对外服务 | `xc_robot_server.py` |
| 机械臂驱动 | `openarmx_hardware` |
| 机械臂控制器 | `openarmx_bringup` / `ros2_control` |
| 机械臂规划 | `openarmx_bimanual_moveit_config` |
| 机械臂统一动作接口 | `openarmx_commander` |
| 机械臂补偿 | `openarmx_gravity_comp` |
| 感知辅助 | `camera_to_base.py` |

## 6.3 执行层内部建议拆分

建议新增一个统一执行包或 Python 服务，包含以下适配器：

### `BaseExecutor`

负责：

- 导航到 POI
- 导航到指定 pose
- 停车
- 查询底盘状态

建议方法：

```python
class BaseExecutor:
    async def navigate_to_poi(self, poi_id: str) -> dict: ...
    async def navigate_to_pose(self, x: float, y: float, yaw: float) -> dict: ...
    async def stop(self) -> dict: ...
    async def status(self) -> dict: ...
```

### `ArmExecutor`

负责：

- 命名动作
- 关节目标
- 位姿目标
- 夹爪动作
- 获取当前关节和末端状态

建议方法：

```python
class ArmExecutor:
    async def move_named(self, arm: str, target: str) -> dict: ...
    async def move_joints(self, arm: str, joints: list[float]) -> dict: ...
    async def move_pose(self, arm: str, pose: dict, cartesian: bool = False) -> dict: ...
    async def set_gripper(self, arm: str, state: str) -> dict: ...
    async def joint_state(self) -> dict: ...
    async def ee_pose(self, arm: str) -> dict: ...
```

### `PerceptionExecutor`

负责：

- 获取检测结果
- 相机位姿到基座位姿变换

建议方法：

```python
class PerceptionExecutor:
    async def detect(self) -> list[dict]: ...
    async def camera_to_base(self, arm: str, obj_pose: dict) -> dict: ...
```

### `RobotStateAggregator`

负责：

- 聚合底盘、机械臂、任务状态

建议方法：

```python
class RobotStateAggregator:
    async def snapshot(self) -> dict: ...
    async def health(self) -> dict: ...
```

## 6.4 执行层中的建图、导航、RViz角色

这是你特别关心的部分，单独说明。

### 6.4.1 建图

建图来自 `slam_toolbox`。

它在架构中的角色是：

- 属于执行层中的“空间建模能力”
- 作用是把雷达数据和位姿估计变成地图
- 输出给导航系统使用

它不是决策层，也不是前端功能本身。

决策层不会“自己建图”，而是调用执行层是否进入建图模式。

### 6.4.2 导航

导航来自 `Nav2`。

它在架构中的角色是：

- 属于执行层中的“移动能力”
- 接收目标位姿或目标点
- 输出速度控制到底盘

决策层只需要说：

- 去哪个点
- 去到哪一个 pose

而不应该关心：

- local costmap
- global planner
- controller server
- mppi 参数

### 6.4.3 RViz

底盘 RViz 中显示的地图、路径、雷达、机器人模型，本质上是：

- 建图和导航的调试可视化界面
- 用于观察执行层状态

所以在架构中：

- `地图构建` 是执行层能力
- `导航` 是执行层能力
- `RViz` 是执行层能力的可视化观察工具

它不应该作为应用层正式产品界面的一部分依赖。

如果以后做产品级页面，HTML 前端应该读取后端整理好的：

- 地图状态
- 当前位姿
- 当前目标点
- 导航状态
- 路径摘要

而不是指望用户直接用 RViz 完成业务操作。

---

## 7. 决策层设计

决策层负责“理解任务，并决定下一步调用什么能力”。

这里统一用 Python。

## 7.1 ReAct 在这里的正确位置

ReAct 不是语言，而是 Agent 工作模式。

它的含义是：

- Reason：先思考
- Act：再调用工具/能力
- Observe：观察结果
- 再继续思考

所以在本项目里，ReAct 应该放在：

```text
决策层的 Agent 运行方式
```

而不是放在前端，也不是放在 ROS 执行层。

## 7.2 决策层职责

- 接收自然语言或结构化任务
- 将任务拆成多个步骤
- 调用底盘和机械臂能力
- 处理成功、失败、重试和中止
- 维护任务状态机

## 7.3 决策层模块建议

### `AgentRuntime`

职责：

- 跑 ReAct 循环
- 调用技能
- 基于上下文作判断

建议方法：

```python
class AgentRuntime:
    async def run(self, instruction: str, context: dict) -> dict: ...
    async def step(self, state: dict) -> dict: ...
```

### `TaskPlanner`

职责：

- 把自然语言变成结构化任务

建议方法：

```python
class TaskPlanner:
    async def parse_task(self, instruction: str) -> dict: ...
```

### `TaskOrchestrator`

职责：

- 真正组织任务执行顺序

建议方法：

```python
class TaskOrchestrator:
    async def execute(self, task: dict) -> dict: ...
    async def pause(self, task_id: str) -> dict: ...
    async def resume(self, task_id: str) -> dict: ...
    async def cancel(self, task_id: str) -> dict: ...
```

### `SkillLibrary`

职责：

- 把复杂动作封装成稳定技能

技能建议：

- `navigate_to_station`
- `pick_from_station`
- `place_to_station`
- `return_home`
- `scan_scene`
- `emergency_stop`

技能接口建议：

```python
class Skill:
    async def run(self, params: dict, context: dict) -> dict: ...
```

## 7.4 决策层建议封装的方法

这一部分应该从“任务语义”出发，而不是从“ROS topic”出发。

推荐分三类。

### 第一类：基础能力方法

```python
async def go_to_poi(poi_id: str) -> dict
async def move_arm_home(arm: str) -> dict
async def move_arm_pose(arm: str, pose: dict) -> dict
async def set_gripper(arm: str, state: str) -> dict
async def get_robot_state() -> dict
```

### 第二类：组合技能方法

```python
async def pre_grasp(arm: str, pose: dict) -> dict
async def grasp_object(arm: str, pose: dict) -> dict
async def place_object(arm: str, pose: dict) -> dict
async def navigate_and_wait(poi_id: str) -> dict
```

### 第三类：任务级方法

```python
async def transport_object(pick_poi: str, place_poi: str, object_id: str) -> dict
async def inspect_station(poi_id: str) -> dict
async def fetch_and_deliver(pick_poi: str, place_poi: str) -> dict
```

## 7.5 当前机器人适合的决策层任务模板

基于现状，最适合先落地的是以下三类任务模板：

### 模板 1：导航任务

```text
输入：去 A 点
动作：调用底盘导航
输出：到位 / 失败
```

### 模板 2：导航 + 预定义机械臂动作

```text
输入：去 A 点并执行右臂抓取动作
动作：导航 -> 右臂预抓 -> 夹爪闭合 -> 抬起
输出：成功 / 失败
```

### 模板 3：双点搬运任务

```text
输入：从 A 拿到 B
动作：导航到 A -> 抓取 -> 导航到 B -> 放置
输出：成功 / 失败
```

这是最符合当前代码成熟度的切入方式。

---

## 8. 应用层设计

应用层不追求复杂，按你的要求，直接使用简单 HTML 页面即可。

## 8.1 应用层职责

- 给人输入任务
- 给人查看当前状态
- 给人操作启动、暂停、取消
- 给人看日志与错误

## 8.2 为什么前端只用简单 HTML 就够

当前阶段最重要的是：

- 任务跑通
- Agent 接底盘和机械臂能力
- 能看到执行进度

而不是前端炫技。

因此完全可以使用：

- HTML
- 少量 JavaScript
- 简单 CSS

只要能完成：

- 表单输入
- 状态刷新
- WebSocket 日志推送

就足够。

## 8.3 应用层页面建议

### 页面 1：任务面板

功能：

- 输入自然语言任务
- 提交任务
- 查看任务状态
- 暂停 / 恢复 / 取消

### 页面 2：机器人状态面板

功能：

- 当前底盘位置
- 当前导航状态
- 当前机械臂状态
- 当前夹爪状态
- 当前错误信息

### 页面 3：运维日志面板

功能：

- Agent 推理日志
- 动作执行日志
- 错误日志

## 8.4 应用层与决策层接口

建议应用层只连决策层 API。

### REST

```text
POST /tasks
GET  /tasks/{id}
POST /tasks/{id}/pause
POST /tasks/{id}/resume
POST /tasks/{id}/cancel
GET  /robot/state
GET  /robot/health
```

### WebSocket

```text
/ws/tasks
/ws/logs
/ws/state
```

## 8.5 简单 HTML 目录建议

```text
apps/simple-console/
├─ index.html
├─ task.html
├─ state.html
├─ logs.html
├─ css/
│  └─ style.css
└─ js/
   ├─ api.js
   ├─ task.js
   ├─ state.js
   └─ logs.js
```

---

## 9. 各层之间的映射关系

这是本次优化文档最关键的部分。

## 9.1 从“现有项目”到“新架构”的映射

### 执行层映射

| 现有代码 | 新架构角色 |
|---|---|
| `my_robot_hardware` | 底盘驱动执行模块 |
| `xc_robot_server.py` | 底盘执行能力接口原型 |
| `wit_ros2_imu` | 执行层传感器输入 |
| `lslidar_driver` | 执行层传感器输入 |
| `slam_toolbox` | 执行层建图能力 |
| `nav2_bringup` | 执行层导航能力 |
| `openarmx_hardware` | 机械臂驱动执行模块 |
| `openarmx_bimanual_moveit_config` | 机械臂规划执行模块 |
| `openarmx_commander` | 机械臂执行能力接口原型 |
| `openarmx_gravity_comp` | 机械臂控制增强模块 |

### 决策层映射

当前仓库还没有完整决策层，只存在一些雏形：

| 现有代码 | 可视为决策层雏形 |
|---|---|
| `pick_and_place.py` | 预定义顺序任务脚本 |
| `nav_api_control.py` | 简单导航任务脚本 |
| `camera_to_base.py` | 面向操作任务的辅助推理脚本 |

这些脚本说明：

- 决策层逻辑现在是分散的
- 还没有被统一成一个 Agent Orchestrator

### 应用层映射

当前应用层也没有真正独立出来：

| 现有代码 | 角色 |
|---|---|
| RViz | 调试和可视化观察工具 |
| `xc_robot_server.py` 提供的 HTTP API | 应用层入口雏形 |
| noVNC | 远程桌面运维工具 |

所以应用层目前还不是正式产品前端，只是原始控制入口。

## 9.2 从“层”到“调用方向”的映射

调用关系应该始终是：

```text
HTML 页面
  -> Python 决策服务
  -> Python 执行适配器
  -> ROS2 / Nav2 / MoveIt / 驱动
  -> 机器人硬件
```

而不是：

```text
HTML 页面 -> ROS Topic
Agent -> MoveIt / Nav2 内部对象
业务逻辑 -> 驱动
```

## 9.3 底盘建图和导航在层间的映射关系

以“建图”举例：

```text
应用层：用户点击“开始建图”
决策层：切换系统到建图模式
执行层：启动 slam_toolbox，接收雷达和位姿数据，持续生成地图
RViz：只负责可视化地图构建结果
```

以“导航到目标点”举例：

```text
应用层：用户输入去 work 点
决策层：生成 navigate_to_poi("work")
执行层：调用 Nav2 执行路径规划与跟踪
RViz：显示地图、路径、机器人位置和目标点
```

这就是“能力”和“可视化”的关系。

---

## 10. 推荐代码结构

这次优化后，代码结构建议进一步收敛，不再引入 React 相关目录。

推荐总目录结构：

```text
xc_rebot/
├─ XC-AGV-2.0
├─ Xc-openarm-2.0
├─ openarmx_ros2-6.0_basic
├─ docs/
├─ services/
│  ├─ agent_server/
│  │  ├─ main.py
│  │  ├─ api/
│  │  ├─ orchestrator/
│  │  ├─ agent/
│  │  ├─ skills/
│  │  ├─ executors/
│  │  └─ models/
│  └─ state_store/
├─ apps/
│  └─ simple-console/
│     ├─ index.html
│     ├─ css/
│     └─ js/
└─ ros2_extensions/
   ├─ robot_execution_bridge/
   └─ robot_task_msgs/
```

## 10.1 `services/agent_server` 内部建议

```text
agent_server/
├─ main.py                 # FastAPI 入口
├─ api/                    # REST / WebSocket
├─ agent/                  # ReAct Agent 逻辑
├─ orchestrator/           # 任务状态机和任务执行
├─ skills/                 # 技能库
├─ executors/              # 底盘/机械臂/感知适配器
├─ models/                 # Pydantic 数据模型
└─ utils/
```

## 10.2 `executors/` 建议

```text
executors/
├─ base_executor.py
├─ arm_executor.py
├─ perception_executor.py
├─ state_executor.py
└─ safety_guard.py
```

## 10.3 `skills/` 建议

```text
skills/
├─ navigate_to_station.py
├─ pick_object.py
├─ place_object.py
├─ transfer_object.py
└─ emergency_stop.py
```

---

## 11. 最合理的落地路线

## 11.1 第一阶段：先把当前能力统一成 Python 接口

目标：

- 不重写底盘和机械臂 ROS 包
- 先做统一执行适配器

工作：

1. 封装底盘导航接口
2. 封装机械臂 Commander 接口
3. 聚合机器人状态

## 11.2 第二阶段：加入 ReAct Agent 与任务状态机

目标：

- 让系统从“脚本调用”升级为“任务执行”

工作：

1. 新建 `TaskOrchestrator`
2. 新建 `SkillLibrary`
3. 新建 `AgentRuntime`

## 11.3 第三阶段：补简单 HTML 控制台

目标：

- 提供运维和人工调试入口

工作：

1. 编写任务提交页
2. 编写状态页
3. 编写日志页

## 11.4 第四阶段：整机统一模型

目标：

- 形成真正一体化移动操作机器人

工作：

1. 统一底盘与机械臂 TF
2. 统一 `robot_description`
3. 统一仿真与状态汇总

---

## 12. 最终结论

重新收敛之后，这套系统最合理的架构应该是：

### 执行层

使用当前现有成熟能力：

- `XC-AGV-2.0` 负责底盘、建图、导航、移动接口
- `Xc-openarm-2.0` 负责机械臂、MoveIt、Commander、抓取动作

### 决策层

新增 Python 服务：

- 使用 ReAct 作为 Agent 工作模式
- 使用任务状态机组织步骤
- 使用技能封装底盘与机械臂组合任务

### 应用层

使用简单 HTML 页面：

- 任务提交
- 状态查看
- 日志查看

### RViz 的位置

RViz 不是正式应用层，而是：

- 底盘建图与导航的可视化调试工具
- 机械臂姿态与轨迹的可视化调试工具
- 执行层的观测窗口

一句话总结：

当前最正确的演进方向，不是“直接让 Agent 控 ROS”，而是：

```text
先把底盘和机械臂统一封装为执行能力
再用 Python 的 ReAct Agent 在决策层调用这些能力
最后用简单 HTML 页面做任务与状态入口
```

这才是和当前代码最匹配、也最稳妥的技术路线。
