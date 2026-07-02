"""动作参数生成器 / 运动规划器"""
from typing import List, Optional
from backend.common.models import Action, ActionParams, ActionType


class MotionPlanner:
    """7种动作参数生成，支持组合动作序列批量构建"""

    @staticmethod
    def walk_straight(distance_m: float, speed: float = 0.8) -> Action:
        return Action(
            id=0, type=ActionType.WALK_STRAIGHT,
            device="底盘",
            params=ActionParams(distance=distance_m, speed=speed)
        )

    @staticmethod
    def turn_in_place(angle_deg: float, speed: float = 0.3) -> Action:
        """角度为正 = 左转，为负 = 右转"""
        direction = "left" if angle_deg > 0 else "right"
        return Action(
            id=0, type=ActionType.TURN_IN_PLACE,
            device="底盘",
            params=ActionParams(angle=abs(angle_deg), speed=speed, direction=direction)
        )

    @staticmethod
    def turn_walk(distance_m: float, angle_deg: float, speed: float = 0.8) -> Action:
        direction = "left" if angle_deg > 0 else "right"
        return Action(
            id=0, type=ActionType.TURN_WALK,
            device="底盘",
            params=ActionParams(distance=distance_m, angle=abs(angle_deg),
                               speed=speed, direction=direction)
        )

    @staticmethod
    def walk_backward(distance_m: float, speed: float = 0.5) -> Action:
        return Action(
            id=0, type=ActionType.WALK_BACKWARD,
            device="底盘",
            params=ActionParams(distance=distance_m, speed=speed)
        )

    @staticmethod
    def sidestep(distance_m: float, speed: float = 0.4) -> Action:
        """距离为正 = 右移，为负 = 左移"""
        direction = "right" if distance_m > 0 else "left"
        return Action(
            id=0, type=ActionType.SIDESTEP,
            device="底盘",
            params=ActionParams(distance=abs(distance_m), speed=speed, direction=direction)
        )

    @staticmethod
    def stop(emergency: bool = False) -> Action:
        return Action(
            id=0, type=ActionType.STOP,
            device="底盘",
            params=ActionParams(emergency=emergency)
        )

    @staticmethod
    def avoid_obstacle(enable: bool = True) -> Action:
        """
        启动/停止 d435i 避障模式。
        后端不规划路径，仅向机器人底层发送模式切换指令。
        实际避障由 d435i 算法直接控制机器人。
        """
        return Action(
            id=0, type=ActionType.AVOID_OBSTACLE,
            device="视觉+底盘",
            params=ActionParams(emergency=not enable),  # emergency=True 表示停止避障
            condition="d435i_avoidance_mode"
        )

    @staticmethod
    def build_sequence(*actions: Action) -> List[Action]:
        """给动作列表分配顺序 id"""
        for i, a in enumerate(actions):
            a.id = i + 1
        return list(actions)
