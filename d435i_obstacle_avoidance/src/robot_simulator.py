#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机器人底层 TCP 模拟器（v2.0 — 闭环任务调度版）

功能：
  1. 物理运动模型：根据速度/转向角实时计算坐标位置
  2. 动作执行引擎：解析高层动作，模拟执行过程，回传进度
  3. 坐标回传：实时上报 (x, y, yaw) 给后端
  4. 任务闭环：动作开始→进度→完成/失败，全生命周期回传

通信协议：4 字节大端长度前缀 + JSON Body
  接收：command / low_level_control / heartbeat / emergency_stop
  发送：status / action_event / heartbeat

用法：
    python src/robot_simulator.py

环境变量：
    ROBOT_HOST  - 监听地址，默认 0.0.0.0
    ROBOT_PORT  - 监听端口，默认 9090
"""

import asyncio
import json
import math
import os
import signal
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

ROBOT_HOST = os.getenv("ROBOT_HOST", "0.0.0.0")
ROBOT_PORT = int(os.getenv("ROBOT_PORT", "9090"))
STATUS_INTERVAL = 0.5  # 状态上报周期 (s) — 更频繁以支持实时坐标


# ══════════════════════════════════════════════════════════════
# 物理运动模型
# ══════════════════════════════════════════════════════════════

@dataclass
class Pose:
    """位姿：位置 + 朝向"""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0  # 度，0 = 正X方向，逆时针增加

    def copy(self) -> "Pose":
        return Pose(self.x, self.y, self.z, self.yaw)

    def to_dict(self) -> dict:
        return {"x": round(self.x, 4), "y": round(self.y, 4), "z": round(self.z, 4)}

    def orientation_dict(self) -> dict:
        return {"roll": 0.0, "pitch": 0.0, "yaw": round(self.yaw, 2)}


class PhysicsEngine:
    """2D 物理运动模型：根据速度/转向角更新位姿"""

    def __init__(self, pose: Optional[Pose] = None):
        self.pose = pose or Pose()
        self.speed: float = 0.0       # 线速度 (m/s)，正向前，负向后
        self.steer: float = 0.0       # 转向角速度 (度/s)
        self._lock = asyncio.Lock()

    async def update(self, dt: float) -> Pose:
        """根据当前速度和转向角，更新 dt 秒后的位姿"""
        async with self._lock:
            yaw_rad = math.radians(self.pose.yaw)
            # 线速度更新位置
            self.pose.x += self.speed * math.cos(yaw_rad) * dt
            self.pose.y += self.speed * math.sin(yaw_rad) * dt
            # 转向角速度更新朝向
            self.pose.yaw += self.steer * dt
            # 规范化 yaw 到 [-180, 180]
            while self.pose.yaw > 180:
                self.pose.yaw -= 360
            while self.pose.yaw < -180:
                self.pose.yaw += 360
            return self.pose.copy()

    async def set_velocity(self, speed: float, steer: float = 0.0):
        async with self._lock:
            self.speed = speed
            self.steer = steer

    async def get_pose(self) -> Pose:
        async with self._lock:
            return self.pose.copy()

    def get_velocity(self) -> Tuple[float, float]:
        return self.speed, self.steer


# ══════════════════════════════════════════════════════════════
# 动作执行引擎
# ══════════════════════════════════════════════════════════════

@dataclass
class ActionExecution:
    """动作执行上下文"""
    task_id: str
    action_id: int
    action_type: str
    params: dict
    start_time: float
    estimated_duration: float
    status: str = "running"  # running, completed, failed
    progress: float = 0.0    # 0.0 ~ 1.0
    detail: str = ""         # 状态描述


class ActionExecutor:
    """动作执行引擎：解析动作参数，驱动物理模型，模拟执行过程"""

    # 动作参数映射：action_type -> 默认参数
    DEFAULT_PARAMS = {
        "walk_straight": {"distance": 2.0, "speed": 0.8},
        "turn_in_place": {"angle": 45.0, "speed": 0.3, "direction": "left"},
        "turn_walk": {"distance": 1.0, "angle": 45.0, "speed": 0.6},
        "walk_backward": {"distance": 1.0, "speed": 0.5},
        "sidestep": {"distance": 0.5, "speed": 0.3, "direction": "left"},
        "stop": {"emergency": False},
        "avoid_obstacle": {"duration": 5.0},
    }

    def __init__(self, physics: PhysicsEngine):
        self._physics = physics
        self._current: Optional[ActionExecution] = None
        self._lock = asyncio.Lock()
        self._cancelled = False

    async def execute(self, task_id: str, action_id: int, action_type: str,
                      params: dict) -> ActionExecution:
        """执行一个动作，返回执行结果。这是阻塞调用。"""
        merged = {**self.DEFAULT_PARAMS.get(action_type, {}), **params}

        async with self._lock:
            self._cancelled = False
            self._current = ActionExecution(
                task_id=task_id,
                action_id=action_id,
                action_type=action_type,
                params=merged,
                start_time=time.time(),
                estimated_duration=self._estimate_duration(action_type, merged),
                detail="动作开始"
            )

        # 发送 "started" 事件
        await self._emit("started", 0.0, "动作开始")

        # 执行动作
        result = await self._run_action(action_type, merged)

        async with self._lock:
            self._current = None

        return result

    async def _run_action(self, action_type: str, params: dict) -> ActionExecution:
        """根据动作类型执行相应的物理模拟"""
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

    # ── 各动作具体实现 ──

    async def _exec_walk_straight(self, params: dict) -> ActionExecution:
        distance = params.get("distance", 2.0)
        speed = abs(params.get("speed", 0.8))
        if distance <= 0 or speed <= 0:
            return await self._fail("距离或速度参数无效")

        await self._physics.set_velocity(speed, 0.0)
        duration = distance / speed
        return await self._wait_for_distance(distance, duration, "直线行走")

    async def _exec_turn_in_place(self, params: dict) -> ActionExecution:
        angle = abs(params.get("angle", 45.0))
        direction = params.get("direction", "left")
        angular_speed = abs(params.get("speed", 0.3)) * 60  # 0.3 → 18 deg/s

        # 修复：left = 逆时针 = yaw 增加，right = 顺时针 = yaw 减小
        if direction == "left":
            steer = angular_speed
        else:
            steer = -angular_speed

        await self._physics.set_velocity(0.0, steer)
        duration = angle / abs(steer) if steer != 0 else 0
        return await self._wait_for_angle(angle, duration, f"原地转向 {direction}")

    async def _exec_turn_walk(self, params: dict) -> ActionExecution:
        distance = params.get("distance", 1.0)
        angle = params.get("angle", 45.0)
        speed = abs(params.get("speed", 0.6))
        duration = distance / speed if speed > 0 else 0

        # 边转边走：设置一个持续的转向角速度
        steer_rate = angle / duration if duration > 0 else 0
        await self._physics.set_velocity(speed, steer_rate)
        return await self._wait_for_duration(duration, "转弯行走")

    async def _exec_walk_backward(self, params: dict) -> ActionExecution:
        distance = params.get("distance", 1.0)
        speed = abs(params.get("speed", 0.5))
        await self._physics.set_velocity(-speed, 0.0)
        duration = distance / speed
        return await self._wait_for_distance(distance, duration, "后退")

    async def _exec_sidestep(self, params: dict) -> ActionExecution:
        distance = params.get("distance", 0.5)
        speed = abs(params.get("speed", 0.3))
        direction = params.get("direction", "left")
        # 侧移：先转向90度，走distance，再转回来
        turn_angle = 90 if direction == "left" else -90

        # 第一阶段：转向
        await self._physics.set_velocity(0.0, turn_angle * 0.5)
        await self._wait_for_duration(2.0, f"侧移准备转向")

        # 第二阶段：行走
        await self._physics.set_velocity(speed, 0.0)
        await self._wait_for_duration(distance / speed, f"侧移")

        # 第三阶段：转回
        await self._physics.set_velocity(0.0, -turn_angle * 0.5)
        return await self._wait_for_duration(2.0, f"侧移复位")

    async def _exec_stop(self, params: dict) -> ActionExecution:
        await self._physics.set_velocity(0.0, 0.0)
        self._current.status = "completed"
        self._current.progress = 1.0
        self._current.detail = "已停止"
        await self._emit("completed", 1.0, "已停止")
        return self._current

    async def _exec_avoid_obstacle(self, params: dict) -> ActionExecution:
        duration = params.get("duration", 5.0)
        # 模拟避障：随机转向+行走
        import random
        steer = random.uniform(-30, 30)
        await self._physics.set_velocity(0.4, steer)
        return await self._wait_for_duration(duration, "视觉避障模式")

    async def _exec_unknown(self, action_type: str, params: dict) -> ActionExecution:
        await self._physics.set_velocity(0.0, 0.0)
        return await self._fail(f"未知动作类型: {action_type}")

    # ── 通用等待工具 ──

    async def _wait_for_duration(self, duration: float, desc: str) -> ActionExecution:
        """按时间等待，期间定期上报进度"""
        start = time.time()
        tick_interval = 0.2  # 200ms 更新一次进度

        while time.time() - start < duration:
            if self._cancelled:
                await self._physics.set_velocity(0.0, 0.0)
                return await self._fail("动作被取消")

            elapsed = time.time() - start
            progress = min(elapsed / duration, 1.0)
            self._current.progress = progress
            await self._emit("progress", progress, f"{desc} 进行中 ({elapsed:.1f}s / {duration:.1f}s)")
            await asyncio.sleep(tick_interval)

        await self._physics.set_velocity(0.0, 0.0)
        self._current.status = "completed"
        self._current.progress = 1.0
        self._current.detail = f"{desc} 完成"
        await self._emit("completed", 1.0, f"{desc} 完成")
        return self._current

    async def _wait_for_distance(self, target_dist: float, duration: float,
                                  desc: str) -> ActionExecution:
        """按距离等待，期间定期上报进度"""
        start_pose = await self._physics.get_pose()
        start = time.time()
        tick_interval = 0.2

        while True:
            if self._cancelled:
                await self._physics.set_velocity(0.0, 0.0)
                return await self._fail("动作被取消")

            pose = await self._physics.get_pose()
            travelled = math.hypot(pose.x - start_pose.x, pose.y - start_pose.y)
            progress = min(travelled / target_dist, 1.0)
            self._current.progress = progress

            # 检查是否完成（按距离或超时）
            elapsed = time.time() - start
            if travelled >= target_dist or elapsed >= duration * 1.5:
                await self._physics.set_velocity(0.0, 0.0)
                self._current.status = "completed"
                self._current.progress = 1.0
                self._current.detail = f"{desc} 完成，实际行走 {travelled:.2f}m"
                await self._emit("completed", 1.0, f"{desc} 完成，实际行走 {travelled:.2f}m")
                return self._current

            await self._emit("progress", progress,
                             f"{desc} 已行走 {travelled:.2f}m / {target_dist:.2f}m")
            await asyncio.sleep(tick_interval)

    async def _wait_for_angle(self, target_angle: float, duration: float,
                               desc: str) -> ActionExecution:
        """按角度等待"""
        start_yaw = (await self._physics.get_pose()).yaw
        start = time.time()
        tick_interval = 0.2

        while True:
            if self._cancelled:
                await self._physics.set_velocity(0.0, 0.0)
                return await self._fail("动作被取消")

            pose = await self._physics.get_pose()
            # 计算角度差（处理 wrap-around）
            diff = abs(self._angle_diff(pose.yaw, start_yaw))
            progress = min(diff / target_angle, 1.0)
            self._current.progress = progress

            elapsed = time.time() - start
            if diff >= target_angle or elapsed >= duration * 1.5:
                await self._physics.set_velocity(0.0, 0.0)
                self._current.status = "completed"
                self._current.progress = 1.0
                self._current.detail = f"{desc} 完成，实际转向 {diff:.1f}°"
                await self._emit("completed", 1.0, f"{desc} 完成，实际转向 {diff:.1f}°")
                return self._current

            await self._emit("progress", progress,
                             f"{desc} 已转向 {diff:.1f}° / {target_angle:.1f}°")
            await asyncio.sleep(tick_interval)

    @staticmethod
    def _angle_diff(a: float, b: float) -> float:
        """计算两个角度之间的最小差值"""
        diff = (a - b + 180) % 360 - 180
        return diff

    async def _fail(self, reason: str) -> ActionExecution:
        self._current.status = "failed"
        self._current.detail = reason
        await self._emit("failed", self._current.progress, reason)
        return self._current

    async def _emit(self, event: str, progress: float, detail: str):
        """发送 action_event 消息（通过回调）"""
        if self._event_callback:
            pose = await self._physics.get_pose()
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
        """预估动作执行时间"""
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
            return (d / s if s > 0 else 2.0) + 4.0  # +转向时间
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
# RobotSimulator 主类
# ══════════════════════════════════════════════════════════════

class RobotSimulator:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._seq = 0
        self._clients: set = set()

        # 物理引擎
        self._physics = PhysicsEngine(pose=Pose(x=0.0, y=0.0, z=0.0, yaw=0.0))
        # 动作执行器
        self._executor = ActionExecutor(self._physics)
        self._executor.set_event_callback(self._on_action_event)
        # 当前执行的任务
        self._current_task_id: Optional[str] = None
        self._current_action_id: Optional[int] = None
        self._execution_task: Optional[asyncio.Task] = None

    async def run(self):
        server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        addr = server.sockets[0].getsockname()
        print(f"[RobotSimulator] 机器人底层模拟器 v2.0 已启动: {addr[0]}:{addr[1]}", flush=True)
        print(f"[RobotSimulator] 等待后端 TCP 连接...", flush=True)

        # 启动物理引擎更新循环
        physics_task = asyncio.create_task(self._physics_loop())
        # 启动状态广播循环
        status_task = asyncio.create_task(self._status_loop())

        # 信号处理
        stop_event = asyncio.Event()

        def _request_stop():
            print("\n[RobotSimulator] 收到终止信号", flush=True)
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal.SIG_DFL)
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _request_stop)

        try:
            async with server:
                serve_task = asyncio.create_task(server.serve_forever())
                stop_task = asyncio.create_task(stop_event.wait())
                await asyncio.wait(
                    [serve_task, stop_task],
                    return_when=asyncio.FIRST_COMPLETED
                )
                serve_task.cancel()
                try:
                    await serve_task
                except asyncio.CancelledError:
                    pass
        finally:
            physics_task.cancel()
            status_task.cancel()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)

    # ── 物理引擎更新 ──

    async def _physics_loop(self):
        """物理引擎更新循环：每 dt 秒更新一次位姿"""
        dt = 0.05  # 50ms 物理步进
        while True:
            await asyncio.sleep(dt)
            await self._physics.update(dt)

    # ── 状态广播 ──

    async def _status_loop(self):
        """定期广播状态给所有已连接后端"""
        while True:
            await asyncio.sleep(STATUS_INTERVAL)
            if not self._clients:
                continue

            pose = await self._physics.get_pose()
            speed, steer = self._physics.get_velocity()

            # 当前动作信息
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
            elif abs(speed) > 0.001 or abs(steer) > 0.001:
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
                    "velocity": round(speed, 3),
                    "error_code": 0,
                    "cpu": 15.0,
                    "obstacle_dist": None,
                    "joints": {},
                    "connected": True,
                    "current_action": action_info,
                    "timestamp": time.time()
                }
            }

            dead = []
            for writer in list(self._clients):
                try:
                    await self._send(writer, status)
                except Exception:
                    dead.append(writer)
            for writer in dead:
                self._clients.discard(writer)

    # ── 客户端连接处理 ──

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        print(f"[RobotSimulator] 后端已连接: {peer}", flush=True)
        self._clients.add(writer)
        try:
            while True:
                prefix = await reader.readexactly(4)
                length = struct.unpack(">I", prefix)[0]
                body = await reader.readexactly(length)
                await self._process_message(body, writer)
        except asyncio.IncompleteReadError:
            print(f"[RobotSimulator] 后端断开: {peer}", flush=True)
        except Exception as e:
            print(f"[RobotSimulator] 客户端异常: {e}", flush=True)
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
            print(f"[RobotSimulator] JSON 解析失败: {e}", flush=True)
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
            steer = msg.get("steer", 0.0)
            speed = msg.get("speed", 0.0)
            source = msg.get("source", "unknown")
            # d435i 控制时，如果不在执行高层动作，则直接应用
            if not self._executor.get_current():
                await self._physics.set_velocity(speed, steer)
            print(f"[RobotSimulator] 收到 d435i 控制 | source={source} steer={steer:.2f} speed={speed:.3f}", flush=True)
            return

        if mtype == "command":
            action = msg.get("action", "")
            params = msg.get("params", {})
            seq = msg.get("seq", 0)
            task_id = msg.get("task_id", "manual")
            action_id = msg.get("action_id", 0)
            print(f"[RobotSimulator] 收到任务指令 | seq={seq} task_id={task_id} action_id={action_id} action={action} params={params}", flush=True)

            # 取消当前正在执行的动作
            if self._execution_task and not self._execution_task.done():
                await self._executor.cancel()
                self._execution_task.cancel()
                try:
                    await self._execution_task
                except asyncio.CancelledError:
                    pass

            # 启动新的动作执行
            self._current_task_id = task_id
            self._current_action_id = action_id
            self._execution_task = asyncio.create_task(
                self._execute_action_async(task_id, action_id, action, params)
            )
            return

        if mtype == "emergency_stop":
            print("[RobotSimulator] ⚠️ 收到紧急停止", flush=True)
            if self._execution_task and not self._execution_task.done():
                await self._executor.cancel()
                self._execution_task.cancel()
                try:
                    await self._execution_task
                except asyncio.CancelledError:
                    pass
            await self._physics.set_velocity(0.0, 0.0)
            self._current_task_id = None
            self._current_action_id = None
            return

        print(f"[RobotSimulator] 未知消息类型: {mtype} | {msg}", flush=True)

    async def _execute_action_async(self, task_id: str, action_id: int,
                                     action_type: str, params: dict):
        """在后台执行动作"""
        try:
            result = await self._executor.execute(task_id, action_id, action_type, params)
            detail = getattr(result, 'detail', '') or getattr(result, 'status', 'unknown')
            print(f"[RobotSimulator] 动作执行结果: {result.status} | {detail}", flush=True)
        except Exception as e:
            print(f"[RobotSimulator] 动作执行异常: {e}", flush=True)
        finally:
            self._current_task_id = None
            self._current_action_id = None

    async def _on_action_event(self, event_msg: dict):
        """动作事件回调：转发给所有后端"""
        dead = []
        for writer in list(self._clients):
            try:
                await self._send(writer, event_msg)
            except Exception:
                dead.append(writer)
        for writer in dead:
            self._clients.discard(writer)

        # 打印到控制台
        event = event_msg.get("event", "")
        detail = event_msg.get("detail", "")
        pos = event_msg.get("position", {})
        x, y = pos.get("x", 0), pos.get("y", 0)
        print(f"[RobotSimulator] action_event: {event} | {detail} | pos=({x:.2f}, {y:.2f})", flush=True)

    async def _send(self, writer: asyncio.StreamWriter, msg: dict):
        data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        writer.write(struct.pack(">I", len(data)) + data)
        await writer.drain()


# ══════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════

def main():
    sim = RobotSimulator(ROBOT_HOST, ROBOT_PORT)
    try:
        asyncio.run(sim.run())
    except KeyboardInterrupt:
        pass
    print("[RobotSimulator] 已退出", flush=True)


if __name__ == "__main__":
    main()
