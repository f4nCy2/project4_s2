# 成员C 交付文档 —— 通信系统开发

> 本文档由成员A编写，定义成员C需要实现的模块、通信协议规范和集成方式。
> 请严格按照PRD需求和通信协议文档实现，与成员A、B协作联调。

---

## 1. 职责范围

根据PRD，成员C负责：

| 职责 | 说明 |
|------|------|
| 上层与机器人底层通信 | TCP/WebSocket连接管理 |
| 指令发送 | 将控制指令可靠发送至机器人 |
| 状态数据接收 | 接收机器人状态并分发给A、B模块 |
| 通信协议设计 | 消息帧格式、JSON协议封装 |
| 仿真与实物联调 | 支持仿真Mock服务器和实物机器人切换 |

---

## 2. 需要实现的模块

### 2.1 SocketClient（底层连接）
**文件位置：** `src/communication/socket_client.py`

核心职责：TCP Socket连接生命周期管理，提供 `send(data)` / `send_json(obj)` 发送原语，后台线程接收数据并回调。

```python
class SocketClient:
    def connect(self, host: str, port: int, timeout: float = 5.0) -> bool
    def disconnect(self) -> None
    def send(self, data: bytes) -> bool
    def send_json(self, obj: dict) -> bool
    def is_connected(self) -> bool

    # 回调注册
    def on_data(self, callback: Callable[[bytes], None])          # 收到数据
    def on_connected(self, callback: Callable[[], None])          # 连接建立
    def on_disconnected(self, callback: Callable[[str], None])    # 连接断开

    # 关键参数
    RECEIVE_BUFFER = 4096         # 接收缓冲区
    RECONNECT_BASE_DELAY = 1.0    # 重连初始延迟
    RECONNECT_MAX_DELAY = 30.0    # 重连最大延迟
```

**关键实现要点：**
- 消息帧格式：4字节大端长度前缀 + JSON Body
- 断线自动重连，指数退避（1s → 2s → 4s → ... → 30s）
- 接收循环在后台daemon线程中运行

### 2.2 CommandSender（指令发送器）
**文件位置：** `src/communication/command_sender.py`

核心职责：将Command对象序列化为JSON并通过SocketClient发送。支持发送队列、失败重试、速率限制。

```python
class CommandSender:
    def __init__(self, socket_client=None)
    def set_socket(self, socket_client)
    def start(self)                               # 启动后台发送线程
    def stop(self)
    def send_command(self, command: Command) -> bool    # 入队发送
    def send_immediate(self, command: Command) -> bool  # 立即发送（跳过队列）

    # 关键参数
    MAX_QUEUE_SIZE = 100     # 队列最大长度
    MAX_RETRIES = 3          # 最大重试次数
    SEND_INTERVAL = 0.05     # 最小发送间隔（秒）
```

### 2.3 HeartbeatManager（心跳管理器）
**文件位置：** `src/communication/heartbeat_manager.py`

核心职责：定期发送心跳检测连接健康状态，超时触发回调。

```python
class HeartbeatManager:
    def set_send_func(self, func: Callable[[dict], bool])   # 设置心跳发送函数
    def start(self, interval_s: float = 1.0)                # 启动心跳
    def stop(self)
    def on_pong(self, seq: int)                             # 收到心跳响应时调用
    def is_alive(self) -> bool
    def on_timeout(self, callback)                          # 超时回调（触发重连）
    def on_beat(self, callback: Callable[[float], None])    # 每次成功返回延迟

    # 关键参数
    DEFAULT_INTERVAL = 1.0    # 心跳间隔
    DEFAULT_TIMEOUT = 3.0     # 超时判定
    MAX_MISSED_BEATS = 3      # 连续丢包判定断连
```

### 2.4 APIService（统一通信API）
**文件位置：** `src/communication/api_service.py`
**接口：** `src/common/interfaces.py` → `ICommunication`

核心职责：整合SocketClient + CommandSender + HeartbeatManager，提供统一的高层API。这是成员A、B直接使用的通信入口。

```python
class APIService(ICommunication):
    # 连接
    def connect(self, host: str, port: int) -> bool
    def disconnect(self) -> None
    def is_connected(self) -> bool

    # 发送
    def send_command(self, command: Command) -> bool
    def send_command_immediate(self, command: Command) -> bool

    # 接收回调（解析JSON后分发）
    def on_status_received(self, callback)       # 收到status → 调用callback(RobotStatus)
    def on_sensor_data(self, callback)           # 收到sensor → 调用callback(SensorData)
    def on_action_complete(self, callback)       # 收到action_complete → 调用callback(action_id, success, error)

    # 心跳
    def start_heartbeat(self, interval_s: float = 1.0)
    def on_heartbeat_timeout(self, callback)
    def on_heartbeat_latency(self, callback)
    def is_heartbeat_alive(self) -> bool
```

