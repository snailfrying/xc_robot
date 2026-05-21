# 机器人消费级 API 说明（优化版）

> 适用场景：固定地图、点位导航、云端视频闭环微调、单帧图像抓拍。  
> 设计目标：去掉租约与复杂任务模型，保留最小可用 REST API；手动控制采用“前 / 后 / 左 / 右 / 停止”方式，导航参考《XC智能小R H20-X5 通信协议》统一为点位导航。  
> 建议版本：V1.1 MVP  
> Base URL：`http://<robot-ip>:8080/api/v1`

---

## 1. 设计原则

### 1.1 面向调用方的简化原则

1. **不使用租约（lease）**：调用方无需先申请占用权，接口直接可用。  
2. **不暴露建图流程**：当前默认机器人运行在固定地图模式，地图预先部署完成。  
3. **不提供 `goto_pose`**：当前导航只面向业务点位，不直接暴露地图坐标导航。  
4. **不提供异步任务轮询模型**：不使用 `task_id`；导航、停止、状态查询均走简单 API。  
5. **手动微调优先使用方向接口**：为大模型和业务侧降低调用复杂度，直接提供前 / 后 / 左 / 右接口。  
6. **统一停止语义**：只保留一个通用 `stop`，用于停止手动运动或取消当前导航。  
7. **图像采用“抓拍后按访问模式返回”**：跨服务器优先 URL 下载；同机且共享存储时可返回文件路径；Base64 仅用于调试或小图预览。  

### 1.2 运动学边界

本机为**差速底盘**，只支持：

- 前进
- 后退
- 原地左转
- 原地右转
- 停止

不支持：

- 横移 / 侧移
- `linear_y` 语义控制
- 斜向平移

因此消费级 API 不开放 `cmd_vel` 原始参数给上层业务方作为主接口，而是提供更稳定的方向型接口。

### 1.3 控制模式

机器人任一时刻只处于以下模式之一：

- `idle`：空闲
- `manual`：手动控制中（前后左右之一）
- `navigating`：导航中
- `failed`：异常或失败态

建议约束：

- 手动运动时可直接切换到另一种手动运动；新指令覆盖旧指令。  
- 导航中若收到手动运动请求，默认返回冲突错误，要求业务方先调用 `POST /stop`。  
- 手动运动中若收到导航请求，系统可先安全停车，再切换为导航模式。  
- 所有会导致底盘持续运动的接口，都应带服务端安全兜底超时机制。  

### 1.4 稳定性优先约束

为避免网络抖动、上层逻辑异常或接口阻塞导致机器人长时间失控，建议增加以下约束：

- **手动运动内置 watchdog**：即使调用方不传 `duration_ms`，服务端也应在超过默认超时时自动停车。建议默认 `1000 ~ 2000 ms`。  
- **导航与手动互斥**：避免同时进入两个控制源。  
- **图像文件自动清理**：抓拍文件不长期堆积，必须有保留时间或数量上限。  
- **接口幂等优先**：`stop`、删除图片等接口即使重复调用，也尽量返回稳定结果。  

---

## 2. API 总览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/move/forward` | 持续前进，直到收到新的运动指令、`stop` 或 watchdog 超时 |
| `POST` | `/move/backward` | 持续后退，直到收到新的运动指令、`stop` 或 watchdog 超时 |
| `POST` | `/move/left` | 持续原地左转，直到收到新的运动指令、`stop` 或 watchdog 超时 |
| `POST` | `/move/right` | 持续原地右转，直到收到新的运动指令、`stop` 或 watchdog 超时 |
| `POST` | `/stop` | 通用停止；停止手动控制或取消导航 |
| `POST` | `/navigate` | 按固定地图点位导航 |
| `GET` | `/status` | 查询综合状态 |
| `GET` | `/points` | 查询可导航点位列表 |
| `POST` | `/camera/capture` | 抓拍并保存一张图片，可选附带深度图 |
| `GET` | `/camera/images/{image_id}` | 查询抓拍图片元数据 |
| `GET` | `/camera/images/{image_id}/rgb` | 下载 RGB 图片 |
| `GET` | `/camera/images/{image_id}/depth` | 下载深度图（如果抓拍时启用） |
| `DELETE` | `/camera/images/{image_id}` | 删除指定抓拍图片 |

