"""数据模型（Pydantic v2）"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from .enums import ActionType, RobotState, TaskStatus, TaskPriority, NavTaskStatus


class Position(BaseModel):
    """三维位置"""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class Orientation(BaseModel):
    """姿态（Roll/Pitch/Yaw，单位：度）"""
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0


class JointState(BaseModel):
    """关节状态"""
    left_knee: str = "ok"
    right_knee: str = "ok"
    hip: str = "ok"
    left_shoulder: str = "ok"
    right_shoulder: str = "ok"
    neck: str = "ok"


class RobotStatus(BaseModel):
    """机器人状态快照"""
    state: RobotState = RobotState.IDLE
    battery: float = 100.0
    position: Position = Field(default_factory=Position)
    orientation: Orientation = Field(default_factory=Orientation)
    velocity: float = 0.0
    error_code: int = 0
    cpu: float = 0.0
    obstacle_dist: Optional[float] = None
    joints: JointState = Field(default_factory=JointState)
    connected: bool = False
    timestamp: float = 0.0


class ActionParams(BaseModel):
    """动作参数"""
    distance: Optional[float] = None
    speed: Optional[float] = None
    angle: Optional[float] = None
    direction: Optional[str] = None
    duration: Optional[float] = None
    emergency: bool = False


class Action(BaseModel):
    """单个动作"""
    id: int
    type: ActionType
    device: str = "底盘"
    params: ActionParams = Field(default_factory=ActionParams)
    condition: Optional[str] = None
    fail_handler: Optional[str] = None


class Task(BaseModel):
    """任务"""
    id: str = Field(default_factory=lambda: f"task_{id(Task())}")
    name: str = ""
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.NORMAL
    actions: List[Action] = Field(default_factory=list)
    current_action_index: int = 0
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class Command(BaseModel):
    """指令消息"""
    type: str = "command"
    action: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    seq: int = 0
    priority: str = "NORMAL"
    task_id: Optional[str] = None
    action_id: Optional[int] = None  # 闭环确认：动作ID


class TaskControlCommand(BaseModel):
    """任务控制指令"""
    type: str = "task_control"
    command: str  # start, stop, pause, resume
    task_id: str
    seq: int = 0


class HeartbeatMessage(BaseModel):
    """心跳消息"""
    type: str = "heartbeat"
    seq: int
    timestamp: float


class StatusMessage(BaseModel):
    """状态消息"""
    type: str = "status"
    status: RobotStatus


class TaskEventMessage(BaseModel):
    """任务事件消息"""
    type: str = "task_event"
    event: str  # created, started, paused, resumed, stopped, completed
    task_id: str
    task_name: str
    action_index: int = 0
    total_actions: int = 0


class ObstacleMessage(BaseModel):
    """障碍物检测消息"""
    type: str = "obstacle"
    distance: float
    direction: str = "center"
    confidence: Optional[float] = None


class VisionFrameMessage(BaseModel):
    """视觉帧消息"""
    type: str = "vision_frame"
    frame: str  # base64 JPEG
    detections: Optional[List[Dict[str, Any]]] = None


class AckMessage(BaseModel):
    """确认消息"""
    type: str = "ack"
    seq: int
    action: str
    status: str


class ErrorMessage(BaseModel):
    """错误消息"""
    type: str = "error"
    message: str
    source: Optional[str] = None


class TaskPlannedMessage(BaseModel):
    """任务计划消息"""
    type: str = "task_planned"
    task_id: str
    task_name: str
    actions: List[Action]
    total_steps: int


# ═══════════════════════════════════════════════════════════
# 2D SLAM 导航相关模型（新增 - 不修改原有模型）
# ═══════════════════════════════════════════════════════════

class Point2D(BaseModel):
    """2D 平面坐标"""
    x: float = 0.0
    y: float = 0.0


class LocationInfo(BaseModel):
    """室内场景点位信息"""
    name: str
    x: float
    y: float
    description: str = ""


class NLPTaskRequest(BaseModel):
    """自然语言任务请求"""
    type: str = "nlp_task"
    text: str = ""                          # 自然语言描述
    current_location: str = "客厅"           # 机器人当前位置


class NavTaskPacket(BaseModel):
    """导航任务数据包（下发至机器人模拟端）"""
    type: str = "nav_task"
    task_name: str = ""
    raw_text: str = ""
    start_location: str = ""
    target_location: str = ""
    start_x: float = 0.0
    start_y: float = 0.0
    target_x: float = 0.0
    target_y: float = 0.0
    initial_yaw: float = 0.0
    target_object: Optional[str] = None
    action: str = "navigate"


class NavPositionUpdate(BaseModel):
    """机器人位置更新（每秒回传）"""
    type: str = "nav_position_update"
    task_id: str = ""
    task_name: str = ""
    current_x: float = 0.0
    current_y: float = 0.0
    yaw: float = 0.0
    distance_to_target: float = 0.0
    progress: float = 0.0
    status: str = "navigating"
    step: int = 0
    timestamp: float = 0.0


class AvoidanceEvent(BaseModel):
    """避障事件"""
    type: str = "avoidance_event"
    task_id: str = ""
    index: int = 0
    trigger_x: float = 0.0
    trigger_y: float = 0.0
    turn_angle: float = 45.0
    forward_distance: float = 2.0
    new_x: float = 0.0
    new_y: float = 0.0
    new_yaw: float = 0.0
    remaining_distance: float = 0.0
    timestamp: float = 0.0


class NavTaskCompleted(BaseModel):
    """导航任务完成"""
    type: str = "nav_task_completed"
    task_id: str = ""
    task_name: str = ""
    target_location: str = ""
    total_steps: int = 0
    avoidance_count: int = 0
    elapsed_seconds: float = 0.0
    trajectory: List[Dict[str, Any]] = Field(default_factory=list)


class NavTaskSummary(BaseModel):
    """导航任务摘要"""
    active: bool = False
    task_id: str = ""
    task_name: str = ""
    raw_text: str = ""
    start_location: str = ""
    target_location: str = ""
    start: Point2D = Field(default_factory=Point2D)
    target: Point2D = Field(default_factory=Point2D)
    current: Point2D = Field(default_factory=Point2D)
    yaw: float = 0.0
    distance_to_target: float = 0.0
    total_distance: float = 0.0
    progress: float = 0.0
    status: str = "idle"
    avoidance_count: int = 0
    elapsed_seconds: float = 0.0
    step_count: int = 0
