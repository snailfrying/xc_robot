# 三个项目说明与机器人整体串联文档

## 1. 文档目的

本文档用于说明当前仓库中的三个项目分别负责什么、它们之间是什么关系、在一台基于 ROS 2 的机器人上如何串联，以及后续如何部署和使用。

当前目录中的三个项目是：

- `XC-AGV-2.0`
- `openarmx_ros2-6.0_basic`
- `Xc-openarm-2.0`

从代码结构和功能划分来看，这不是三个互不相关的项目，而是围绕“移动操作机器人”形成的三部分能力：

- 底盘移动与导航
- 机械臂运动控制与规划
- 机械臂项目的基础版与增强版演进关系

最核心的结论先写在前面：

1. `XC-AGV-2.0` 是底盘项目，负责移动、定位、导航和对外 API。
2. `openarmx_ros2-6.0_basic` 是 OpenArmX 机械臂 ROS 2 基础版。
3. `Xc-openarm-2.0` 是在基础版之上做了增强和项目化改造的机械臂版本，更适合作为实际运行栈。
4. 真正部署时，推荐使用 `XC-AGV-2.0 + Xc-openarm-2.0`。
5. `openarmx_ros2-6.0_basic` 更适合作为参考代码、基础版本和对照版本保留，不建议和 `Xc-openarm-2.0` 一起作为同一套机械臂运行包编译。

---

## 2. 三个项目的定位

| 项目 | 角色 | 主要职责 | 建议用途 |
|---|---|---|---|
| `XC-AGV-2.0` | 移动底盘项目 | 差速底盘驱动、IMU 融合、激光雷达、SLAM/Nav2、自主导航、HTTP/WebSocket 接口 | 作为底盘运行栈 |
| `openarmx_ros2-6.0_basic` | 机械臂基础项目 | OpenArmX 双臂硬件驱动、`ros2_control`、MoveIt 配置、基础 bringup | 作为基础参考版本 |
| `Xc-openarm-2.0` | 机械臂增强项目 | 在基础版上增加 Commander、补偿控制、抓取脚本、位姿工具、自定义接口 | 作为机械臂实际运行栈 |

可以简单理解为：

- `XC-AGV-2.0` 解决“机器人怎么走”。
- `Xc-openarm-2.0` 解决“机械臂怎么动、怎么抓”。
- `openarmx_ros2-6.0_basic` 解决“机械臂原始框架是什么样”，相当于基础母版。

---

## 3. 对三个项目的理解

## 3.1 `XC-AGV-2.0`：底盘、定位、导航与上位机接口

这个项目是一套完整的 ROS 2 差速底盘系统，不只是底层驱动，还包括导航和接口服务。

### 3.1.1 硬件层

从 `my_robot_description/urdf/xiaoche_shiyan_control.urdf.xacro` 可以看出，该平台至少包含：

- `base_footprint` / `base_link`
- 左右驱动轮
- 激光雷达安装位
- IMU 安装位
- 相机安装位

说明它是一台带传感器的移动底盘，而不是简单的两轮控制示例。

### 3.1.2 控制层

从 `my_robot_description/urdf/xiaoche_hardware.ros2_control.xacro` 和 `my_robot_hardware/src/mobile_base_hardware_interface.cpp` 可以看出：

- 底盘通过 `ros2_control` 接入 ROS 2。
- 硬件插件是 `mobile_base_hardware/MobileBaseHardwareInterface`。
- 实际电机控制器驱动是 `ZLAC8015DCanDriver`。
- 控制命令接口是左右轮速度接口。
- 通信方式是 SocketCAN，默认走 `can0`。

底盘执行链路可以概括为：

```text
/cmd_vel
  -> diff_drive_controller
  -> ros2_control_node
  -> MobileBaseHardwareInterface
  -> ZLAC8015DCanDriver
  -> CAN 总线电机控制器
  -> 左右驱动轮
```

### 3.1.3 感知与导航层

该项目已经把导航能力搭完整了，包括：

- `wit_ros2_imu`：IMU 数据采集
- `robot_localization`：EKF 融合
- `lslidar_driver`：激光雷达驱动
- `slam_toolbox`：建图
- `nav2_bringup`：导航框架
- `nav2_mppi_controller`：局部控制器

从 `my_robot_bringup/config/nav2_params.yaml` 可见：

- 全局规划器使用 `ThetaStarPlanner`
- 局部控制器使用 `MPPIController`
- 运动模型是 `DiffDrive`

因此这个仓库承担的是完整移动能力：

```text
里程计 + IMU + 激光雷达
  -> EKF / AMCL / SLAM
  -> Nav2
  -> /cmd_vel
  -> 底盘执行
```