所有请求 / 响应默认使用 `application/json`，图片下载接口返回二进制文件流。

统一响应格式：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {}
}
```

---

## 3. 手动控制 API

### 3.1 设计说明

手动控制不再要求上层传 `duration_ms`。  
调用方在外部做闭环判断：

1. 根据视频或传感器判断偏差；
2. 下发某个方向运动命令；
3. 持续观察；
4. 在合适时机调用 `POST /stop`；
5. 再继续下一轮判断与控制。

这比上层传“持续多少毫秒”更简单，也更贴近真实业务控制方式。

同时为了安全，建议服务端内置 watchdog：

- 上层可以按 `200 ~ 500 ms` 周期重复发送同一方向控制；  
- 若超出 watchdog 时间未收到新的运动请求或 `stop`，机器人自动停车；  
- 这样既保留了“方向接口易调用”的优点，也避免网络断开后机器人持续空跑。  

### 3.2 POST /move/forward

机器人持续前进。

**请求示例**：

```json
{
  "speed_level": "normal"
}
```

**字段说明**：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `speed_level` | string | 否 | `slow` / `normal` / `fast`，默认 `normal` |
| `speed_mps` | number | 否 | 可选高级参数；显式指定前进速度，单位 m/s |

**成功响应**：

```json
{
  "code": 0,
  "msg": "moving_forward",
  "data": {
    "robot_state": "manual",
    "direction": "forward"
  }
}
```

### 3.3 POST /move/backward

机器人持续后退。

```json
{
  "speed_level": "normal"
}
```

**成功响应**：

```json
{
  "code": 0,
  "msg": "moving_backward",
  "data": {
    "robot_state": "manual",
    "direction": "backward"
  }
}
```

### 3.4 POST /move/left

机器人持续原地左转。

```json
{
  "speed_level": "normal"
}
```

**字段说明**：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `speed_level` | string | 否 | `slow` / `normal` / `fast`，默认 `normal` |
| `angular_radps` | number | 否 | 可选高级参数；显式指定角速度，单位 rad/s |

**成功响应**：

```json
{
  "code": 0,
  "msg": "turning_left",
  "data": {
    "robot_state": "manual",
    "direction": "left"
  }
}
```

### 3.5 POST /move/right

机器人持续原地右转。

```json
{
  "speed_level": "normal"
}
```

**成功响应**：

```json
{
  "code": 0,
  "msg": "turning_right",
  "data": {
    "robot_state": "manual",
    "direction": "right"
  }
}
```

### 3.6 手动控制行为约束

- 不带任何参数也应可调用，服务端使用默认速度档位。  
- 同类运动连续下发时，后一次请求覆盖前一次请求。  
- `forward` 与 `backward` 为线速度控制；`left` 与 `right` 为原地转向控制。  
- 运动持续生效，直到：
  - 收到新的手动控制指令；
  - 收到 `POST /stop`；
  - watchdog 超时；
  - 进入保护停车或故障态。  

---

## 4. 停止 API

### 4.1 POST /stop

统一停止接口，用于：

- 停止前进 / 后退 / 左转 / 右转
- 取消当前导航
- 作为业务侧的统一“刹车”接口

**请求示例**：

```json
{
  "reason": "user_command"
}
```

**字段说明**：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `reason` | string | 否 | `user_command` / `task_cancel` / `safety_stop` |

**成功响应**：

```json
{
  "code": 0,
  "msg": "stopped",
  "data": {
    "robot_state": "idle"
  }
}
```

约定：

- `stop` 为**幂等接口**。即使机器人当前已经停止，也返回成功。  
- 若当前在导航中，`stop` 表示取消导航并停车。  
- 当前版本不区分 `stop` 与 `estop` 两套对外接口；如后续需要硬件急停，可在驱动层扩展，不影响该消费 API。  

---

## 5. 导航 API（固定地图 / 点位导航）

### 5.1 设计说明

导航能力参考《XC智能小R H20-X5 通信协议》，采用最小可用模式：

- 只提供 `POST /navigate`  
- 目标使用 `point_id`  
- 不提供 `goto_pose`  
- 不提供 `pause` / `resume`  
- 不提供建图类接口  
- 导航状态统一通过 `GET /status` 查询  
- 导航主状态尽量简化，避免前后端语义不一致  

### 5.2 POST /navigate

根据目标点位 ID 发起导航。

**请求示例**：

```json
{
  "point_id": "QR-B02",
  "nav_mode": "normal"
}
```

**字段说明**：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `point_id` | string | 是 | 目标点位 ID，由 `/points` 返回 |
| `nav_mode` | string | 否 | `normal` / `careful`，默认 `normal` |

**成功响应**：

```json
{
  "code": 0,
  "msg": "accepted",
  "data": {
    "point_id": "QR-B02",
    "point_name": "张三工位",
    "nav_state": "navigating"
  }
}
```

**点位不存在**：

```json
{
  "code": 2004,
  "msg": "POINT_NOT_FOUND",
  "data": {
    "point_id": "QR-Z99"
  }
}
```

**已有导航进行中**：

```json
{
  "code": 2006,
  "msg": "NAV_IN_PROGRESS",
  "data": {
    "current_target": "QR-A01"
  }
}
```

### 5.3 导航状态约定

考虑到上层确实需要明确判断“导航成功 / 导航失败”，`/status` 中的 `nav.state` 建议保留导航终态：

| 状态 | 含义 |
| --- | --- |
| `idle` | 当前无导航任务 |
| `navigating` | 导航中 |
| `succeeded` | 最近一次导航成功到达 |
| `failed` | 最近一次导航失败 |
| `stopped` | 最近一次导航被停止 |

推荐语义如下：

- `navigating` 表示机器人当前正在执行导航；  
- `succeeded` / `failed` / `stopped` 表示**最近一次导航的终态结果**；  
- 终态建议保留一小段时间，或一直保留到下一次 `POST /navigate` 覆盖，避免上层轮询时错过结果。  

也就是说，推荐不要在导航一结束就立刻把 `nav.state` 切回 `idle`，否则上层很容易轮询不到结果。

更稳妥的做法是：

1. 导航过程中：`nav.state = navigating`；  
2. 导航结束后：切到 `succeeded` / `failed` / `stopped`；  
3. 该终态保留到下一次导航开始，或由服务端按固定窗口自动回收为 `idle`。  

这样做的优点是：

- 上层轮询 `GET /status` 就能直接拿到成功或失败结果；  
- 不需要额外引入异步任务系统；  
- 比“结束瞬间直接回 `idle`”更适合实际联调。  

推荐保留策略二选一：

- **方案 A：保留到下一次导航开始**，实现最简单，最适合 MVP；  
- **方案 B：终态保留 `5 ~ 30` 秒后自动回到 `idle`**，更偏“当前状态”语义。  

就你现在的场景，我更推荐**方案 A**。

### 5.4 推荐调用方式

典型调用流程：

1. `GET /points` 获取或缓存点位表；  
2. `POST /navigate` 发起导航；  
3. 轮询 `GET /status`，直到 `nav.state` 从 `navigating` 变为 `succeeded` / `failed` / `stopped`；  
4. 如需取消，调用 `POST /stop`。  

---

## 6. 状态查询 API

### 6.1 GET /status

提供统一综合状态，替代异步任务查询。

**成功响应示例**：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "robot_state": "navigating",
    "pose": {
      "x": 10.2,
      "y": 5.8,
      "theta": 0.78
    },
    "nav": {
      "state": "navigating",
      "target_point_id": "QR-B02",
      "target_point_name": "张三工位"
    },
    "battery": {
      "level": 78,
      "is_charging": false
    },
    "localization": {
      "valid": true,
      "map_id": "floor7_v1"
    },
    "errors": []
  }
}
```

