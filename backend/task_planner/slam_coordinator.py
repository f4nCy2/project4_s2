"""2D SLAM 坐标协调器

功能：
  - 管理机器人 2D 平面坐标状态
  - 封装导航任务数据包
  - 处理坐标收敛进度
  - 计算避障后新坐标
  - 维护完整轨迹记录
"""
import math
import time
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field


@dataclass
class Robot2DPose:
    """机器人 2D 位姿"""
    x: float = 0.0       # 平面 X 坐标 (m)
    y: float = 0.0       # 平面 Y 坐标 (m)
    yaw: float = 0.0     # 航向角 (0~360°)
    timestamp: float = 0.0


@dataclass
class NavTaskState:
    """导航任务状态"""
    task_name: str = ""
    task_id: str = ""
    raw_text: str = ""
    start_x: float = 0.0
    start_y: float = 0.0
    target_x: float = 0.0
    target_y: float = 0.0
    start_location: str = ""
    target_location: str = ""
    target_object: Optional[str] = None
    current_x: float = 0.0
    current_y: float = 0.0
    current_yaw: float = 0.0
    distance_to_target: float = 0.0
    total_distance: float = 0.0
    status: str = "pending"          # pending, navigating, avoiding, completed
    started_at: float = 0.0
    obstacle_triggered: bool = False
    avoidance_count: int = 0


