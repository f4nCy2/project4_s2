# 通信协议文档 / Communication Protocol

> 本文档定义上层任务规划系统与机器人底层控制器之间的通信协议。
> 成员C负责实现通信层，成员A和B通过APIService接口与通信层交互。

---

## 1. 概述

| 项目 | 说明 |
|------|------|
| 传输层 | TCP Socket |
| 消息格式 | JSON，长度前缀帧 (4字节大端 + JSON Body) |
| 默认端口 | 9090 |
| 编码 | UTF-8 |
| 心跳间隔 | 1.0 s |
| 心跳超时 | 3.0 s |

---

## 2. 消息帧格式

```
┌──────────────────┬────────────────────────────┐
│  Length (4 bytes)│  JSON Body (UTF-8)          │
│  Big-endian      │                            │
└──────────────────┴────────────────────────────┘
```

所有消息均使用此帧格式。Length = JSON Body 的字节数。

---

## 3. 消息类型总览

| type | 方向 | 说明 |
|------|------|------|
| `command` | 上层 → 机器人 | 动作控制指令 |
| `status` | 机器人 → 上层 | 机器人状态数据 |
| `sensor` | 机器人 → 上层 | 传感器/视觉数据 |
| `heartbeat` | 双向 | 心跳检测 |
| `action_complete` | 机器人 → 上层 | 动作执行完成通知 |
| `error` | 机器人 → 上层 | 错误报告 |

---

## 4. 消息详细定义

### 4.1 Command（上层 → 机器人）

```json
{
  "type": "command",
  "command_id": "a1b2c3d4",
  "action_type": "walk_straight",
  "params": {
    "distance_m": 2.0,
    "speed": 0.5
  },
  "timestamp": 1700000000.123
}
```

**action_type 取值：**

| 值 | 参数 | 说明 |
|----|------|------|
| `walk_straight` | `distance_m`, `speed` | 直线行走 |
| `walk_backward` | `distance_m`, `speed` | 后退行走 |
| `turn_in_place` | `angle_deg`, `angular_speed` | 原地掉头(正=顺时针) |
| `turn_walk` | `distance_m`, `angle_deg`, `speed` | 转弯行走 |
| `stop` | `emergency` (bool) | 停止 |
| `sidestep` | `distance_m`, `speed` | 侧向移动(正=右) |
| `avoid_obstacle` | `waypoints` | 避障绕行 |

### 4.2 Status（机器人 → 上层）

```json
{
  "type": "status",
  "data": {
    "state": "moving",
    "battery": 85.5,
    "position": [1.23, 2.45, 0.0],
    "orientation": [0.0, 0.0, 45.0],
    "velocity": 0.5,
    "current_action_id": "a1b2c3d4",
    "error_code": 0,
    "timestamp": 1700000000.456
  }
}
```

**state 取值：** `idle` | `moving` | `avoiding` | `stopped` | `error`

### 4.3 Sensor Data（机器人 → 上层）

```json
{
  "type": "sensor",
  "data": {
    "lidar_points": [[0.1, 0.5, 0.0], [0.2, 0.6, 0.0]],
    "imu": {
      "accel_x": 0.01, "accel_y": 0.0, "accel_z": 9.8,
      "gyro_x": 0.0, "gyro_y": 0.0, "gyro_z": 0.0
    },
    "timestamp": 1700000000.789
  }
}
```

> 注: depth_map 和 rgb_frame 数据量较大，通过独立数据通道传输。

### 4.4 Heartbeat

```json
// 请求 (上层 → 机器人)
{
  "type": "heartbeat",
  "seq": 42,
  "timestamp": 1700000000.000
}

// 响应 (机器人 → 上层)
{
  "type": "heartbeat",
  "seq": 42,
  "timestamp": 1700000000.050
}
```

- `seq` 必须原样返回，用于延迟计算
- 连续3次未收到响应 → 判定连接断开

### 4.5 Action Complete（机器人 → 上层）

```json
{
  "type": "action_complete",
  "action_id": "a1b2c3d4",
  "success": true,
  "error": ""
}
```

### 4.6 Error（机器人 → 上层）

```json
{
  "type": "error",
  "code": 101,
  "message": "Motor overheat"
}
```

**错误码定义：**

| code | 说明 |
|------|------|
| 0 | 无错误 |
| 101 | 电机过热 |
| 102 | 电机堵转 |
| 201 | 通信超时 |
| 301 | 电池电量过低 |
| 401 | 碰撞检测触发 |
| 501 | 避障失败 |
| 999 | 连接断开 |

---

## 5. 通信流程

```
上层系统                                    机器人控制器
   │                                             │
   │──── TCP Connect ───────────────────────────▶│
   │                                             │
   │◀─── status (initial) ──────────────────────│
   │                                             │
   │──── heartbeat (seq=1) ────────────────────▶│
   │◀─── heartbeat (seq=1) ────────────────────│
   │                                             │
   │──── command (walk_straight) ──────────────▶│
   │◀─── status (moving) ──────────────────────│
   │◀─── status (moving) ──────────────────────│
   │◀─── action_complete ──────────────────────│
   │◀─── status (idle) ────────────────────────│
   │                                             │
   │──── heartbeat (seq=2) ────────────────────▶│
   │◀─── heartbeat (seq=2) ────────────────────│
   │                                             │
```

---

## 6. 仿真模式

仿真时可使用 `scripts/run_simulation.py` 启动 Mock Robot Server，它:

- 监听 TCP 9090 端口
- 自动回复心跳
- 模拟动作执行(延时0.3s后返回 action_complete)
- 定时发送 status 数据
- 根据指令更新模拟位置

```bash
# 启动仿真服务器 + 客户端
python scripts/run_simulation.py

# 仅启动服务器
python scripts/run_simulation.py --server-only

# 启动带UI的客户端
python scripts/run_simulation.py --ui
```