### 6.2 字段建议

| 字段 | 说明 |
| --- | --- |
| `robot_state` | `idle` / `manual` / `navigating` / `failed` |
| `pose` | 当前位姿，固定地图坐标系 |
| `nav` | 当前导航状态 |
| `battery` | 电量与充电状态 |
| `localization.valid` | 当前定位是否可用 |
| `errors` | 当前故障列表 |

说明：

- `nav.state` 建议取值为 `idle` / `navigating` / `succeeded` / `failed` / `stopped`。  
- 当 `nav.state=navigating` 时，`robot_state` 一般也为 `navigating`。  
- 当 `nav.state=succeeded` / `failed` / `stopped` 时，`robot_state` 可以回到 `idle`，因为此时机器人已结束导航动作。  
- `progress` 删除，不建议对外提供，因为实际很难稳定估计，容易误导上层逻辑。  
- `robot_state=manual` 即表示当前正在前后左右手动控制，它与 `idle`、`navigating` 是同级状态。  
- 不再单独提供 `motion` 对象，避免状态查询接口过于繁琐。  
- 若业务侧需要知道最近一次手动控制方向，建议由调用方根据自己已发送的控制命令在本地维护，而不是反复查询机器人当前“手动方向”。  

---

## 7. 点位查询 API

### 7.1 GET /points

