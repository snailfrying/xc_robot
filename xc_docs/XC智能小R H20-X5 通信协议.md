# XC智能小R H20-X5 通信协议

| 项目 | 内容 |
| --- | --- |
| 文档编号 | XC-ROBOT-PROTO-001 |
| 版本 | V1.0 MVP |
| 编制日期 | 2026-04-15 |
| 状态 | 待评审 |

---

## 1. 概述

本协议定义 H20（感知脑）与 X5（运动脑）之间的通信接口。

**MVP 目标**：H20 下发点位指令，X5 导航到目标点，全程可停止、可查状态、可感知到达。

### 1.1 通信架构

```plaintext
  H20（上位机）                         X5（下位机）
 ┌────────────┐    HTTP REST        ┌────────────────┐
 │ REST Client├───────────────────► │ HTTP Server    │
 │            │◄───────────────────┤ :8080           │
 └────────────┘    JSON Response    └────────────────┘
 ┌────────────┐    WebSocket        ┌────────────────┐
 │ WS Client  │◄───────────────────┤ WS Server      │
 │            │    实时推送          │ :8081           │
 └────────────┘                     └────────────────┘

```

*   **REST**（H20→X5）：导航指令、停止、状态查询
    
*   **WebSocket**（X5→H20）：导航状态变更、异常告警
    
    ### 1.2 网络配置（示例）
    
    | 设备 | IP | 说明 |
    | --- | --- | --- |
    | H20 | `192.168.1.10` | 上位机，以太网直连 |
    | X5 | `192.168.1.20` | 下位机，运行 HTTP + WS 服务 |
    
    ---
    
    ## 2. REST API（H20 → X5）
    
    Base URL: `http://192.168.1.20:8080/api/v1\`
    
    所有请求和响应均为 `application/json`。
    
    ### 2.1 POST /navigate — 导航到目标点
    
    **请求**：
    
    ```json
    {
      "point_id": "QR-B02",         // 必填 - 目标点位 ID
      "nav_mode": "normal"          // 可选 - "normal"（默认） | "careful"（降速）
    }
    
    ```
    
    X5 根据 `point_id` 从本地点位表查询坐标，若点位不存在则返回错误。
    
    **成功** `200`：
    
    ```json
    {
      "code": 0,
      "msg": "accepted",
      "data": {
        "nav_id": "NAV-001",
        "point_id": "QR-B02",
        "point_name": "张三工位"
      }
    }
    
    ```
    
    **点位不存在** `400`：
    
    ```json
    {
      "code": 2004,
      "msg": "POINT_NOT_FOUND",
      "data": { "point_id": "QR-Z99" }
    }
    
    ```
    
    **已有导航任务进行中** `409`：
    
    ```json
    {
      "code": 2006,
      "msg": "NAV_IN_PROGRESS",
      "data": { "current_target": "QR-A01" }
    }
    
    ```
    
    ### 2.2 POST /stop — 立即停止
    
    **请求**：
    
    ```json
    {
      "reason": "user_command"       // 可选 - "user_command" | "emergency" | "task_cancel"
    }
    
    ```
    
    **响应** `200`：
    
    ```json
    {
      "code": 0,
      "msg": "stopped"
    }
    
    ```
    
    无论当前是否在导航，调用 stop 都返回 200（幂等）。
    
    ### 2.3 GET /status — 查询综合状态
    
    **响应** `200`：
    
    ```json
    {
      "code": 0,
      "msg": "ok",
      "data": {
        "pose": {
          "x": 10.2,
          "y": 5.8,
          "theta": 0.78
        },
        "nav": {
          "state": "navigating",       // "idle" | "navigating" | "arrived" | "failed"
          "target_point_id": "QR-B02",
          "progress": 0.65
        },
        "battery": {
          "level": 78,
          "is_charging": false
        },
    
        "errors": [ ]
    
      }
    }
    
    ```
    
    ### 2.4 API 汇总
    
    | 方法 | 路径 | 说明 |
    | --- | --- | --- |
    | `POST` | `/api/v1/navigate` | 导航到目标点 |
    | `POST` | `/api/v1/stop` | 立即停止 |
    | `GET` | `/api/v1/status` | 查询综合状态 |
    
    ### 2.5 通用错误响应
    
    ```json
    {
      "code": 1002,
      "msg": "PARSE_ERROR",
      "data": { "detail": "invalid JSON" }
    }
    
    ```
    
    | HTTP Status | 场景 |
    | --- | --- |
    | `200` | 成功 |
    | `400` | 参数错误 / 点位不存在 |
    | `409` | 状态冲突 |
    | `500` | X5 内部错误 |
    | `503` | X5 未就绪 |
    
    ---
    
    ## 3. WebSocket 推送（X5 → H20）
    
    连接地址：`ws://192.168.1.20:8081/ws`
    
    心跳：WebSocket 原生 ping/pong，间隔 10s。30s 未收到 pong 视为断连。
    
    ### 3.1 消息格式
    
    ```json
    {
      "type": "string",
      "timestamp": 0,
      "data": {}
    }
    
    ```
    
    ### 3.2 nav\_status — 导航状态变更
    
    X5 导航状态发生变化时推送。
    
    ```json
    {
      "type": "nav_status",
      "timestamp": 1713168002000,
      "data": {
        "nav_state": "arrived",
        "nav_id": "NAV-001",
        "target_point_id": "QR-B02"
      }
    }
    
    ```
    
    **nav\_state 取值**：
    
    | 值 | 含义 |
    | --- | --- |
    | `idle` | 空闲 |
    | `navigating` | 导航中 |
    | `arrived` | 已到达 |
    | `blocked` | 路径阻塞，等待/绕行中 |
    | `failed` | 导航失败（多次重试后仍无法到达） |
    
    **推送时机**：状态变更时立即推送。
    
    ### 3.3 error — 异常告警
    
    ```json
    {
      "type": "error",
      "timestamp": 1713168005000,
      "data": {
        "error_code": 3001,
        "error_level": "warning",    // "warning" | "error" | "fatal"
        "error_msg": "LiDAR data timeout",
        "action": "degraded"         // "degraded" | "stopped" | "retry"
      }
    }
    
    ```
    
    | error\_level | X5 行为 |
    | --- | --- |
    | `warning` | 降级运行 |
    | `error` | 已停车，等待 H20 指令 |
    | `fatal` | 严重故障，需人工介入 |
    
    ### 3.4 MVP 阶段 WebSocket 消息汇总
    
    | type | 说明 | 触发方式 |
    | --- | --- | --- |
    | `nav_status` | 导航状态变更 | 事件触发 |
    | `error` | 异常告警 | 事件触发 |
    
    ---
    
    ## 4. 断连处理
    
    | 场景 | X5 行为 | H20 行为 |
    | --- | --- | --- |
    | WebSocket 断连 | 原地停车 | 自动重连（1s/2s/4s 退避，最大 30s） |
    | HTTP 请求超时 | \- | 重试最多 3 次，全部失败标记 X5 离线 |
    | H20 重连成功 | 恢复 WS 推送 | `GET /status` 查询 X5 当前状态后决策 |
    
    ---
    
    ## 5. 点位配置表
    
    X5 本地维护，文件路径：`/opt/xc-robot/config/poi_map.json`
    
    H20 也持有一份副本，用于语音/大模型做名称匹配和反问补齐。
    
    ```json
    {
      "version": "1.0",
      "map_id": "floor7_v1",
      "points": [
        {
          "point_id": "QR-A01",
          "name": "快递站",
          "aliases": ["快递站", "取件处", "A区"],
          "area": "A",
          "x": 2.5,
          "y": 3.1,
          "theta": 0.0,
          "type": "pickup"
        },
        {
          "point_id": "QR-B02",
          "name": "张三工位",
          "aliases": ["张三", "B区工位2"],
          "area": "B",
          "x": 12.5,
          "y": 8.3,
          "theta": 1.57,
          "type": "workstation"
        },
        {
          "point_id": "QR-D01",
          "name": "会议室A",
          "aliases": ["大会议室"],
          "area": "D",
          "x": 5.0,
          "y": 15.2,
          "theta": 3.14,
          "type": "meeting_room"
        },
        {
          "point_id": "QR-F01",
          "name": "充电站",
          "aliases": ["充电", "回去充电"],
          "area": "F",
          "x": 30.0,
          "y": 2.0,
          "theta": 0.0,
          "type": "charger"
        }
      ]
    }
    
    ```
    
    | 字段 | 类型 | 必填 | 说明 |
    | --- | --- | --- | --- |
    | `point_id` | string | 是 | 唯一标识，navigate 接口使用此 ID |
    | `name` | string | 是 | 显示名称 |
    
    | `aliases`
    
