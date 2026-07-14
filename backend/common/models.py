"""数据模型（Pydantic v2）"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from .enums import ActionType, RobotState, TaskStatus, TaskPriority


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