获取机器人当前可用的导航点位列表。

**成功响应示例**：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "map_id": "floor7_v1",
    "points": [
      {
        "point_id": "QR-A01",
        "name": "快递站",
        "aliases": ["快递站", "取件处", "A区"],
        "area": "A",
        "type": "pickup"
      },
      {
        "point_id": "QR-B02",
        "name": "张三工位",
        "aliases": ["张三", "B区工位2"],
        "area": "B",
        "type": "workstation"
      }
    ]
  }
}
```

### 7.2 说明

- 该接口面向业务侧与大模型，用于名称匹配、候选反问、目标确认。  
- 消费侧默认只需要点位元信息，不强制对外暴露 `x / y / theta`。  
- 若后续需要调试版接口，可额外提供内部 `GET /points/debug`。  

---

## 8. 图像抓拍与访问 API

### 8.1 设计说明

图片能力需要兼容两种部署场景：

1. **异机部署**：你的业务服务和机器人服务不在同一台机器上；  
2. **同机部署**：你的业务服务和机器人服务部署在同一台机器上，甚至共享同一块磁盘目录。  

因此，图像接口建议不要只固定成一种方式，而是统一成“**一次抓拍，多种访问模式**”：

- **`url` 模式**：适合异机部署，服务端保存文件并返回下载 URL；  
- **`path` 模式**：适合同机且共享存储的部署，直接返回文件路径；  
- **`inline` 模式**：返回 Base64 或内联编码，只建议用于调试、小图预览或极低频场景；  
- **`auto` 模式**：由服务端根据配置自动选择，推荐作为默认值。  

推荐原则：

- **跨服务器默认用 `url`**：最稳定，失败可单独重试；  
- **同服务器优先用 `path`**：开销最低，不重复走 HTTP 文件下载；  
- **不要默认返回完整 Base64 大图**：编码膨胀明显，通常会增加约 33% 体积，也会增加 CPU、内存和 JSON 解析压力。  

综合来看，更好的方案是：

1. 调用方发起抓拍；  
2. 服务端统一落盘保存；  
3. 返回 `image_id` 和访问描述；  
4. 调用方根据部署场景使用 `url`、`path` 或 `inline`；  
5. 调用方可主动删除，服务端也应自动清理历史图片。  

相比“固定只返回 Base64”或“固定只返回 URL”，这个方案更稳定，优点是：

- 同时兼容异机和同机两类部署；  
- 避免超大 JSON；  
- 文件下载失败时可单独重试；  
- 更适合 FastAPI 用 `FileResponse` 提供文件；  
- 可支持删除和自动过期回收。  

### 8.2 POST /camera/capture

抓拍并把图片保存到服务端。

**请求示例**：

```json
{
  "include_depth": true,
  "return_mode": "auto"
}
```

**字段说明**：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `include_depth` | boolean | 否 | 是否同时保存深度图，默认 `false` |
| `return_mode` | string | 否 | `auto` / `url` / `path` / `inline`，默认 `auto` |

`return_mode` 建议语义：

- `auto`：服务端按部署配置自动决定返回方式；  
- `url`：返回可下载 URL；  
- `path`：返回本地文件路径，仅适用于共享存储的可信环境；  
- `inline`：返回图片编码，仅建议调试或预览。  

**推荐成功响应示例**：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "image_id": "img_20260512_155800_001",
    "created_at": "2026-05-12T15:58:00+08:00",
    "expires_at": "2026-05-12T16:08:00+08:00",
    "return_mode": "url",
    "has_depth": true,
    "rgb": {
      "content_type": "image/jpeg",
      "download_url": "/api/v1/camera/images/img_20260512_155800_001/rgb",
      "file_path": null,
      "inline_data": null
    },
    "depth": {
      "content_type": "image/png",
      "download_url": "/api/v1/camera/images/img_20260512_155800_001/depth",
      "file_path": null,
      "inline_data": null
    },
    "delete_url": "/api/v1/camera/images/img_20260512_155800_001"
  }
}
```