*   [ ] | 否   | 别名，用于语音匹配                                    |
    

| `area`     | string   | 是   | 区域标识                                              | | `x`, `y`   | float    | 是   | 地图坐标（米）                                        | | `theta`    | float    | 否   | 到达朝向（弧度），默认 0                              | | `type`     | string   | 是   | `pickup` / `workstation` / `meeting_room` / `charger` |

---

## 6. 错误码

| 码 | 名称 | 说明 |
| --- | --- | --- |
| 0 | OK | 成功 |
| 1002 | PARSE\_ERROR | 请求解析失败 |
| 1003 | SERVICE\_UNAVAILABLE | X5 服务不可达 |
| 2001 | NAV\_UNREACHABLE | 目标点不可达 |
| 2004 | POINT\_NOT\_FOUND | 点位 ID 不存在 |
| 2005 | EMERGENCY\_STOP | 紧急停车 |
| 2006 | NAV\_IN\_PROGRESS | 已有导航进行中 |
| 3001 | SENSOR\_ERROR | 传感器异常 |

---

## 7. 联调方案

### 7.1 验证步骤

| 步骤 | 操作 | 通过标准 |
| --- | --- | --- |
| 1 | `ping 192.168.1.20` | 网络通 |
| 2 | Apifox `GET /status` | 返回 200 + 正确 JSON |
| 3 | Apifox 连接 `ws://...:8081/ws` | 收到消息 |
| 4 | Apifox `POST /navigate {"point_id":"QR-A01"}` | 返回 accepted |
| 5 | 观察 WS 推送 `nav_status: arrived` | X5 到达 A 点 |
| 6 | `POST /navigate {"point_id":"QR-B02"}` | A→B 跑通 |
| 7 | 导航中 `POST /stop` | X5 立即停车 |