### 3.1.4 上位机接口层

`my_robot_bringup/scripts/xc_robot_server.py` 提供了很重要的对外接口：

- HTTP REST，端口 `8080`
- WebSocket，端口 `8081`
- 导航到点位：`/api/v1/navigate`
- 停车：`/api/v1/stop`
- 状态查询：`/api/v1/status`

这意味着底盘已经不是只能通过 ROS 节点操作，而是可以被上位机、网页端或业务系统直接调用。

---

## 3.2 `openarmx_ros2-6.0_basic`：机械臂基础版

这个项目是 OpenArmX 双臂系统的基础版 ROS 2 仓库。

### 3.2.1 它包含什么

主要包含这些核心包：

- `openarmx_hardware`
- `openarmx_bringup`
- `openarmx_bimanual_moveit_config`
- `openarmx_gravity_comp`
- `openarmx_preview_bringup`

说明它已经具备机械臂系统最核心的三层：

1. 硬件驱动层
2. 控制器与启动层
3. 运动规划层

### 3.2.2 它负责什么

该项目主要解决：

- 双臂硬件如何通过 CAN 接到 ROS 2
- 双臂控制器如何通过 `ros2_control` 启动
- MoveIt 如何对双臂做规划
- RViz 如何可视化机械臂状态

因此它更像一个“原始平台版”或者“官方基础版”。

### 3.2.3 机械臂控制链路

它的控制链路可以理解为：

```text
MoveIt / 控制命令
  -> joint_trajectory_controller 或 forward_position_controller
  -> ros2_control
  -> openarmx_hardware
  -> 机械臂 CAN 驱动
  -> 双臂与夹爪
```

### 3.2.4 它在整体里的角色

在当前三个项目中，它主要起到两个作用：

- 作为机械臂系统的基础参考版本
- 作为 `Xc-openarm-2.0` 的上游思路和代码基线

---

## 3.3 `Xc-openarm-2.0`：机械臂增强版

这个项目是在基础版之上做了更贴近项目落地的增强。

### 3.3.1 相比基础版增加了什么

从目录和 README 可以看出，增强点主要包括：

- `openarmx_commander`
- `openarmx_interfaces`
- 增强版 `openarmx_gravity_comp`
- `pick_and_place.py`
- `camera_to_base.py`
- 更细的关节运动约束配置

它不只是“能动”，而是更接近“能完成任务”。

### 3.3.2 Commander 的意义

`openarmx_commander/src/moveit_commander.cpp` 的作用非常关键：它把 MoveIt 的调用封装成直接可用的话题接口。

实际可以直接发布的话题包括：

- `left_arm_named_target`
- `left_arm_joint_target`
- `left_arm_pose_target`
- `right_arm_named_target`
- `right_arm_joint_target`
- `right_arm_pose_target`
- `left_gripper_named_target`
- `right_gripper_named_target`

并且提供动作完成反馈：

- `moveit_execution_done`

这让上层任务不需要自己再写复杂的 MoveIt C++ 代码，只要发 ROS 话题就能驱动机械臂动作。

### 3.3.3 补偿控制的意义

增强版中的 `openarmx_gravity_comp` 不只是重力补偿，还加入了：

- 科里奥利项补偿
- 可运行时调参
- 前馈 effort 控制链路

这对双臂装在移动底盘上的场景尤其有意义，因为机械臂运动质量会直接影响整机稳定性和抓取效果。

### 3.3.4 脚本层的意义

该项目还带了两个很典型的任务辅助脚本：

- `pick_and_place.py`：顺序执行抓取流程
- `camera_to_base.py`：把相机检测结果变换到机械臂基坐标系

这说明它已经开始面向“任务执行层”，而不只是驱动层。

---

## 4. 三个项目之间的关系

这三个项目的关系可以概括为：

```text
openarmx_ros2-6.0_basic
        ↓
   （机械臂基础版）
        ↓
Xc-openarm-2.0
   （机械臂增强版）

XC-AGV-2.0
   （底盘与导航）
```

然后在系统层面，底盘项目和机械臂项目被任务层串联起来，形成移动操作机器人。

也就是说：

- `openarmx_ros2-6.0_basic` 和 `Xc-openarm-2.0` 是“基础版”和“增强版”的关系。
- `XC-AGV-2.0` 和机械臂项目是“移动端”和“操作端”的关系。

---

## 5. 这是一个什么机器人系统

把三个项目合起来看，它对应的是一台典型的 ROS 2 移动操作机器人。

可以分成四层来理解。

### 5.1 设备层

- 差速底盘
- 双臂机械臂
- 夹爪
- IMU
- 激光雷达
- 可选相机

