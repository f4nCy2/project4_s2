#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gazebo 仿真桥接脚本 — 替换 robot_simulator.py，兼容原 TCP 协议

功能：
  1. 启动 TCP Server（端口 9090），兼容原 4 字节长度前缀 + JSON 协议
  2. 作为 ROS 节点，连接 Gazebo 中的机器人
  3. 接收后端 command → 转换为 ROS Twist → 控制 Gazebo 机器人
  4. 接收 d435i low_level_control → 直接发布 Twist（绕过动作系统，跟真机一致）
  5. 订阅 /odom → 实时位姿 → 转换为 status 回传后端
  6. 动作闭环：跟踪动作执行进度，回传 action_event (started/progress/completed/failed)
  7. 订阅 /camera/color/image_raw 和 /camera/depth/image_raw → 可选转发给 vision_server

用法：
    # 需要先 source ROS
    source /opt/ros/noetic/setup.bash
    python3 gazebo_bridge.py

环境变量：
    ROBOT_HOST  - TCP 监听地址，默认 0.0.0.0
    ROBOT_PORT  - TCP 监听端口，默认 9090
    GAZEBO_ODOM_TOPIC  - 里程计话题，默认 /odom
    GAZEBO_CMD_TOPIC   - 速度指令话题，默认 /cmd_vel
    GAZEBO_MODEL_NAME  - Gazebo 模型名，默认 robot
    ENABLE_CAMERA      - 是否启用相机转发，默认 true
    VISION_WS_URL      - 视觉帧服务器 WebSocket 地址，默认 ws://127.0.0.1:8765