### 7.2 Python 快速验证

```python
import requests, asyncio, aiohttp, json

X5 = "http://192.168.1.20:8080/api/v1"

# 查状态
print(requests.get(f"{X5}/status").json())

# 发导航
print(requests.post(f"{X5}/navigate", json={"point_id": "QR-B02"}).json())

# 监听 WS
async def listen():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("ws://192.168.1.20:8081/ws") as ws:
            async for msg in ws:
                print(json.loads(msg.data))

asyncio.run(listen())

```
---

## 8. 后续迭代预留

以下能力不在 MVP 范围，协议层预留但暂不实现：

| 能力 | 接口/消息 | 预计阶段 |
| --- | --- | --- |
| 暂停/恢复 | `POST /pause`, `POST /resume` | V1.1 |
| 位姿实时推送 | WS `pose`（5Hz） | V1.1 |
| 电池状态推送 | WS `battery` | V1.1 |
| 障碍物告警推送 | WS `obstacle` | V1.1 |
| 调整推送频率 | WS `configure` | V1.2 |
| 拍照留痕触发 | `POST /photo` | V1.2 |
| 钉钉通知 | H20 内部实现 | V1.2 |
| 地图点位查询 | `GET /points` | V1.2 |
| OpenAPI 规范输出 | \- | V2.0 |

---

## 附录：坐标系约定

| 参数 | 定义 |
| --- | --- |
| 原点 | SLAM 建图起始点 |
| X 轴 | 建图起始正前方 |
| Y 轴 | 建图起始左侧 |
| 单位 | 米（m） |
| 角度 | 弧度（rad），逆时针为正，\[-pi, pi\] |

---

_本文档为 MVP 初稿，待协议评审会讨论后定版。_