说明：

- `image_id` 为本次抓拍资源唯一标识。  
- `expires_at` 由服务端给出，表示自动清理时间。  
- `return_mode` 表示本次实际返回方式。  
- 若未启用深度图，则 `has_depth=false`，`depth=null` 或 `download_url/file_path/inline_data` 为空。  
- 推荐统一使用 `rgb` / `depth` 对象，便于后续扩展大小、格式、哈希值等元信息。  

### 8.3 不同模式的建议响应

#### 8.3.1 `url` 模式

适用于你的服务器和机器人服务不在同一台机器上。

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "image_id": "img_20260512_155800_001",
    "return_mode": "url",
    "rgb": {
      "content_type": "image/jpeg",
      "download_url": "/api/v1/camera/images/img_20260512_155800_001/rgb"
    }
  }
}
```

优点：

- 适合跨机访问；  
- 下载失败可单独重试；  
- 接口职责清晰。  

#### 8.3.2 `path` 模式

适用于你的服务和机器人服务在同机，且能访问同一存储目录。

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "image_id": "img_20260512_155800_001",
    "return_mode": "path",
    "rgb": {
      "content_type": "image/jpeg",
      "file_path": "/data/captures/2026-05-12/img_20260512_155800_001_rgb.jpg"
    }
  }
}
```

优点：

- 无额外 HTTP 文件传输开销；  
- 处理链最短；  
- 更适合同机算法直接读取。  

约束：

- 只有在调用方和服务端**共享同一文件系统或挂载卷**时才真正可用；  
- 如果是同机不同容器但没有共享卷，`path` 依然不可用，此时应退回 `url`。  

#### 8.3.3 `inline` 模式

仅建议用于调试、小图、缩略图或极低频单帧场景。

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "image_id": "img_20260512_155800_001",
    "return_mode": "inline",
    "rgb": {
      "content_type": "image/jpeg",
      "encoding": "base64",
      "inline_data": "<base64...>"
    }
  }
}
```

不建议作为默认方案，原因是：

- 体积更大；  
- 编码和解码开销更高；  
- JSON 响应会变重；  
- 深度图和高分辨率图片时问题更明显。  

### 8.4 GET /camera/images/{image_id}

查询某张抓拍图的元数据。

**成功响应示例**：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "image_id": "img_20260512_155800_001",
    "created_at": "2026-05-12T15:58:00+08:00",
    "expires_at": "2026-05-12T16:08:00+08:00",
    "has_depth": true,
    "rgb": {
      "download_url": "/api/v1/camera/images/img_20260512_155800_001/rgb",
      "file_path": null
    },
    "depth": {
      "download_url": "/api/v1/camera/images/img_20260512_155800_001/depth",
      "file_path": null
    }
  }
}
```

