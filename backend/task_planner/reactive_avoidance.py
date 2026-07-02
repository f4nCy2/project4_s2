"""实时避障监控闭环"""
import asyncio
from typing import Optional, Callable
from backend.task_planner.obstacle_detector import ObstacleDetector
from backend.task_planner.avoidance_planner import AvoidancePlanner
from backend.task_planner.motion_planner import MotionPlanner
from backend.common.models import Action, ActionType


class ReactiveAvoidance:
    """实时避障监控：周期性检测 + 自动触发避障序列"""

    def __init__(self, detector: ObstacleDetector, planner: AvoidancePlanner):
        self.detector = detector
        self.planner = planner
        self._active = False
        self._avoiding = False
        self._on_avoid_triggered: Optional[Callable] = None
        self._task: Optional[asyncio.Task] = None

    def set_callback(self, callback: Callable) -> None:
        """设置避障触发回调（传入避障动作列表）"""
        self._on_avoid_triggered = callback

    def start(self) -> None:
        self._active = True
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._monitor_loop())

    def stop(self) -> None:
        self._active = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _monitor_loop(self) -> None:
        """检测循环：每 500ms 检查一次"""
        while self._active:
            if not self._avoiding:
                result = self.detector.detect()
                if result and self._on_avoid_triggered:
                    self._avoiding = True
                    actions = self.planner.plan(result["distance"], result["direction"])
                    self._on_avoid_triggered(actions)
            await asyncio.sleep(0.5)

    def on_avoidance_complete(self) -> None:
        """避障序列完成后调用，恢复检测"""
        self._avoiding = False

    def reset(self) -> None:
        self._avoiding = False
        self._active = False
