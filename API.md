# API 接口规范 / API Specification

> 本文档定义各模块之间的编程接口，供三位成员协作参考。
> 所有接口定义见 `src/common/interfaces.py`，数据模型见 `src/common/models.py`。

---

## 1. 模块依赖关系

```
┌──────────────────────────────────────────────┐
│               ControlPanel (B)               │
│  start/stop/pause/resume + manual actions    │
└──────────────┬───────────────────────────────┘
               │ 调用
┌──────────────▼───────────────────────────────┐
│            TaskManager (A)                   │
│  create_task / start_task / advance_action   │
└──────┬───────────────────┬───────────────────┘
       │ 生成Action         │ 状态通知
┌──────▼──────────┐  ┌─────▼───────────────────┐
│ ActionScheduler │  │   StatusManager (B)     │
│   (A)           │  │   update / get / logs   │
└──────┬──────────┘  └─────▲───────────────────┘
       │ dispatch           │ 写入
┌──────▼─────────────────────┴──────────────────┐
│           APIService (C)                      │
│  send_command / on_status_received            │
└──────┬────────────────────────────────────────┘
       │ TCP
┌──────▼──────────┐
│  Robot Controller│
└─────────────────┘
```

**核心规则：**
- A 不直接调用 C 的底层 socket，通过 `ICommunication` 接口
- B 不直接访问 C 的接收数据，通过 `IStatusManager` 接口
- 所有模块间的数据传递使用 `src/common/models.py` 中的 dataclass

---

## 2. 成员A 接口（Task Planner）

### ITaskManager

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `create_task` | name, actions, repeat=1 | Task | 创建新任务 |
| `start_task` | task_id | bool | 启动任务 |
| `stop_task` | task_id | bool | 停止任务 |
| `pause_task` | task_id | bool | 暂停任务 |
| `resume_task` | task_id | bool | 继续任务 |
| `get_task` | task_id | Task\|None | 查询任务 |
| `get_all_tasks` | - | list[Task] | 所有任务 |
| `get_current_task` | - | Task\|None | 当前执行任务 |

### IActionScheduler

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `schedule_action` | action | str (action_id) | 调度执行动作 |
| `interrupt_current_action` | - | bool | 中断当前动作 |
| `get_current_action` | - | Action\|None | 当前动作 |
| `get_action_status` | action_id | Action\|None | 动作状态 |
| `on_action_complete` | callback | - | 注册完成回调 |

### IMotionPlanner

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `plan_straight_walk` | distance_m, speed | Action | 直线行走 |
| `plan_turn_in_place` | angle_deg, angular_speed | Action | 原地掉头 |
| `plan_turn_walk` | distance_m, angle_deg, speed | Action | 转弯行走 |
| `plan_stop` | emergency | Action | 停止 |
| `plan_backward_walk` | distance_m, speed | Action | 后退 |
| `plan_sidestep` | distance_m, speed | Action | 侧移 |
| `plan_avoidance_path` | obstacles, current_pos | list[Action] | 避障路径 |
| `build_action_sequence` | descriptions | list[Action] | 批量构建 |

**build_action_sequence 输入格式：**
```python
[
    {"type": "walk_straight", "distance": 2.0, "speed": 0.5},
    {"type": "turn_in_place", "angle": 90},
    {"type": "stop"},
]
```

---

## 3. 成员B 接口（Status & UI）

### IStatusManager

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `update_robot_status` | RobotStatus | - | C 调用写入状态 |
| `get_robot_status` | - | RobotStatus | UI 读取状态 |
| `add_log` | level, source, message | - | 添加日志 |
| `get_logs` | count=100 | list[dict] | 获取日志 |
| `subscribe_status` | callback | - | 订阅状态更新 |

### ControlPanel 回调注册

```python
control_panel.on_start_task = lambda task_id: ...   # 启动任务
control_panel.on_stop_task = lambda task_id: ...    # 停止任务
control_panel.on_pause_task = lambda task_id: ...   # 暂停任务
control_panel.on_resume_task = lambda task_id: ...  # 继续任务
control_panel.on_send_action = lambda action_type, params: ...  # 手动发送动作
```

---

## 4. 成员C 接口（Communication）

### ICommunication

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `connect` | host, port | bool | 建立TCP连接 |
| `disconnect` | - | - | 断开连接 |
| `send_command` | Command | bool | 发送控制指令 |
| `start_receiving` | - | - | 开始接收 |
| `on_status_received` | callback | - | 注册状态回调 |
| `on_sensor_data` | callback | - | 注册传感器回调 |
| `is_connected` | - | bool | 连接状态 |
| `start_heartbeat` | interval_s | - | 启动心跳 |

### Command 数据格式

```python
Command(
    command_id="a1b2c3d4",        # 自动生成
    action_type=ActionType.WALK_STRAIGHT,
    params={"distance_m": 2.0, "speed": 0.5},
    timestamp=1700000000.123,
)
```

---

## 5. 共享数据模型（src/common/models.py）

### Action
```python
@dataclass
class Action:
    action_type: ActionType       # 动作类型枚举
    params: dict                  # 动作参数
    priority: TaskPriority        # 优先级
    action_id: str                # 唯一ID (8位hex)
    status: ActionStatus          # pending/running/completed/failed/interrupted
```

### Task
```python
@dataclass
class Task:
    name: str                     # 任务名称
    actions: list[Action]         # 动作序列
    priority: TaskPriority        # 优先级
    repeat: int                   # 重复次数(0=无限)
    task_id: str                  # 唯一ID
    current_action_index: int     # 当前执行到第几个动作
    is_running: bool              # 是否运行中
```

### RobotStatus
```python
@dataclass
class RobotStatus:
    state: RobotState             # idle/moving/avoiding/stopped/error
    battery: float                # 电量百分比
    position: tuple[float,float,float]  # (x, y, z)
    orientation: tuple[float,float,float]  # (roll, pitch, yaw)
    velocity: float               # 当前速度 m/s
    error_code: int               # 错误码
```

---

## 6. 集成示例

```python
from src.task_planner import TaskManager, ActionScheduler, MotionPlanner
from src.communication import APIService
from src.status_ui import StatusManager

# 1. 创建并连接
comm = APIService()
comm.connect("127.0.0.1", 9090)

# 2. 状态管理
status_mgr = StatusManager()
comm.on_status_received(status_mgr.update_robot_status)

# 3. 任务调度
scheduler = ActionScheduler(comm)
planner = MotionPlanner()
task_mgr = TaskManager()

# 4. 创建并执行任务
actions = planner.build_action_sequence([
    {"type": "walk_straight", "distance": 2.0},
    {"type": "turn_in_place", "angle": 90},
    {"type": "walk_straight", "distance": 1.0},
    {"type": "stop"},
])
task = task_mgr.create_task("demo", actions)
task_mgr.start_task(task.task_id)
```