适用场景：

- 上层系统只缓存了 `image_id`，稍后再查下载地址；  
- 判断图片是否已过期或是否包含深度图；  
- 同机部署时，也可以通过该接口反查本地文件路径。  

### 8.5 GET /camera/images/{image_id}/rgb

下载 RGB 图片文件。

返回：

- HTTP `200`
- `Content-Type: image/jpeg` 或 `image/png`
- Body 为图片二进制内容

### 8.6 GET /camera/images/{image_id}/depth

下载深度图文件。

约定：

- 若该图片未保存深度图，返回 `404` 或 `400`。  
- 深度图格式建议固定为 `png` 或 `npy`，避免前后端处理不一致。  
- 若后续主要给算法服务使用，优先推荐 `png` 或无损格式。  

### 8.7 DELETE /camera/images/{image_id}

主动删除指定抓拍图片。

**成功响应示例**：

```json
{
  "code": 0,
  "msg": "deleted",
  "data": {
    "image_id": "img_20260512_155800_001"
  }
}
```

约定：

- 删除接口应尽量幂等。  
- 若图片已被自动清理，再次删除可直接返回成功，或返回一个明确的 `IMAGE_NOT_FOUND`。  

### 8.8 最佳实践建议

综合你的两种场景，我建议采用下面这套默认策略：

- `POST /camera/capture` 默认 `return_mode=auto`；  
- 服务端配置一个部署级开关，例如：  
  - `capture_access_mode = url`：默认异机场景；  
  - `capture_access_mode = path`：默认同机共享存储场景；  
- 即使走 `path` 模式，也建议保留 `image_id` 和下载接口，作为兜底方案；  
- `inline` 不作为正式业务默认模式，只保留给调试或低频预览。  

也就是说：

- **你部署在不同服务器**：优先 `url`；  
- **你部署在同一台服务器且共享存储**：优先 `path`；  
- **你只是想临时快速验证**：可用 `inline`，但不要作为长期方案。  

如果只能在“图片编码”与“图片位置”二选一，我的建议是：

- **同机共享存储时选图片位置（`file_path`）**；  
- **异机时选图片下载 URL**；  
- **不推荐把完整图片编码作为主方案**。  

### 8.9 自动清理策略建议

为了避免历史图片堆积，建议服务端至少实现一种自动清理策略，最好两种同时启用：

- **按时间清理**：例如默认保留 `10 ~ 30 分钟`；  
- **按数量清理**：例如最多保留最近 `200 ~ 1000` 组抓拍；  
- **双阈值策略**：谁先触发就先清理。  

推荐默认值：

- `capture_ttl_sec = 600`  
- `max_capture_count = 500`  

### 8.10 FastAPI 落地建议

如果服务基于 FastAPI，推荐实现方式如下：

- 抓拍文件存储在本地目录，例如 `data/captures/<date>/`；  
- 图片元数据记录在内存索引或轻量数据库中，如 SQLite；  
- `POST /camera/capture` 负责抓拍、落盘、生成 `image_id`；  
- 当 `return_mode=path` 时，只在 `enable_local_file_access=true` 且确认共享存储可访问时返回 `file_path`；  
- `GET /camera/images/{image_id}/rgb` 使用 `FileResponse` 返回文件；  
- `DELETE /camera/images/{image_id}` 删除文件并移除索引；  
- 用后台定时任务清理过期图片；FastAPI 可结合 APScheduler、独立线程或启动时后台任务实现。  

这个方案对前后端都比较友好，出了问题也容易排查：

- 抓拍失败是采集问题；  
- 下载失败是文件或网络问题；  
- `path` 不可用通常是共享卷或部署方式问题；  
- 删除失败是存储问题；  
- 不会把所有问题混在一次大响应里。  

---

## 9. 通用错误码

### 9.1 响应格式

```json
{
  "code": 1002,
  "msg": "PARSE_ERROR",
  "data": {
    "detail": "invalid JSON"
  }
}
```

