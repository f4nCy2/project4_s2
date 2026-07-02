# 成员B 交付文档 —— 状态管理、控制界面与视觉画面显示

> 本文档由成员A编写，定义成员B需要实现的模块、接口规范和集成方式。
> 请严格按照PRD需求实现，与成员A、C协作联调。

---

## 1. 职责范围

根据PRD，成员B负责：

| 职责 | 说明 |
|------|------|
| 机器人状态管理 | 接收并存储机器人实时状态数据 |
| 日志系统 | 系统级日志记录、存储、查询 |
| 控制界面 | 提供任务控制按钮交互 |
| 数据可视化 | 实时显示机器人状态、动作执行情况、系统日志 |
| 视觉画面显示 | 显示摄像头画面/避障检测画面 |

---

## 2. 需要实现的模块

### 2.1 StatusManager（状态管理器）
**文件位置：** `src/status_ui/status_manager.py`
**接口：** `src/common/interfaces.py` → `IStatusManager`

核心职责：作为系统的中心状态存储。成员C收到机器人数据后调用 `update_robot_status()` 写入，成员B的UI调用 `get_robot_status()` 读取显示。

```python
class StatusManager(IStatusManager):
    # 必须实现的方法：
    def update_robot_status(self, status: RobotStatus) -> None  # C写入
    def get_robot_status(self) -> RobotStatus                     # UI读取
    def add_log(self, level: str, source: str, message: str)     # 添加日志
    def get_logs(self, count: int = 100) -> list[dict]           # 获取日志
    def subscribe_status(self, callback) -> None                  # 订阅状态推送
```

**数据模型（来自 `src/common/models.py`）：**
- `RobotStatus`: state, battery, position(x,y,z), orientation(roll,pitch,yaw), velocity, error_code
- `state` 枚举: idle | moving | avoiding | stopped | error

### 2.2 LogSystem（日志系统）
**文件位置：** `src/status_ui/log_system.py`

核心职责：系统级日志记录，支持文件滚动存储、控制台输出、按级别过滤。

```python
class LogSystem:
    def __init__(self, log_dir: str = "./logs")
    def info(self, source: str, message: str)     # 信息来源 + 消息
    def warning(self, source: str, message: str)
    def error(self, source: str, message: str)
    def debug(self, source: str, message: str)
    def get_log_file_path(self) -> str
```

### 2.3 ControlPanel（控制面板）
**文件位置：** `src/status_ui/control_panel.py`

核心职责：提供任务控制的回调接口（启动/停止/暂停/继续/手动动作）。成员A的main.py将回调注册到ControlPanel，UI按钮触发时调用对应回调。

```python
class ControlPanel:
    # 回调属性（由main.py注册，UI按钮触发时调用）：
    on_start_task: callable    # lambda task_id: ...
    on_stop_task: callable
    on_pause_task: callable
    on_resume_task: callable
    on_send_action: callable   # lambda action_type, params: ...

    # 对UI暴露的方法：
    def start_task(self, task_id)
    def stop_task(self, task_id)
    def pause_task(self, task_id)
    def resume_task(self, task_id)
    def send_manual_action(self, action_type: ActionType, params: dict)
    def get_status_text(self) -> dict       # 返回UI可用的状态数据
    def get_logs_for_display(self, count)   # 返回日志列表
```

### 2.4 RobotDashboard（仪表盘窗口）
**文件位置：** `src/status_ui/dashboard.py`

核心职责：主UI窗口，将StatusManager的数据可视化展示。技术方案可选 PyQt6（桌面）或 Web（HTML/JS）。

```python
class RobotDashboard:
    def __init__(self, status_manager: IStatusManager)
    def register_control_panel(self, control_panel)   # 绑定控制面板
    def run()                                          # 启动UI（阻塞）
    def update_vision_frame(self, frame_data)          # 更新视觉画面
```

需要展示的内容：
- 机器人状态（state, battery, position, orientation, velocity）
- 动作执行进度条
- 任务队列列表
- 系统日志（支持按级别过滤：ERROR/WARNING/INFO/DEBUG）
- 控制按钮（启动/停止/暂停/继续/紧急停止）
- 手动动作发送

### 2.5 VisionDisplay（视觉画面显示）
**文件位置：** `src/status_ui/` 下新建或集成在dashboard中

核心职责：显示摄像头画面/深度图/避障检测框，从成员A的VisionDetector获取障碍物数据并叠加显示。

---

## 3. 与其他成员的接口

### 3.1 成员C → 成员B（数据流入）
```
APIService.on_status_received → StatusManager.update_robot_status()
APIService.on_sensor_data      → LogSystem.info()
```
成员C收到机器人数据后，调用StatusManager写入状态。B的UI通过 `get_robot_status()` 和 `subscribe_status()` 订阅更新。

### 3.2 成员A → 成员B（事件通知）
```
TaskManager.add_listener → StatusManager.add_log()  # 任务事件日志
ActionScheduler           → StatusManager.get_logs() # UI获取动作状态
```
成员A的任务规划模块产生事件（created/started/paused/resumed/stopped/completed），通过StatusManager记录日志。

### 3.3 成员B → 成员A（用户操作）
```
ControlPanel.on_start_task → TaskManager.start_task()
ControlPanel.on_stop_task  → TaskManager.stop_task()
ControlPanel.on_send_action → ActionScheduler.schedule_action()
```
UI按钮触发 → ControlPanel回调 → A的模块执行。这些回调在 `main.py:build_system()` 中注册。

---

## 4. 共享数据模型（来自 `src/common/models.py`）

```python
# RobotStatus — 机器人状态
@dataclass
class RobotStatus:
    state: RobotState           # idle/moving/avoiding/stopped/error
    battery: float              # 电量 0-100
    position: tuple[float,float,float]     # (x, y, z) 米
    orientation: tuple[float,float,float]  # (roll, pitch, yaw) 度
    velocity: float             # 当前速度 m/s
    error_code: int             # 错误码 (0=正常)

# Task — 任务
@dataclass
class Task:
    name: str
    actions: list[Action]
    task_id: str
    current_action_index: int
    is_running: bool

# Action — 动作
@dataclass
class Action:
    action_type: ActionType     # walk_straight/turn_in_place/turn_walk/stop/walk_backward/sidestep
    params: dict                # e.g. {"distance_m": 2.0, "speed": 0.5}
    status: ActionStatus        # pending/running/completed/failed/interrupted
    action_id: str
```

---

## 5. 验收标准

- [ ] StatusManager 稳定存储并推送机器人状态
- [ ] LogSystem 支持文件滚动存储（10MB/文件，保留5个备份）
- [ ] 控制界面可启动/停止/暂停/继续任务
- [ ] 控制界面实时显示状态（刷新率 >= 10Hz）
- [ ] 控制界面显示系统日志，支持按级别过滤
- [ ] 视觉画面区域可显示摄像头画面或障碍物检测标记
- [ ] 紧急停止按钮（ESC快捷键）立即生效
- [ ] 与成员A、C联调通过

---

## 6. 技术建议

- 状态更新使用订阅模式（observer pattern），避免轮询
- UI刷新建议用定时器（QTimer / setInterval），间隔100ms
- 日志列表建议限制显示最新N条（默认100条），避免DOM/Qt控件过多卡顿
- 视觉画面建议用独立线程接收帧数据，避免阻塞UI主线程
