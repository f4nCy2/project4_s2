"""系统枚举定义"""
from enum import Enum, auto


class ActionType(Enum):
    """动作类型"""
    WALK_STRAIGHT = "walk_straight"
    TURN_IN_PLACE = "turn_in_place"
    TURN_WALK = "turn_walk"
    WALK_BACKWARD = "walk_backward"
    SIDESTEP = "sidestep"
    STOP = "stop"
    AVOID_OBSTACLE = "avoid_obstacle"


class RobotState(Enum):
    """机器人状态"""
    IDLE = "idle"
    MOVING = "moving"
    AVOIDING = "avoiding"
    STOPPED = "stopped"
    ERROR = "error"


class TaskPriority(Enum):
    """任务优先级"""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    EMERGENCY = 3


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CommandType(Enum):
    """指令类型"""
    COMMAND = "command"
    TASK_CONTROL = "task_control"
    HEARTBEAT = "heartbeat"
    STATUS = "status"


class MessageType(Enum):
    """WebSocket 消息类型"""
    STATUS = "status"
    TASK_EVENT = "task_event"
    OBSTACLE = "obstacle"
    VISION_FRAME = "vision_frame"
    ACK = "ack"
    HEARTBEAT = "heartbeat"
    ERROR = "error"
    COMMAND = "command"
    TASK_CONTROL = "task_control"