### 9.2 MVP 阶段建议保留的核心错误码

按同事备注，错误码现阶段不宜设计过多。MVP 只保留最常用、最稳定的一组即可：

| code | msg | HTTP | 说明 |
| --- | --- | --- | --- |
| `0` | `OK` | `200` | 成功 |
| `1001` | `INVALID_PARAM` | `400` | 参数缺失或格式错误 |
| `1002` | `PARSE_ERROR` | `400` | JSON 解析失败 |
| `1003` | `SERVICE_UNAVAILABLE` | `503` | 服务未就绪 |
| `2004` | `POINT_NOT_FOUND` | `400` | 点位 ID 不存在 |
| `2006` | `NAV_IN_PROGRESS` | `409` | 已有导航进行中 |
| `2007` | `LOCALIZATION_INVALID` | `422` | 当前定位不可用 |
| `3001` | `INTERNAL_FAILED` | `500` | 底层模块或服务内部异常 |
| `4004` | `IMAGE_NOT_FOUND` | `404` | 图片不存在或已过期删除 |

补充说明：

- 非关键细分错误，优先通过错误响应或 `errors` 字段表达。  
- 错误码宁可少而稳，不要一开始设计过细但后期难维护。  

---

## 10. 推荐业务调用模式

### 10.1 云端视频闭环微调

适用于大模型 / 视觉模型微调机器人姿态：

1. `POST /camera/capture` 或读取已有图像；  
2. 下载图片并判断应前进、后退、左转或右转；  
3. 调用对应 `/move/*`；  
4. 按固定频率继续发控制或调用 `POST /stop`；  
5. 重复上述过程直到姿态满足要求。  

### 10.2 固定点位导航

适用于业务点到点配送 / 到工位 / 到会议室：

1. `GET /points` 获取可用点位；  
2. 业务侧完成目标理解与点位匹配；  
3. `POST /navigate` 发起导航；  
4. `GET /status` 查询 `robot_state` 与 `nav.state`；  
5. 必要时 `POST /stop` 取消任务。  

### 10.3 单帧图像抓拍与下载

适用于拍照留痕、视觉识别、故障取证：

1. `POST /camera/capture` 创建抓拍；  
2. 使用返回的 `rgb_url` / `depth_url` 下载文件；  
3. 处理完成后调用 `DELETE /camera/images/{image_id}` 提前清理；  
4. 若未主动删除，由服务端按过期策略自动回收。  

---

## 11. 本版本明确不包含的能力

以下能力当前不放入消费级 MVP：

- 租约 / 占用控制  
- 建图启动 / 停止 / 保存  
- `goto_pose` 坐标导航  
- `pause` / `resume`  
- 异步 `task_id` 任务模型  
- 对外暴露原始 `cmd_vel` 主接口  
- 长连接视频流协议标准化  
- 多机器人调度相关接口  

---

## 12. 结论

这一版接口的核心是：

- **手动控制足够简单**：前 / 后 / 左 / 右 / 停即可直接驱动。  
- **导航状态更收敛**：`GET /status` 只关心当前是否在导航，任务成败在导航结束时单独返回。  
- **状态足够统一**：全部通过 `GET /status` 获取，而不是引入复杂异步任务体系。  
- **手动状态更简洁**：直接用 `robot_state=manual` 表示当前处于前后左右手动控制，不再额外维护 `motion` 查询结构。  
- **图像方案更适合混合部署**：统一抓拍落盘，再按 `url / path / inline` 返回，支持删除和自动过期。  
- **和小R协议思路一致**：保留 `navigate / stop / status / points` 的最小闭环。  

如果后续要继续扩展，建议优先增加：

1. `GET /health` 健康检查；  
2. 内部调试版 `cmd_vel`；  
3. 可选 WebSocket 状态推送；  
4. 调试版坐标点查询接口；  
5. 图片批量清理或分页查询接口。  

---

**版本**：V1.1 优化版（面向消费侧 / 固定地图 / 点位导航 / 手动微调 / 单帧图像抓拍）
