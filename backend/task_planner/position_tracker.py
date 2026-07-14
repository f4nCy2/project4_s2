"""坐标追踪器：接收机器人实时坐标，维护轨迹历史"""
import time
from typing import List, Dict, Optional, Callable
from collections import deque


class PositionTracker:
    """追踪机器人实时坐标，维护轨迹历史
    
    功能：
      - 接收 status / action_event 中的坐标
      - 维护最近 N 个轨迹点
      - 计算移动距离、速度等统计
      - 广播坐标更新给前端
    """

    MAX_TRAJECTORY_POINTS = 200  # 最大轨迹点数

    def __init__(self):
        self._trajectory: deque = deque(maxlen=self.MAX_TRAJECTORY_POINTS)
        self._current_position: Optional[Dict[str, float]] = None
        self._current_yaw: float = 0.0
        self._last_update_time: float = 0.0
        self._total_distance: float = 0.0
        self._position_callbacks: List[Callable] = []
        self._lock = False  # 简单锁标记

    def subscribe(self, callback: Callable):
        """订阅坐标更新事件"""
        self._position_callbacks.append(callback)

    def update_from_status(self, status: dict):
        """从 status 消息中提取坐标"""
        pos = status.get("position", {})
        ori = status.get("orientation", {})
        self._update_position(
            x=pos.get("x", 0.0),
            y=pos.get("y", 0.0),
            z=pos.get("z", 0.0),
            yaw=ori.get("yaw", 0.0),
            timestamp=status.get("timestamp", time.time())
        )

    def update_from_action_event(self, event: dict):
        """从 action_event 中提取坐标"""
        pos = event.get("position", {})
        ori = event.get("orientation", {})
        self._update_position(
            x=pos.get("x", 0.0),
            y=pos.get("y", 0.0),
            z=pos.get("z", 0.0),
            yaw=ori.get("yaw", 0.0),
            timestamp=event.get("timestamp", time.time())
        )

    def _update_position(self, x: float, y: float, z: float, yaw: float, timestamp: float):
        if self._lock:
            return
        self._lock = True

        try:
            # 计算移动距离
            if self._current_position:
                import math
                dx = x - self._current_position["x"]
                dy = y - self._current_position["y"]
                dist = math.hypot(dx, dy)
                self._total_distance += dist

            self._current_position = {"x": x, "y": y, "z": z}
            self._current_yaw = yaw
            self._last_update_time = timestamp

            # 添加到轨迹
            self._trajectory.append({
                "x": round(x, 3),
                "y": round(y, 3),
                "yaw": round(yaw, 2),
                "timestamp": timestamp
            })

            # 通知订阅者
            self._notify()
        finally:
            self._lock = False

    def _notify(self):
        payload = {
            "type": "robot_position",
            "position": self._current_position,
            "yaw": round(self._current_yaw, 2),
            "trajectory": list(self._trajectory),
            "total_distance": round(self._total_distance, 3),
            "timestamp": time.time()
        }
        for cb in self._position_callbacks:
            try:
                cb(payload)
            except Exception as e:
                print(f"[PositionTracker] 回调异常: {e}")

    def get_position(self) -> Optional[Dict[str, float]]:
        return self._current_position

    def get_yaw(self) -> float:
        return self._current_yaw

    def get_trajectory(self) -> List[Dict]:
        return list(self._trajectory)

    def get_total_distance(self) -> float:
        return self._total_distance

    def reset(self):
        """重置轨迹（例如新任务开始时）"""
        self._trajectory.clear()
        self._total_distance = 0.0
        self._current_position = None

    def clear(self):
        """清除所有数据"""
        self._trajectory.clear()
        self._total_distance = 0.0
        self._current_position = None
        self._current_yaw = 0.0