class SLAMCoordinator:
    """2D SLAM 坐标协调器

    管理机器人当前位置、导航目标、轨迹记录，
    计算避障后新坐标，封装各类数据包。
    """

    def __init__(self):
        self._pose = Robot2DPose()
        self._nav_task: Optional[NavTaskState] = None
        self._trajectory: List[Dict[str, Any]] = []     # 完整轨迹
        self._avoidance_log: List[Dict[str, Any]] = []  # 避障日志
        self._step_count: int = 0
        self._max_trajectory_points = 500

    # ── 属性 ──
    @property
    def current_pose(self) -> Robot2DPose:
        return self._pose

    @property
    def nav_task(self) -> Optional[NavTaskState]:
        return self._nav_task

    @property
    def trajectory(self) -> List[Dict]:
        return self._trajectory

    @property
    def avoidance_log(self) -> List[Dict]:
        return self._avoidance_log

    # ── 任务管理 ──
    def start_nav_task(self, task_data: dict) -> NavTaskState:
        """开始一个新的导航任务

        Args:
            task_data: 来自 NLP 解析器的任务数据包
        """
        import math
        start_x = task_data.get("start_x", 0.0)
        start_y = task_data.get("start_y", 0.0)
        target_x = task_data.get("target_x", 0.0)
        target_y = task_data.get("target_y", 0.0)

        total_dist = math.hypot(target_x - start_x, target_y - start_y)

        self._nav_task = NavTaskState(
            task_name=task_data.get("task_name", ""),
            task_id=f"nav_{int(time.time()*1000)}",
            raw_text=task_data.get("raw_text", ""),
            start_x=start_x,
            start_y=start_y,
            target_x=target_x,
            target_y=target_y,
            start_location=task_data.get("start_location", ""),
            target_location=task_data.get("target_location", ""),
            target_object=task_data.get("target_object"),
            current_x=start_x,
            current_y=start_y,
            current_yaw=task_data.get("initial_yaw", 0.0),
            distance_to_target=total_dist,
            total_distance=total_dist,
            status="navigating",
            started_at=time.time(),
        )

        # 重置轨迹，记录起点
        self._pose.x = start_x
        self._pose.y = start_y
        self._pose.yaw = task_data.get("initial_yaw", 0.0)
        self._pose.timestamp = time.time()

        self._trajectory = [{
            "step": 0,
            "x": start_x,
            "y": start_y,
            "yaw": self._pose.yaw,
            "distance_to_target": total_dist,
            "type": "start",
            "timestamp": self._pose.timestamp,
        }]
        self._step_count = 0
        self._avoidance_log = []

        return self._nav_task

    def update_position(self, x: float, y: float, yaw: float,
                        distance_to_target: float,
                        status: str = "navigating") -> dict:
        """更新机器人当前位置（每秒回调）

        Returns:
            dict: 格式化的位置更新数据包，供 WebSocket 推送
        """
        self._step_count += 1
        self._pose.x = x
        self._pose.y = y
        self._pose.yaw = yaw
        self._pose.timestamp = time.time()

        if self._nav_task:
            self._nav_task.current_x = x
            self._nav_task.current_y = y
            self._nav_task.current_yaw = yaw
            self._nav_task.distance_to_target = distance_to_target
            self._nav_task.status = status

        # 记录轨迹
        point = {
            "step": self._step_count,
            "x": x,
            "y": y,
            "yaw": yaw,
            "distance_to_target": distance_to_target,
            "type": status,
            "timestamp": self._pose.timestamp,
        }
        self._trajectory.append(point)
        if len(self._trajectory) > self._max_trajectory_points:
            self._trajectory = self._trajectory[-self._max_trajectory_points:]

        # 返回格式化的位置更新包
        return self._make_position_packet(x, y, yaw, distance_to_target, status)

    def _make_position_packet(self, x: float, y: float, yaw: float,
                               distance: float, status: str) -> dict:
        """封装位置更新数据包"""
        progress = 0.0
        if self._nav_task and self._nav_task.total_distance > 0.001:
            progress = max(0.0, min(1.0,
                1.0 - distance / self._nav_task.total_distance))

        return {
            "type": "nav_position_update",
            "task_id": self._nav_task.task_id if self._nav_task else "",
            "task_name": self._nav_task.task_name if self._nav_task else "",
            "current_x": round(x, 3),
            "current_y": round(y, 3),
            "yaw": round(yaw, 1),
            "distance_to_target": round(distance, 3),
            "progress": round(progress, 4),
            "status": status,
            "step": self._step_count,
            "timestamp": time.time(),
        }

    def record_avoidance(self, trigger_x: float, trigger_y: float,
                          turn_angle: float, forward_distance: float,
                          new_x: float, new_y: float,
                          new_yaw: float, remaining_distance: float) -> dict:
        """记录避障事件

        Args:
            trigger_x/y: 触发避障时的坐标
            turn_angle: 左转角度 (度)
            forward_distance: 前进距离 (m)
            new_x/y: 避障后新坐标
            new_yaw: 避障后航向角
            remaining_distance: 避障后剩余距离

        Returns:
            dict: 避障数据包
        """
        if self._nav_task:
            self._nav_task.avoidance_count += 1
            self._nav_task.obstacle_triggered = True

        log_entry = {
            "index": len(self._avoidance_log) + 1,
            "trigger_x": round(trigger_x, 3),
            "trigger_y": round(trigger_y, 3),
            "turn_angle": turn_angle,
            "forward_distance": forward_distance,
            "new_x": round(new_x, 3),
            "new_y": round(new_y, 3),
            "new_yaw": round(new_yaw, 1),
            "remaining_distance": round(remaining_distance, 3),
            "timestamp": time.time(),
        }
        self._avoidance_log.append(log_entry)

        # 也加入轨迹
        self._trajectory.append({
            "step": self._step_count,
            "x": new_x,
            "y": new_y,
            "yaw": new_yaw,
            "distance_to_target": remaining_distance,
            "type": "avoidance",
            "avoidance_index": len(self._avoidance_log),
            "timestamp": time.time(),
        })

        return {
            "type": "avoidance_event",
            "task_id": self._nav_task.task_id if self._nav_task else "",
            **log_entry,
        }

    def complete_task(self) -> Optional[dict]:
        """完成任务"""
        if not self._nav_task:
            return None
        self._nav_task.status = "completed"
        self._nav_task.distance_to_target = 0.0

        self._trajectory.append({
            "step": self._step_count + 1,
            "x": self._nav_task.target_x,
            "y": self._nav_task.target_y,
            "yaw": self._nav_task.current_yaw,
            "distance_to_target": 0.0,
            "type": "arrived",
            "timestamp": time.time(),
        })

        return {
            "type": "nav_task_completed",
            "task_id": self._nav_task.task_id,
            "task_name": self._nav_task.task_name,
            "target_location": self._nav_task.target_location,
            "total_steps": self._step_count,
            "avoidance_count": self._nav_task.avoidance_count,
            "elapsed_seconds": time.time() - self._nav_task.started_at,
            "trajectory": [{
                "step": p["step"],
                "x": p["x"],
                "y": p["y"],
                "type": p.get("type", "move"),
            } for p in self._trajectory],
        }

    def get_task_summary(self) -> dict:
        """获取当前任务摘要"""
        if not self._nav_task:
            return {"active": False}
        return {
            "active": True,
            "task_id": self._nav_task.task_id,
            "task_name": self._nav_task.task_name,
            "raw_text": self._nav_task.raw_text,
            "start_location": self._nav_task.start_location,
            "target_location": self._nav_task.target_location,
            "start": {"x": self._nav_task.start_x, "y": self._nav_task.start_y},
            "target": {"x": self._nav_task.target_x, "y": self._nav_task.target_y},
            "current": {"x": self._nav_task.current_x, "y": self._nav_task.current_y},
            "yaw": self._nav_task.current_yaw,
            "distance_to_target": round(self._nav_task.distance_to_target, 3),
            "total_distance": round(self._nav_task.total_distance, 3),
            "progress": round(
                max(0.0, min(1.0,
                    1.0 - self._nav_task.distance_to_target / max(self._nav_task.total_distance, 0.001)
                )), 4
            ),
            "status": self._nav_task.status,
            "avoidance_count": self._nav_task.avoidance_count,
            "elapsed_seconds": time.time() - self._nav_task.started_at if self._nav_task.started_at > 0 else 0,
            "step_count": self._step_count,
        }

    def get_trajectory(self) -> List[Dict]:
        """获取完整轨迹"""
        return self._trajectory

    def get_avoidance_log(self) -> List[Dict]:
        """获取避障日志"""
        return self._avoidance_log

    def reset(self):
        """重置状态"""
        self._nav_task = None
        self._trajectory = []
        self._avoidance_log = []
        self._step_count = 0
        self._pose = Robot2DPose()

    @staticmethod
    def calculate_avoidance_position(current_x: float, current_y: float,
                                      current_yaw: float,
                                      target_x: float, target_y: float,
                                      turn_angle: float = 45.0,
                                      forward_distance: float = 2.0) -> dict:
        """计算避障后的新 2D 位置

        标准避障动作：左转指定角度 → 前进指定距离

        Args:
            current_x/y: 当前坐标
            current_yaw: 当前航向角
            target_x/y: 目标坐标
            turn_angle: 左转角度 (度)，默认 45°
            forward_distance: 前进距离 (m)，默认 2m

        Returns:
            dict: {new_x, new_y, new_yaw, remaining_distance}
        """
        import math
        # 1. 左转
        new_yaw = (current_yaw + turn_angle) % 360

        # 2. 沿新航向直线前进
        rad = math.radians(new_yaw)
        new_x = current_x + forward_distance * math.cos(rad)
        new_y = current_y + forward_distance * math.sin(rad)

        # 3. 计算到终点的剩余距离
        remaining = math.hypot(target_x - new_x, target_y - new_y)

        # 4. 避障后修正航向，重新指向目标
        dx = target_x - new_x
        dy = target_y - new_y
        corrected_yaw = math.degrees(math.atan2(dy, dx)) % 360

        return {
            "new_x": round(new_x, 3),
            "new_y": round(new_y, 3),
            "new_yaw": round(new_yaw, 1),
            "corrected_yaw": round(corrected_yaw, 1),
            "remaining_distance": round(remaining, 3),
        }


# 全局单例
_slam_coordinator: Optional[SLAMCoordinator] = None


def get_slam_coordinator() -> SLAMCoordinator:
    """获取 SLAM 协调器单例"""
    global _slam_coordinator
    if _slam_coordinator is None:
        _slam_coordinator = SLAMCoordinator()
    return _slam_coordinator