### 5.2 驱动与控制层

- `my_robot_hardware` 负责底盘硬件接口
- `openarmx_hardware` 负责机械臂硬件接口
- `diff_drive_controller` 负责底盘速度控制
- `joint_trajectory_controller` / `forward_position_controller` 负责机械臂控制
- `openarmx_gravity_comp` 负责机械臂前馈补偿

### 5.3 规划与感知层

- `robot_localization`、AMCL、SLAM、Nav2 负责底盘定位导航
- MoveIt 2 负责机械臂轨迹规划
- 相机相关工具负责目标位姿变换

### 5.4 任务编排层

- `xc_robot_server.py` 提供底盘任务入口
- `openarmx_commander` 提供机械臂动作入口
- 上层任务节点负责把两者串起来

---

## 6. 三者如何串联起来

三者串联的本质，不是底盘直接控制机械臂，也不是机械臂直接控制底盘，而是由上层任务逻辑统一调度：

```text
任务系统
  -> 先导航
  -> 到位
  -> 再机械臂执行抓取/放置
  -> 必要时再继续导航
```

### 6.1 最小可运行串联方式

最直接的串联方式是：

1. 启动底盘系统
2. 启动机械臂系统
3. 编写一个任务节点，或者由上位机按顺序调用

推荐调用方式：

- 底盘：调用 `XC-AGV-2.0` 的 HTTP / WebSocket 接口
- 机械臂：发布 `Xc-openarm-2.0` 的 Commander 话题

示例流程：

```text
步骤1：POST /api/v1/navigate 到目标工位
步骤2：等待 /api/v1/status 或 WebSocket 返回 arrived
步骤3：发布 /right_arm_pose_target 或 /right_arm_joint_target
步骤4：发布 /right_gripper_named_target
步骤5：监听 /moveit_execution_done
步骤6：完成后再导航去下一个点位
```

### 6.2 完整工程化串联方式

如果要真正做成“一台机器人”，还需要进一步统一：

- TF 树
- `robot_description`
- `joint_states`
- 机械臂安装位姿
- 感知到机械臂的坐标变换

也就是说，需要做一份“整机模型”，而不是简单把两个系统并行启动。

---

## 7. 集成时必须注意的问题

## 7.1 两个机械臂仓库不要一起作为运行包编译

`openarmx_ros2-6.0_basic` 和 `Xc-openarm-2.0` 里有大量同名包，例如：

- `openarmx`
- `openarmx_hardware`
- `openarmx_bringup`
- `openarmx_bimanual_moveit_config`

所以：

- 可以同时保留在仓库里；
- 但不能一起作为同一工作空间里的正式运行包去 `colcon build`。

推荐只选 `Xc-openarm-2.0` 作为机械臂运行版本。

## 7.2 CAN 口默认会冲突

当前默认配置里：

- 底盘默认用 `can0`
- 机械臂右臂默认也用 `can0`
- 机械臂左臂默认用 `can1`

如果三套硬件挂在同一台主机上，这会冲突。

建议重新规划：

- `can0`：底盘
- `can1`：右臂
- `can2`：左臂

## 7.3 `base_link` 和整机坐标系需要统一

当前底盘和机械臂各自都有自己的根坐标命名习惯。

如果只是做最小串联，可以先不改整机模型；
但如果要做真正的一体化移动操作机器人，就必须明确：

- 机械臂安装在底盘哪个位置
- 机械臂根坐标相对底盘 `base_link` 的固定变换
- 整个系统最终统一使用哪一套 TF 树

## 7.4 接口消息不要混用

当前仓库有两套相似消息：

- `my_robot_interfaces`
- `openarmx_interfaces`

建议：

- 底盘侧使用 `my_robot_interfaces`
- 机械臂侧使用 `openarmx_interfaces`

尤其 `JointCommand` 不要混用，因为机械臂增强版是 7 轴定义。

---

## 8. 推荐部署方案

推荐把当前目录理解成“源码总目录”，但真正运行时建立一个单独工作空间，只放正式运行需要的项目。

推荐运行组合：

- `XC-AGV-2.0`
- `Xc-openarm-2.0`
- `openarmx_description` 及必要依赖

不建议把 `openarmx_ros2-6.0_basic` 再一起并入正式运行工作空间。

### 8.1 推荐工作空间结构

```text
xc_mobile_manipulator_ws/
└─ src/
   ├─ XC-AGV-2.0
   ├─ Xc-openarm-2.0
   └─ openarmx_description 等依赖
```

### 8.2 推荐环境

- Ubuntu 22.04
- ROS 2 Humble
- SocketCAN
- `colcon`
- `rosdep`