"""

import asyncio
import json
import math
import os
import signal
import struct
import time
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set

import rospy
from geometry_msgs.msg import Twist, TwistStamped
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image, CameraInfo
from std_msgs.msg import Float64
from cv_bridge import CvBridge
import cv2
import numpy as np
import base64


# ── 配置 ──
ROBOT_HOST = os.getenv("ROBOT_HOST", "0.0.0.0")
ROBOT_PORT = int(os.getenv("ROBOT_PORT", "9090"))
ODOM_TOPIC = os.getenv("GAZEBO_ODOM_TOPIC", "/odom")
CMD_TOPIC = os.getenv("GAZEBO_CMD_TOPIC", "/cmd_vel")
MODEL_NAME = os.getenv("GAZEBO_MODEL_NAME", "robot")
STATUS_INTERVAL = 0.5  # 状态上报周期 (s)
ENABLE_CAMERA = os.getenv("ENABLE_CAMERA", "true").lower() == "true"
VISION_WS_URL = os.getenv("VISION_WS_URL", "ws://127.0.0.1:8765")


# ══════════════════════════════════════════════════════════════
# ROS 节点封装（在独立线程中运行）
# ══════════════════════════════════════════════════════════════

@dataclass
class RobotPose:
    """机器人位姿"""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    vth: float = 0.0

    def to_dict(self) -> dict:
        return {"x": round(self.x, 4), "y": round(self.y, 4), "z": round(self.z, 4)}

    def orientation_dict(self) -> dict:
        return {"roll": 0.0, "pitch": 0.0, "yaw": round(self.yaw, 2)}


class ROSInterface:
    """ROS 接口：订阅 Gazebo 数据，发布控制指令"""

    def __init__(self, odom_topic: str, cmd_topic: str):
        self.odom_topic = odom_topic
        self.cmd_topic = cmd_topic
        self.pose = RobotPose()
        self._lock = threading.Lock()
        self._cmd_pub = None
        self._cv_bridge = CvBridge()
        self.latest_color_frame: Optional[str] = None  # base64 JPEG
        self.latest_depth_frame: Optional[str] = None  # base64 JPEG
        self._running = True

    def start(self):
        """在调用线程中初始化 ROS（需先 source /opt/ros/noetic/setup.bash）"""
        rospy.init_node("gazebo_bridge", anonymous=True, disable_signals=True)

        self._cmd_pub = rospy.Publisher(self.cmd_topic, Twist, queue_size=10)
        rospy.Subscriber(self.odom_topic, Odometry, self._on_odom)

        if ENABLE_CAMERA:
            rospy.Subscriber("/camera/color/image_raw", Image, self._on_color)
            rospy.Subscriber("/camera/depth/image_raw", Image, self._on_depth)
            rospy.Subscriber("/camera/color/camera_info", CameraInfo, self._on_camera_info)

        print(f"[ROSInterface] 已订阅: {self.odom_topic}")
        print(f"[ROSInterface] 已发布: {self.cmd_topic}")
        if ENABLE_CAMERA:
            print(f"[ROSInterface] 已订阅: /camera/color/image_raw, /camera/depth/image_raw")

    def stop(self):
        self._running = False
        rospy.signal_shutdown("gazebo_bridge stopping")

    def _on_odom(self, msg: Odometry):
        """处理里程计消息"""
        p = msg.pose.pose.position
        o = msg.pose.pose.orientation
        # 四元数 → yaw
        yaw = math.atan2(2.0 * (o.w * o.z + o.x * o.y),
                         1.0 - 2.0 * (o.y * o.y + o.z * o.z))
        yaw_deg = math.degrees(yaw)

        v = msg.twist.twist.linear
        w = msg.twist.twist.angular

        with self._lock:
            self.pose.x = p.x
            self.pose.y = p.y
            self.pose.z = p.z
            self.pose.yaw = yaw_deg
            self.pose.vx = v.x
            self.pose.vy = v.y
            self.pose.vth = w.z

    def _on_color(self, msg: Image):
        """处理彩色图像，编码为 base64 JPEG"""
        try:
            cv_img = self._cv_bridge.imgmsg_to_cv2(msg, "bgr8")
            # 压缩为 JPEG 降低带宽
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 70]
            ret, buf = cv2.imencode(".jpg", cv_img, encode_param)
            if ret:
                self.latest_color_frame = base64.b64encode(buf.tobytes()).decode("utf-8")
        except Exception as e:
            print(f"[ROSInterface] 彩色帧处理失败: {e}")

    def _on_depth(self, msg: Image):
        """处理深度图像，编码为伪彩色 JPEG"""
        try:
            cv_img = self._cv_bridge.imgmsg_to_cv2(msg, desired_encoding="32FC1")
            # 裁剪到 4m 以内，转为伪彩色
            depth_vis = np.clip(cv_img, 0, 4.0)
            depth_norm = cv2.convertScaleAbs(depth_vis, alpha=255.0 / 4.0)
            depth_color = cv2.applyColorMap(depth_norm, cv2.COLORMAP_JET)
            ret, buf = cv2.imencode(".jpg", depth_color, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if ret:
                self.latest_depth_frame = base64.b64encode(buf.tobytes()).decode("utf-8")
        except Exception as e:
            print(f"[ROSInterface] 深度帧处理失败: {e}")

    def _on_camera_info(self, msg: CameraInfo):
        """相机内参（仅打印一次）"""
        if not hasattr(self, '_camera_info_printed'):
            print(f"[ROSInterface] 相机: {msg.width}x{msg.height}, K={msg.K[:3]}")
            self._camera_info_printed = True

    def get_pose(self) -> RobotPose:
        with self._lock:
            return RobotPose(
                x=self.pose.x, y=self.pose.y, z=self.pose.z,
                yaw=self.pose.yaw, vx=self.pose.vx, vy=self.pose.vy, vth=self.pose.vth
            )

    def send_velocity(self, linear_x: float, angular_z: float):
        """发布速度指令到 Gazebo"""
        if self._cmd_pub is None:
            return
        twist = Twist()
        twist.linear.x = linear_x
        twist.linear.y = 0.0
        twist.linear.z = 0.0
        twist.angular.x = 0.0
        twist.angular.y = 0.0
        twist.angular.z = angular_z
        self._cmd_pub.publish(twist)

    def stop_robot(self):
        self.send_velocity(0.0, 0.0)


# ══════════════════════════════════════════════════════════════
# 动作执行引擎（复用 robot_simulator.py 的物理模型，但使用 Gazebo 的 odom）
# ══════════════════════════════════════════════════════════════

@dataclass
class ActionExecution:
    task_id: str
    action_id: int
    action_type: str
    params: dict
    start_time: float
    estimated_duration: float
    status: str = "running"
    progress: float = 0.0
    detail: str = ""


class ActionExecutor:
    """动作执行引擎：跟踪动作执行进度，基于 Gazebo odometry 做闭环"""

    DEFAULT_PARAMS = {
        "walk_straight": {"distance": 2.0, "speed": 0.8},
        "turn_in_place": {"angle": 45.0, "speed": 0.3, "direction": "left"},
        "turn_walk": {"distance": 1.0, "angle": 45.0, "speed": 0.6},
        "walk_backward": {"distance": 1.0, "speed": 0.5},
        "sidestep": {"distance": 0.5, "speed": 0.3, "direction": "left"},
        "stop": {"emergency": False},
        "avoid_obstacle": {"duration": 5.0},
    }

    def __init__(self, ros_interface: ROSInterface):
        self._ros = ros_interface
        self._current: Optional[ActionExecution] = None
        self._lock = asyncio.Lock()
        self._cancelled = False

    async def execute(self, task_id: str, action_id: int, action_type: str,
                      params: dict) -> ActionExecution:
        merged = {**self.DEFAULT_PARAMS.get(action_type, {}), **params}

        async with self._lock:
            self._cancelled = False
            self._current = ActionExecution(
                task_id=task_id, action_id=action_id,
                action_type=action_type, params=merged,
                start_time=time.time(),
                estimated_duration=self._estimate_duration(action_type, merged),
                detail="动作开始"
            )

        await self._emit("started", 0.0, "动作开始")
        result = await self._run_action(action_type, merged)

        async with self._lock:
            self._current = None

        return result

    async def _run_action(self, action_type: str, params: dict) -> ActionExecution:
        if action_type == "walk_straight":
            return await self._exec_walk_straight(params)
        elif action_type == "turn_in_place":
            return await self._exec_turn_in_place(params)
        elif action_type == "turn_walk":
            return await self._exec_turn_walk(params)
        elif action_type == "walk_backward":
            return await self._exec_walk_backward(params)
        elif action_type == "sidestep":
            return await self._exec_sidestep(params)
        elif action_type == "stop":
            return await self._exec_stop(params)
        elif action_type == "avoid_obstacle":
            return await self._exec_avoid_obstacle(params)
        else:
            return await self._exec_unknown(action_type, params)

    # ── 各动作具体实现（使用 Gazebo odometry 闭环）──

    async def _exec_walk_straight(self, params: dict) -> ActionExecution:
        distance = params.get("distance", 2.0)
        speed = abs(params.get("speed", 0.8))
        if distance <= 0 or speed <= 0:
            return await self._fail("距离或速度参数无效")
        self._ros.send_velocity(speed, 0.0)
        return await self._wait_for_distance(distance, f"直线行走 {distance}m", linear_x=speed, angular_z=0.0)
        distance = params.get("distance", 2.0)
        speed = abs(params.get("speed", 0.8))
        if distance <= 0 or speed <= 0:
            return await self._fail("距离或速度参数无效")
        self._ros.send_velocity(speed, 0.0)
        return await self._wait_for_distance(distance, f"直线行走 {distance}m")

    async def _exec_turn_in_place(self, params: dict) -> ActionExecution:
        angle = abs(params.get("angle", 45.0))
        direction = params.get("direction", "left")
        angular_speed = abs(params.get("speed", 0.3)) * 60  # deg/s
        steer = angular_speed if direction == "left" else -angular_speed
        angular_z = math.radians(steer)
        self._ros.send_velocity(0.0, angular_z)
        return await self._wait_for_angle(angle, f"原地转向 {direction}", angular_z=angular_z)
        angle = abs(params.get("angle", 45.0))
        direction = params.get("direction", "left")
        angular_speed = abs(params.get("speed", 0.3)) * 60  # deg/s
        steer = angular_speed if direction == "left" else -angular_speed
        self._ros.send_velocity(0.0, math.radians(steer))
        return await self._wait_for_angle(angle, f"原地转向 {direction}")

    async def _exec_turn_walk(self, params: dict) -> ActionExecution:
        distance = params.get("distance", 1.0)
        speed = abs(params.get("speed", 0.6))
        self._ros.send_velocity(speed, 0.0)
        return await self._wait_for_distance(distance, f"转弯行走 {distance}m", linear_x=speed, angular_z=0.0)
        distance = params.get("distance", 1.0)
        speed = abs(params.get("speed", 0.6))
        self._ros.send_velocity(speed, 0.0)
        return await self._wait_for_distance(distance, f"转弯行走 {distance}m")

    async def _exec_walk_backward(self, params: dict) -> ActionExecution:
        distance = params.get("distance", 1.0)
        speed = abs(params.get("speed", 0.5))
        self._ros.send_velocity(-speed, 0.0)
        return await self._wait_for_distance(distance, f"后退 {distance}m", linear_x=-speed, angular_z=0.0)
        distance = params.get("distance", 1.0)
        speed = abs(params.get("speed", 0.5))
        self._ros.send_velocity(-speed, 0.0)
        return await self._wait_for_distance(distance, f"后退 {distance}m")

    async def _exec_sidestep(self, params: dict) -> ActionExecution:
        # 在差速轮式机器人上，sidestep 近似为：原地旋转 → 前进 → 旋转复位
        # 实际人形机器人有侧移能力，这里做简化近似
        distance = params.get("distance", 0.5)
        speed = abs(params.get("speed", 0.3))
        direction = params.get("direction", "left")
        turn_dir = 1 if direction == "left" else -1

        # 阶段1: 转向 90°
        angular_z = math.radians(30 * turn_dir)
        self._ros.send_velocity(0.0, angular_z)
        await self._wait_for_angle(90, f"侧移准备转向", angular_z=angular_z)

        # 阶段2: 前进
        self._ros.send_velocity(speed, 0.0)
        await self._wait_for_distance(distance, f"侧移 {distance}m", linear_x=speed, angular_z=0.0)

        # 阶段3: 转回
        angular_z = math.radians(-30 * turn_dir)
        self._ros.send_velocity(0.0, angular_z)
        return await self._wait_for_angle(90, f"侧移复位", angular_z=angular_z)
        # 在差速轮式机器人上，sidestep 近似为：原地旋转 → 前进 → 旋转复位
        # 实际人形机器人有侧移能力，这里做简化近似
        distance = params.get("distance", 0.5)
        speed = abs(params.get("speed", 0.3))
        direction = params.get("direction", "left")
        turn_dir = 1 if direction == "left" else -1

        # 阶段1: 转向 90°
        self._ros.send_velocity(0.0, math.radians(30 * turn_dir))
        await self._wait_for_angle(90, f"侧移准备转向")

        # 阶段2: 前进
        self._ros.send_velocity(speed, 0.0)
        await self._wait_for_distance(distance, f"侧移 {distance}m")

        # 阶段3: 转回
        self._ros.send_velocity(0.0, math.radians(-30 * turn_dir))
        return await self._wait_for_angle(90, f"侧移复位")

    async def _exec_stop(self, params: dict) -> ActionExecution:
        self._ros.stop_robot()
        self._current.status = "completed"
        self._current.progress = 1.0
        self._current.detail = "已停止"
        await self._emit("completed", 1.0, "已停止")
        return self._current

    async def _exec_avoid_obstacle(self, params: dict) -> ActionExecution:
        duration = params.get("duration", 5.0)
        import random
        steer = random.uniform(-30, 30)
        angular_z = math.radians(steer)
        self._ros.send_velocity(0.4, angular_z)
        return await self._wait_for_duration(duration, "视觉避障模式")
        duration = params.get("duration", 5.0)
        import random
        steer = random.uniform(-30, 30)
        self._ros.send_velocity(0.4, math.radians(steer))
        return await self._wait_for_duration(duration, "视觉避障模式")

    async def _exec_unknown(self, action_type: str, params: dict) -> ActionExecution:
        self._ros.stop_robot()
        return await self._fail(f"未知动作类型: {action_type}")

    # ── 通用等待工具（基于 Gazebo odometry 闭环）──

    async def _wait_for_duration(self, duration: float, desc: str) -> ActionExecution:
        start = time.time()
        tick = 0.2
        while time.time() - start < duration:
            if self._cancelled:
                self._ros.stop_robot()
                return await self._fail("动作被取消")
            elapsed = time.time() - start
            progress = min(elapsed / duration, 1.0)
            self._current.progress = progress
            await self._emit("progress", progress, f"{desc} 进行中")
            await asyncio.sleep(tick)
        self._ros.stop_robot()
        self._current.status = "completed"
        self._current.progress = 1.0
        self._current.detail = f"{desc} 完成"
        await self._emit("completed", 1.0, f"{desc} 完成")
        return self._current

    async def _wait_for_distance(self, target_dist: float, desc: str, linear_x: float = 0.0, angular_z: float = 0.0) -> ActionExecution:
        start_pose = self._ros.get_pose()
        start = time.time()
        tick = 0.2
        # 保守超时估计：距离 / 0.3m/s + 10s
        timeout = target_dist / 0.3 + 10.0

        while True:
            if self._cancelled:
                self._ros.stop_robot()
                return await self._fail("动作被取消")

            # 持续发送速度指令（防止 topic 超时或丢包）
            if linear_x != 0.0 or angular_z != 0.0:
                self._ros.send_velocity(linear_x, angular_z)

            pose = self._ros.get_pose()
            travelled = math.hypot(pose.x - start_pose.x, pose.y - start_pose.y)
            progress = min(travelled / target_dist, 1.0)
            self._current.progress = progress

            elapsed = time.time() - start
            if travelled >= target_dist or elapsed >= timeout:
                self._ros.stop_robot()
                self._current.status = "completed"
                self._current.progress = 1.0
                self._current.detail = f"{desc} 完成，实际行走 {travelled:.2f}m"
                await self._emit("completed", 1.0, f"{desc} 完成，实际行走 {travelled:.2f}m")
                return self._current

            await self._emit("progress", progress, f"{desc} 已行走 {travelled:.2f}m / {target_dist:.2f}m")
            await asyncio.sleep(tick)
        start_pose = self._ros.get_pose()
        start = time.time()
        tick = 0.2
        # 保守超时估计：距离 / 0.3m/s + 10s
        timeout = target_dist / 0.3 + 10.0

        while True:
            if self._cancelled:
                self._ros.stop_robot()
                return await self._fail("动作被取消")

            pose = self._ros.get_pose()
            travelled = math.hypot(pose.x - start_pose.x, pose.y - start_pose.y)
            progress = min(travelled / target_dist, 1.0)
            self._current.progress = progress

            elapsed = time.time() - start
            if travelled >= target_dist or elapsed >= timeout:
                self._ros.stop_robot()
                self._current.status = "completed"
                self._current.progress = 1.0
                self._current.detail = f"{desc} 完成，实际行走 {travelled:.2f}m"
                await self._emit("completed", 1.0, f"{desc} 完成，实际行走 {travelled:.2f}m")
                return self._current

            await self._emit("progress", progress, f"{desc} 已行走 {travelled:.2f}m / {target_dist:.2f}m")
            await asyncio.sleep(tick)

    async def _wait_for_angle(self, target_angle: float, desc: str, angular_z: float = 0.0) -> ActionExecution:
        start_yaw = self._ros.get_pose().yaw
        start = time.time()
        tick = 0.2
        timeout = target_angle / 15.0 + 10.0  # 保守超时

        while True:
            if self._cancelled:
                self._ros.stop_robot()
                return await self._fail("动作被取消")

            # 持续发送角速度指令
            if angular_z != 0.0:
                self._ros.send_velocity(0.0, angular_z)

            pose = self._ros.get_pose()
            diff = abs(self._angle_diff(pose.yaw, start_yaw))
            progress = min(diff / target_angle, 1.0)
            self._current.progress = progress

            elapsed = time.time() - start
            if diff >= target_angle or elapsed >= timeout:
                self._ros.stop_robot()
                self._current.status = "completed"
                self._current.progress = 1.0
                self._current.detail = f"{desc} 完成，实际转向 {diff:.1f}°"
                await self._emit("completed", 1.0, f"{desc} 完成，实际转向 {diff:.1f}°")
                return self._current

            await self._emit("progress", progress, f"{desc} 已转向 {diff:.1f}° / {target_angle:.1f}°")
            await asyncio.sleep(tick)
        start_yaw = self._ros.get_pose().yaw
        start = time.time()
        tick = 0.2
        timeout = target_angle / 15.0 + 10.0  # 保守超时

        while True:
            if self._cancelled:
                self._ros.stop_robot()
                return await self._fail("动作被取消")

            pose = self._ros.get_pose()
            diff = abs(self._angle_diff(pose.yaw, start_yaw))
            progress = min(diff / target_angle, 1.0)
            self._current.progress = progress

            elapsed = time.time() - start
            if diff >= target_angle or elapsed >= timeout:
                self._ros.stop_robot()
                self._current.status = "completed"
                self._current.progress = 1.0
                self._current.detail = f"{desc} 完成，实际转向 {diff:.1f}°"
                await self._emit("completed", 1.0, f"{desc} 完成，实际转向 {diff:.1f}°")
                return self._current

            await self._emit("progress", progress, f"{desc} 已转向 {diff:.1f}° / {target_angle:.1f}°")
            await asyncio.sleep(tick)

    @staticmethod
    def _angle_diff(a: float, b: float) -> float:
        diff = (a - b + 180) % 360 - 180
        return diff

    async def _fail(self, reason: str) -> ActionExecution:
        self._current.status = "failed"
        self._current.detail = reason
        await self._emit("failed", self._current.progress, reason)
        return self._current

    async def _emit(self, event: str, progress: float, detail: str):
        if self._event_callback:
            pose = self._ros.get_pose()
            await self._event_callback({
                "type": "action_event",
                "task_id": self._current.task_id,
                "action_id": self._current.action_id,
                "action_type": self._current.action_type,
                "event": event,
                "progress": round(progress, 3),
                "position": pose.to_dict(),
                "orientation": pose.orientation_dict(),
                "detail": detail,
                "timestamp": time.time()
            })

    def _estimate_duration(self, action_type: str, params: dict) -> float:
        if action_type == "walk_straight":
            d, s = params.get("distance", 2.0), params.get("speed", 0.8)
            return d / s if s > 0 else 2.0
        elif action_type == "turn_in_place":
            a, s = params.get("angle", 45.0), params.get("speed", 0.3)
            return a / (s * 60) if s > 0 else 1.0
        elif action_type == "turn_walk":
            d, s = params.get("distance", 1.0), params.get("speed", 0.6)
            return d / s if s > 0 else 2.0
        elif action_type == "walk_backward":
            d, s = params.get("distance", 1.0), params.get("speed", 0.5)
            return d / s if s > 0 else 2.0
        elif action_type == "sidestep":
            d, s = params.get("distance", 0.5), params.get("speed", 0.3)
            return (d / s if s > 0 else 2.0) + 4.0
        elif action_type == "avoid_obstacle":
            return params.get("duration", 5.0)
        elif action_type == "stop":
            return 0.5
        return 2.0

    def set_event_callback(self, callback):
        self._event_callback = callback

    async def cancel(self):
        async with self._lock:
            self._cancelled = True

    def get_current(self) -> Optional[ActionExecution]:
        return self._current


# ══════════════════════════════════════════════════════════════
# GazeboBridge 主类（TCP Server + ROS 桥接）
# ══════════════════════════════════════════════════════════════

class GazeboBridge:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._seq = 0
        self._clients: Set[asyncio.StreamWriter] = set()

        # ROS 接口
        self._ros = ROSInterface(ODOM_TOPIC, CMD_TOPIC)
        # 动作执行器
        self._executor = ActionExecutor(self._ros)
        self._executor.set_event_callback(self._on_action_event)

        self._current_task_id: Optional[str] = None
        self._current_action_id: Optional[int] = None
        self._execution_task: Optional[asyncio.Task] = None

        # 视觉帧转发（可选，通过 WebSocket 连接 vision_server 或直接发给后端）
        self._vision_ws = None
        self._vision_loop_task: Optional[asyncio.Task] = None

    async def run(self):
        # 先启动 ROS 节点（在独立线程中）
        ros_thread = threading.Thread(target=self._run_ros, daemon=True)
        ros_thread.start()
        # 等待 ROS 初始化完成
        await asyncio.sleep(2.0)
        print("[GazeboBridge] ROS 节点已启动")

        # 启动 TCP 服务器
        server = await asyncio.start_server(self._handle_client, self.host, self.port)
        addr = server.sockets[0].getsockname()
        print(f"[GazeboBridge] TCP 服务器已启动: {addr[0]}:{addr[1]}")
        print(f"[GazeboBridge] 等待后端连接...")

        # 启动状态广播循环
        status_task = asyncio.create_task(self._status_loop())
        # 启动视觉帧转发循环
        if ENABLE_CAMERA:
            self._vision_loop_task = asyncio.create_task(self._vision_loop())

        # 信号处理
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

        try:
            async with server:
                serve_task = asyncio.create_task(server.serve_forever())
                stop_task = asyncio.create_task(stop_event.wait())
                await asyncio.wait([serve_task, stop_task], return_when=asyncio.FIRST_COMPLETED)
                serve_task.cancel()
                try:
                    await serve_task
                except asyncio.CancelledError:
                    pass
        finally:
            status_task.cancel()
            if self._vision_loop_task:
                self._vision_loop_task.cancel()
            self._ros.stop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)

    def _run_ros(self):
        """在独立线程中运行 ROS"""
        try:
            self._ros.start()
            rospy.spin()
        except Exception as e:
            print(f"[GazeboBridge] ROS 错误: {e}")

    # ── 状态广播 ──

    async def _status_loop(self):
        """定期广播状态给所有已连接后端"""
        await asyncio.sleep(1.0)  # 等待 ROS 初始化
        while True:
            await asyncio.sleep(STATUS_INTERVAL)
            if not self._clients:
                continue

            pose = self._ros.get_pose()
            current_action = self._executor.get_current()

            action_info = None
            if current_action:
                action_info = {
                    "task_id": current_action.task_id,
                    "action_id": current_action.action_id,
                    "action_type": current_action.action_type,
                    "progress": round(current_action.progress, 3)
                }

            # 状态机判断
            if current_action:
                state = "moving" if current_action.action_type in (
                    "walk_straight", "turn_walk", "walk_backward", "sidestep"
                ) else "avoiding" if current_action.action_type == "avoid_obstacle" else "moving"
            elif abs(pose.vx) > 0.01 or abs(pose.vth) > 0.01:
                state = "avoiding"  # d435i 控制中
            else:
                state = "idle"

            status = {
                "type": "status",
                "status": {
                    "state": state,
                    "battery": 85.0,
                    "position": pose.to_dict(),
                    "orientation": pose.orientation_dict(),
                    "velocity": round(pose.vx, 3),
                    "error_code": 0,
                    "cpu": 15.0,
                    "obstacle_dist": None,
                    "joints": {},
                    "connected": True,
                    "current_action": action_info,
                    "timestamp": time.time()
                }
            }

            await self._broadcast(status)

    # ── 视觉帧转发 ──

    async def _vision_loop(self):
        """将 Gazebo 相机帧通过 WebSocket 转发给后端"""
        import websockets
        while True:
            try:
                async with websockets.connect(VISION_WS_URL, ping_interval=None) as ws:
                    print(f"[GazeboBridge] 视觉帧 WebSocket 已连接: {VISION_WS_URL}")
                    self._vision_ws = ws
                    # 发送注册消息
                    await ws.send(json.dumps({"type": "register", "role": "gazebo_vision"}))
                    # 定期发送帧
                    while True:
                        await asyncio.sleep(0.1)  # 10fps
                        if self._ros.latest_color_frame:
                            await ws.send(json.dumps({
                                "type": "vision_frame",
                                "frame": self._ros.latest_color_frame,
                                "timestamp": time.time()
                            }))
            except Exception as e:
                print(f"[GazeboBridge] 视觉帧连接断开: {e}，5s 后重连...")
                self._vision_ws = None
                await asyncio.sleep(5)

    # ── 客户端连接处理 ──

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        print(f"[GazeboBridge] 后端已连接: {peer}")
        self._clients.add(writer)
        try:
            while True:
                prefix = await reader.readexactly(4)
                length = struct.unpack(">I", prefix)[0]
                body = await reader.readexactly(length)
                await self._process_message(body, writer)
        except asyncio.IncompleteReadError:
            print(f"[GazeboBridge] 后端断开: {peer}")
        except Exception as e:
            print(f"[GazeboBridge] 客户端异常: {e}")
        finally:
            self._clients.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _process_message(self, body: bytes, writer: asyncio.StreamWriter):
        try:
            msg = json.loads(body.decode("utf-8"))
        except Exception as e:
            print(f"[GazeboBridge] JSON 解析失败: {e}")
            return

        mtype = msg.get("type", "")

        if mtype == "heartbeat":
            await self._send(writer, {
                "type": "heartbeat",
                "seq": msg.get("seq", 0),
                "timestamp": time.time()
            })
            return

        if mtype == "low_level_control":
            # d435i 避障的实时控制指令
            steer = msg.get("steer", 0.0)  # deg/s
            speed = msg.get("speed", 0.0)  # m/s
            source = msg.get("source", "unknown")
            # 如果不在执行高层动作，则直接应用（跟真机一致）
            if not self._executor.get_current():
                angular_z = math.radians(steer) if steer is not None else 0.0
                self._ros.send_velocity(speed, angular_z)
            print(f"[GazeboBridge] d435i 控制 | source={source} steer={steer:.2f}°/s speed={speed:.3f}m/s")
            return

        if mtype == "command":
            action = msg.get("action", "")
            params = msg.get("params", {})
            seq = msg.get("seq", 0)
            task_id = msg.get("task_id", "manual")
            action_id = msg.get("action_id", 0)
            print(f"[GazeboBridge] 任务指令 | seq={seq} task={task_id} action={action_id} type={action} params={params}")

            # 取消当前动作
            if self._execution_task and not self._execution_task.done():
                await self._executor.cancel()
                self._execution_task.cancel()
                try:
                    await self._execution_task
                except asyncio.CancelledError:
                    pass

            self._current_task_id = task_id
            self._current_action_id = action_id
            self._execution_task = asyncio.create_task(
                self._execute_action_async(task_id, action_id, action, params)
            )
            return

        if mtype == "emergency_stop":
            print("[GazeboBridge] ⚠️ 紧急停止")
            if self._execution_task and not self._execution_task.done():
                await self._executor.cancel()
                self._execution_task.cancel()
                try:
                    await self._execution_task
                except asyncio.CancelledError:
                    pass
            self._ros.stop_robot()
            self._current_task_id = None
            self._current_action_id = None
            return

        print(f"[GazeboBridge] 未知消息类型: {mtype}")

    async def _execute_action_async(self, task_id: str, action_id: int,
                                     action_type: str, params: dict):
        try:
            result = await self._executor.execute(task_id, action_id, action_type, params)
            detail = getattr(result, 'detail', '') or getattr(result, 'status', 'unknown')
            print(f"[GazeboBridge] 动作结果: {result.status} | {detail}")
        except Exception as e:
            print(f"[GazeboBridge] 动作异常: {e}")
        finally:
            self._current_task_id = None
            self._current_action_id = None

    async def _on_action_event(self, event_msg: dict):
        """转发 action_event 给所有后端"""
        await self._broadcast(event_msg)
        event = event_msg.get("event", "")
        detail = event_msg.get("detail", "")
        pos = event_msg.get("position", {})
        x, y = pos.get("x", 0), pos.get("y", 0)
        print(f"[GazeboBridge] action_event: {event} | {detail} | pos=({x:.2f}, {y:.2f})")

    async def _broadcast(self, msg: dict):
        dead = []
        data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        prefix = struct.pack(">I", len(data))
        for writer in list(self._clients):
            try:
                writer.write(prefix + data)
                await writer.drain()
            except Exception:
                dead.append(writer)
        for writer in dead:
            self._clients.discard(writer)

    async def _send(self, writer: asyncio.StreamWriter, msg: dict):
        data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        writer.write(struct.pack(">I", len(data)) + data)
        await writer.drain()


# ══════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════

def main():
    # 检查 ROS 环境
    if "ROS_DISTRO" not in os.environ:
        print("⚠️  警告: 未检测到 ROS 环境。请先运行:")
        print("    source /opt/ros/noetic/setup.bash")
        print("")

    bridge = GazeboBridge(ROBOT_HOST, ROBOT_PORT)
    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        pass
    print("[GazeboBridge] 已退出")


if __name__ == "__main__":
    main()