**APIService内部消息分发逻辑：**
收到TCP数据 → JSON解析 → 根据 `type` 字段路由：
- `type: "status"` → 解析为 `RobotStatus` → 调用 `on_status_received` 回调
- `type: "sensor"` → 解析为 `SensorData` → 调用 `on_sensor_data` 回调
- `type: "heartbeat"` → 调用 `HeartbeatManager.on_pong(seq)`
- `type: "action_complete"` → 调用 `on_action_complete` 回调
- `type: "error"` → 构造 error RobotStatus → 调用 `on_status_received` 回调

### 2.5 VisionDataBridge（视觉数据桥接）
**文件位置：** `src/communication/` 下新建

核心职责：处理大量视觉数据（深度图、RGB帧）的传输。由于数据量大，需用独立数据通道或二进制帧协议，避免阻塞主通信通道。

---

## 3. 通信协议

详见 `docs/PROTOCOL.md`，核心要点如下：

### 3.1 帧格式
```
┌──────────────────┬────────────────────────────┐
│  Length (4 bytes)│  JSON Body (UTF-8)          │
│  Big-endian      │                            │
└──────────────────┴────────────────────────────┘
```

### 3.2 消息类型

| type | 方向 | 说明 |
|------|------|------|
| `command` | 上层→机器人 | 动作控制指令 |
| `status` | 机器人→上层 | 机器人状态数据 |
| `sensor` | 机器人→上层 | 传感器/视觉数据 |
| `heartbeat` | 双向 | 心跳检测 |
| `action_complete` | 机器人→上层 | 动作执行完成通知 |
| `error` | 机器人→上层 | 错误报告 |

### 3.3 关键协议格式

**Command（上层→机器人）：**
```json
{
  "type": "command",
  "command_id": "a1b2c3d4",
  "action_type": "walk_straight",
  "params": {"distance_m": 2.0, "speed": 0.5},
  "timestamp": 1700000000.123
}
```

**Status（机器人→上层）：**
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

**Heartbeat（双向）：**
```json
{"type": "heartbeat", "seq": 42, "timestamp": 1700000000.000}
```
机器人必须原样返回seq号，用于计算RTT延迟。

**ActionComplete（机器人→上层）：**
```json
{"type": "action_complete", "action_id": "a1b2c3d4", "success": true, "error": ""}
```

---

## 4. 与其他成员的接口

### 4.1 成员A → 成员C（指令发送）
```
ActionScheduler.schedule_action() → Command.build() → APIService.send_command()
```
成员A生成Action后，通过ActionScheduler将Command送入APIService的发送队列。

### 4.2 成员C → 成员A（动作完成通知）
```
APIService._handle_incoming_data() → on_action_complete → ActionScheduler.mark_action_complete()
```
机器人回报动作完成后，APIService解析后调用A的回调，更新动作状态。

### 4.3 成员C → 成员B（状态数据）
```
APIService._handle_incoming_data() → on_status_received → StatusManager.update_robot_status()
```
机器人状态数据到达后，APIService解析为RobotStatus对象，写入StatusManager，B的UI自动更新。

### 4.4 成员C → 成员A（传感器数据）
```
APIService._handle_incoming_data() → on_sensor_data → ObstacleDetector.detect()
```
传感器数据到达后，APIService解析为SensorData，传给A的避障模块处理。

---

## 5. 错误码定义

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

## 6. 联调测试

### 6.1 仿真测试
```bash
# 启动Mock服务器 + 系统客户端（headless模式）
python scripts/run_simulation.py

# 仅启动服务器（另一终端运行客户端）
python scripts/run_simulation.py --server-only
```

Mock服务器行为：
- 监听 TCP 9090 端口
- 自动回复心跳（seq原样返回）
- 收到command后延时0.3s返回 action_complete
- 每0.5s发送一次 status 数据
- 根据 walk_straight/distance_m 更新模拟位置

### 6.2 实物联调
将 `host` 和 `port` 参数改为实物机器人的IP和端口：
```python
system["comm"].connect("192.168.1.xxx", 9090)
```

---

## 7. 验收标准

- [ ] TCP连接稳定建立/断开，支持自动重连
- [ ] 指令发送成功率 >= 99%（正常网络环境）
- [ ] 状态数据实时接收，延迟 < 100ms
- [ ] 心跳检测正常：间隔1s，超时3s，连续3次丢失判定断连
- [ ] 通信协议消息帧格式正确（4字节大端长度 + JSON）
- [ ] 所有6种消息类型正确解析和路由
- [ ] 与成员A联调：动作指令发送→执行→完成回调 流程正常
- [ ] 与成员B联调：状态数据接收→StatusManager→UI显示 流程正常
- [ ] 仿真Mock服务器联调通过
- [ ] 实物机器人联调通过
