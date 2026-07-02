"""避障路径规划器"""
from typing import List, Optional
from backend.common.models import Action, ActionType
from backend.task_planner.motion_planner import MotionPlanner


class AvoidancePlanner:
    """避障路径规划：侧移绕行策略"""

    def __init__(self, side_clearance: float = 0.5, step_length: float = 1.0):
        self.side_clearance = side_clearance  # 侧移距离
        self.step_length = step_length        # 绕行前进距离

    def plan(self, obstacle_distance: float, obstacle_direction: str = "center") -> List[Action]:
        """规划绕行路径：侧移 → 直行 → 恢复"""
        # 决定侧移方向（障碍物在左则右移，反之）
        if obstacle_direction == "left":
            sidestep_dir = "right"
            sidestep_dist = self.side_clearance
        elif obstacle_direction == "right":
            sidestep_dir = "left"
            sidestep_dist = -self.side_clearance
        else:
            # 中央：默认右移
            sidestep_dir = "right"
            sidestep_dist = self.side_clearance

        actions = [
            MotionPlanner.stop(),  # 先停
            MotionPlanner.sidestep(sidestep_dist, speed=0.3),
            MotionPlanner.walk_straight(self.step_length, speed=0.5),
            MotionPlanner.sidestep(-sidestep_dist, speed=0.3),  # 恢复
            MotionPlanner.walk_straight(0.5, speed=0.8),  # 继续
        ]
        return MotionPlanner.build_sequence(*actions)

    def plan_simple_avoid(self, direction: str = "right") -> List[Action]:
        """简化避障：转45° → 直行 → 恢复"""
        angle = 45 if direction == "right" else -45
        actions = [
            MotionPlanner.stop(),
            MotionPlanner.turn_in_place(angle, speed=0.3),
            MotionPlanner.walk_straight(1.0, speed=0.4),
            MotionPlanner.turn_in_place(-angle, speed=0.3),
            MotionPlanner.walk_straight(0.5, speed=0.8),
        ]
        return MotionPlanner.build_sequence(*actions)