### 8.3 编译建议

底盘项目 README 已说明：

- 如果是 ARM64 平台，`nav2_mppi_controller` 建议按项目说明单独源码编译。

机械臂项目则按 `Xc-openarm-2.0` 的 README 安装 CAN 驱动和依赖后编译。

---

## 9. 推荐启动顺序

## 9.1 底盘启动

推荐顺序：

1. 启动底盘硬件
2. 启动 IMU
3. 启动 EKF
4. 启动激光雷达
5. 启动 Nav2
6. 启动底盘 HTTP 服务

典型命令：

```bash
ros2 launch my_robot_bringup xiaoche_hardware.launch.xml
ros2 run wit_ros2_imu wit_ros2_imu
ros2 launch my_robot_bringup ekf.launch.py
ros2 launch lslidar_driver lsm10p_uart_launch.py
ros2 launch nav2_bringup bringup_launch.py use_sim_time:=False map:=/home/USER/maps/xc_room1.yaml params_file:=/path/to/nav2_params.yaml
python3 /path/to/XC-AGV-2.0/my_robot_bringup/scripts/xc_robot_server.py
```

## 9.2 机械臂启动

推荐直接使用增强版：

```bash
ros2 launch openarmx_bimanual_moveit_config demo.launch.py \
  right_can_interface:=can1 \
  left_can_interface:=can2 \
  control_mode:=mit \
  enable_forward_effort:=true
```

再启动 Commander：

```bash
ros2 launch openarmx_bringup moveit_commander.launch.py
```

## 9.3 整机启动顺序建议

推荐整体顺序如下：

1. 配置好所有 CAN 接口
2. 启动底盘
3. 启动底盘定位与导航
4. 启动底盘对外服务
5. 启动机械臂 MoveIt 与控制器
6. 启动机械臂 Commander
7. 最后启动任务编排节点

---

## 10. 一个完整任务如何执行

以“到工位抓取”为例，整机任务流可以这样理解：

### 第一步：导航到工位

调用底盘接口：

```bash
curl -X POST http://<robot_ip>:8080/api/v1/navigate \
  -H "Content-Type: application/json" \
  -d '{"point_id": "work"}'
```

### 第二步：等待导航完成

通过：

- `/api/v1/status`
- WebSocket

判断 `nav.state` 是否为 `arrived`。

### 第三步：控制机械臂到抓取位

例如发布右臂关节目标：

```bash
ros2 topic pub --once /right_arm_joint_target openarmx_interfaces/msg/JointCommand \
  "{joint_positions: [0.393010, 0.030114, -0.000192, 0.251841, 0.089190, 0.045074, 0.941959]}"
```

### 第四步：控制夹爪

```bash
ros2 topic pub --once /right_gripper_named_target example_interfaces/msg/String \
  "{data: 'half_closed'}"
```

### 第五步：等待动作完成

监听：

- `/moveit_execution_done`

然后进入下一步，例如抬臂、运输、放置。

---

## 11. 后续最推荐补的一层：统一任务编排节点

如果后续要把系统做成真正好用的项目，最建议新增一个统一任务层包，例如：

- `mobile_manipulator_task`
- `xc_robot_orchestrator`

它负责：

- 调用底盘导航
- 调用机械臂动作
- 接入视觉结果
- 做任务状态机
- 处理超时、失败和重试

推荐状态机大致如下：

```text
IDLE
 -> NAVIGATING_TO_PICK
 -> ARM_REACHING
 -> GRASPING
 -> LIFTING
 -> NAVIGATING_TO_PLACE
 -> PLACING
 -> DONE / ERROR
```

这样三个项目就不再是“两个独立子系统”，而是真正组成一台移动操作机器人。

---

## 12. 总结

最后总结成一句话：

- `XC-AGV-2.0` 负责底盘移动和导航；
- `openarmx_ros2-6.0_basic` 负责提供机械臂基础框架；
- `Xc-openarm-2.0` 负责提供更适合项目落地的机械臂增强能力。

三者串联后的完整链路就是：

```text
导航到底盘目标点
  -> 到位后触发机械臂
  -> MoveIt / Commander 执行动作
  -> 必要时结合视觉做坐标转换
  -> 再继续导航到下一个任务点
```

因此，对当前项目最合理的理解和落地方式是：

1. 用 `XC-AGV-2.0` 作为底盘运行栈；
2. 用 `Xc-openarm-2.0` 作为机械臂运行栈；
3. 用 `openarmx_ros2-6.0_basic` 作为基础参考版；
4. 在上层增加统一任务编排节点，把移动和抓取真正串起来。
