"""障碍物检测器（深度图 + 模拟 LIDAR 融合）"""
import numpy as np
from typing import Optional, Dict


class ObstacleDetector:
    """障碍物检测：支持 D435i 深度图和仿真数据融合"""

    def __init__(self, safe_distance: float = 0.8):
        self.safe_distance = safe_distance  # m
        self._last_dist = None
        self._last_direction = "center"
        self._confidence = 0.0

    def detect(self, depth_map: Optional[np.ndarray] = None,
               lidar_ranges: Optional[list] = None) -> Optional[Dict]:
        """
        检测障碍物。返回 {"distance": float, "direction": str, "confidence": float}
        或 None（无障碍）
        """
        # 简化：优先使用 lidar 数据（D435i 的栅格图结果可映射为 lidar ranges）
        if lidar_ranges is not None:
            return self._from_lidar(lidar_ranges)
        if depth_map is not None:
            return self._from_depth(depth_map)
        return None

    def _from_lidar(self, ranges: list) -> Optional[Dict]:
        """ranges: 各角度距离值，假设中心为 0°，左右均匀分布"""
        if not ranges or len(ranges) < 3:
            return None
        center = len(ranges) // 2
        # 中央 ±3 扇区取最小值
        window = ranges[max(0, center-3):min(len(ranges), center+4)]
        min_dist = min(window)
        if min_dist >= self.safe_distance or min_dist < 0.05:
            self._last_dist = None
            return None
        # 判断方向
        min_idx = ranges.index(min_dist)
        if min_idx < center - 1:
            direction = "left"
        elif min_idx > center + 1:
            direction = "right"
        else:
            direction = "center"
        self._last_dist = min_dist
        self._last_direction = direction
        self._confidence = 0.9
        return {"distance": min_dist, "direction": direction, "confidence": 0.9}

    def _from_depth(self, depth_map: np.ndarray) -> Optional[Dict]:
        """从深度图提取中心区域最小距离"""
        h, w = depth_map.shape
        # 取中央区域
        cx, cy = w // 2, h // 2
        roi = depth_map[max(0, cy-60):min(h, cy+60), max(0, cx-80):min(w, cx+80)]
        valid = roi[(roi > 0.05) & (roi < 10.0)]
        if valid.size == 0:
            return None
        min_dist = float(np.min(valid))
        if min_dist >= self.safe_distance:
            return None
        return {"distance": min_dist, "direction": "center", "confidence": 0.85}

    @property
    def last_detection(self) -> Optional[Dict]:
        if self._last_dist is None:
            return None
        return {"distance": self._last_dist,
                "direction": self._last_direction,
                "confidence": self._confidence}
