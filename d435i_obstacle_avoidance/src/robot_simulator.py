#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机器人底层 TCP 模拟器（含 2D SLAM 导航模拟）

用法（跑在带 D435i 的电脑上，模拟机器人底层）：
    python src/robot_simulator.py

环境变量：
    ROBOT_HOST  - 监听地址，默认 0.0.0.0
    ROBOT_PORT  - 监听端口，默认 9090

通信协议：与后端对齐
    4 字节大端长度前缀 + JSON Body
    接收：low_level_control / command / heartbeat / emergency_stop / nav_task
    发送：status / heartbeat / nav_position_update / avoidance_event

2D SLAM 导航模拟功能：
    - 接收 nav_task（起点/终点 2D 坐标）
    - 每秒回传收敛型 2D 坐标 (x, y)
    - 随机触发障碍物检测 → 执行避障动作 → 回传新坐标
    - 线性收敛：current → target，欧氏距离持续减小
"""

import asyncio
import json
import math
import os
import random
import signal
import struct
import time


ROBOT_HOST = os.getenv("ROBOT_HOST", "0.0.0.0")
ROBOT_PORT = int(os.getenv("ROBOT_PORT", "9090"))
STATUS_INTERVAL = 1.0  # 主动上报状态周期 (s)

# 2D 导航模拟参数
NAV_SPEED = float(os.getenv("NAV_SPEED", "1.0"))         # 导航速度 (m/s)
OBSTACLE_CHANCE = float(os.getenv("OBSTACLE_CHANCE", "0.15"))  # 每秒障碍物触发概率
OBSTACLE_TURN = float(os.getenv("OBSTACLE_TURN", "45.0"))      # 避障左转角度 (度)
OBSTACLE_FORWARD = float(os.getenv("OBSTACLE_FORWARD", "2.0")) # 避障前进距离 (m)


class RobotSimulator:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._seq = 0
        self._state = "idle"
        self._speed = 0.0
        self._steer = 0.0
        self._clients = set()

        # ── 2D 导航状态 ──
        self._nav_active = False
        self._nav_task_name = ""
        self._start_x = 0.0
        self._start_y = 0.0
        self._target_x = 0.0
        self._target_y = 0.0
        self._current_x = 0.0
        self._current_y = 0.0
        self._current_yaw = 0.0
        self._nav_step = 0
        self._avoiding = False
        self._avoid_cooldown = 0  # 避障冷却步数

    async def run(self):
        server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        addr = server.sockets[0].getsockname()
        print(f"[RobotSimulator] 机器人底层模拟器已启动: {addr[0]}:{addr[1]}", flush=True)
        print(f"[RobotSimulator] 等待后端 TCP 连接...", flush=True)
        print(f"[RobotSimulator] 2D 导航模拟: 速度={NAV_SPEED}m/s 避障概率={OBSTACLE_CHANCE}", flush=True)

        # 启动状态广播任务
        status_task = asyncio.create_task(self._status_loop())
        # 启动 2D 导航循环任务
        nav_task = asyncio.create_task(self._nav_loop())

        # 注册信号处理（Windows 不支持 add_signal_handler，降级处理）
        stop_event = asyncio.Event()

        def _request_stop():
            print("\n[RobotSimulator] 收到终止信号", flush=True)
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal.SIG_DFL)
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            print("[RobotSimulator] 信号处理器不可用 (Windows), Ctrl+C 降级模式", flush=True)

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
        except KeyboardInterrupt:
            pass
        finally:
            status_task.cancel()
            nav_task.cancel()
            try:
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.remove_signal_handler(sig)
            except (NotImplementedError, RuntimeError):
                pass

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        print(f"[RobotSimulator] 后端已连接: {peer}", flush=True)
        self._clients.add(writer)
        try:
            while True:
                # 读取 4 字节长度前缀
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
            self._steer = msg.get("steer", 0.0)
            self._speed = msg.get("speed", 0.0)
            source = msg.get("source", "unknown")
            print(f"[RobotSimulator] 收到 d435i 控制指令 | source={source} steer={self._steer:.2f} speed={self._speed:.3f}", flush=True)
            self._state = "avoiding" if abs(self._speed) > 0.001 or abs(self._steer) > 0.001 else "idle"
            return

        if mtype == "command":
            action = msg.get("action", "")
            params = msg.get("params", {})
            seq = msg.get("seq", 0)
            print(f"[RobotSimulator] 收到任务指令 | seq={seq} action={action} params={params}", flush=True)
            await self._send(writer, {
                "type": "task_event",
                "task_id": msg.get("task_id", 0),
                "step_id": msg.get("step_id", 0),
                "status": "RUNNING",
                "detail": f"executing {action}"
            })
            self._state = "moving"
            return

        if mtype == "emergency_stop":
            print("[RobotSimulator] ⚠️ 收到紧急停止", flush=True)
            self._state = "stopped"
            self._speed = 0.0
            self._steer = 0.0
            self._nav_active = False
            return

        # ── 2D SLAM 导航任务 ──
        if mtype == "nav_task":
            await self._start_navigation(msg, writer)
            return

        print(f"[RobotSimulator] 未知消息类型: {mtype} | {msg}", flush=True)

    # ═══════════════════════════════════════════════════════════════
    # 2D SLAM 导航模拟
    # ═══════════════════════════════════════════════════════════════

    async def _start_navigation(self, msg: dict, writer: asyncio.StreamWriter):
        """启动 2D 导航任务"""
        self._nav_active = True
        self._nav_task_name = msg.get("task_name", "导航任务")
        self._start_x = float(msg.get("start_x", 0.0))
        self._start_y = float(msg.get("start_y", 0.0))
        self._target_x = float(msg.get("target_x", 0.0))
        self._target_y = float(msg.get("target_y", 0.0))
        self._current_x = self._start_x
        self._current_y = self._start_y
        self._current_yaw = float(msg.get("initial_yaw", 0.0))
        self._nav_step = 0
        self._avoiding = False
        self._avoid_cooldown = 0
        self._state = "navigating"

        dist = math.hypot(self._target_x - self._start_x, self._target_y - self._start_y)
        print(f"[RobotSimulator] 📍 开始导航: {self._nav_task_name}", flush=True)
        print(f"[RobotSimulator]    起点=({self._start_x:.2f}, {self._start_y:.2f}) → "
              f"终点=({self._target_x:.2f}, {self._target_y:.2f}) 距离={dist:.2f}m", flush=True)

        # 立即回传起点位置
        await self._send_nav_update(writer)

    async def _nav_loop(self):
        """2D 导航主循环：每秒回传收敛坐标"""
        while True:
            await asyncio.sleep(STATUS_INTERVAL)
            if not self._nav_active or not self._clients:
                continue

            self._nav_step += 1

            # 检查是否到达
            dist = math.hypot(self._target_x - self._current_x, self._target_y - self._current_y)
            if dist < 0.05:
                # 已到达目标
                self._current_x = self._target_x
                self._current_y = self._target_y
                self._nav_active = False
                self._state = "idle"
                for writer in list(self._clients):
                    try:
                        await self._send_nav_update(writer)
                        await self._send(writer, {
                            "type": "nav_task_completed",
                            "task_name": self._nav_task_name,
                            "final_x": self._current_x,
                            "final_y": self._current_y,
                            "final_yaw": self._current_yaw,
                            "total_steps": self._nav_step,
                        })
                    except Exception:
                        self._clients.discard(writer)
                print(f"[RobotSimulator] ✅ 导航完成: {self._nav_task_name} (共 {self._nav_step} 步)", flush=True)
                continue

            # ── 避障冷却 ──
            if self._avoid_cooldown > 0:
                self._avoid_cooldown -= 1

            # ── 随机触发障碍物检测 ──
            if not self._avoiding and self._avoid_cooldown <= 0 and dist > 2.0:
                if random.random() < OBSTACLE_CHANCE:
                    await self._trigger_avoidance(dist)
                    continue  # 避障步骤会单独广播

            # ── 正常行进：向目标线性收敛 ──
            if not self._avoiding:
                # 计算本步移动距离
                step_dist = min(NAV_SPEED * STATUS_INTERVAL, dist)
                dx = self._target_x - self._current_x
                dy = self._target_y - self._current_y
                # 更新坐标
                self._current_x += (dx / dist) * step_dist
                self._current_y += (dy / dist) * step_dist
                # 更新航向角
                self._current_yaw = math.degrees(math.atan2(dy, dx)) % 360
                self._state = "navigating"

            # 广播当前坐标给所有连接的后端
            dead = []
            for writer in list(self._clients):
                try:
                    await self._send_nav_update(writer)
                except Exception:
                    dead.append(writer)
            for writer in dead:
                self._clients.discard(writer)

    async def _trigger_avoidance(self, distance_to_target: float):
        """触发避障动作"""
        self._avoiding = True
        self._state = "avoiding"
        trigger_x = self._current_x
        trigger_y = self._current_y
        trigger_yaw = self._current_yaw

        print(f"[RobotSimulator] ⚠️ 检测到障碍物 | "
              f"位置=({trigger_x:.2f}, {trigger_y:.2f}) 距终点={distance_to_target:.2f}m", flush=True)

        # 1. 左转 OBSTACLE_TURN 度
        new_yaw = (self._current_yaw + OBSTACLE_TURN) % 360

        # 2. 沿新航向前进 OBSTACLE_FORWARD 米
        rad = math.radians(new_yaw)
        new_x = self._current_x + OBSTACLE_FORWARD * math.cos(rad)
        new_y = self._current_y + OBSTACLE_FORWARD * math.sin(rad)

        # 3. 避障后修正航向，重新指向目标
        dx = self._target_x - new_x
        dy = self._target_y - new_y
        corrected_yaw = math.degrees(math.atan2(dy, dx)) % 360

        # 4. 更新当前位置
        self._current_x = new_x
        self._current_y = new_y
        self._current_yaw = corrected_yaw

        # 5. 计算剩余距离
        remaining = math.hypot(self._target_x - new_x, self._target_y - new_y)

        # 6. 广播避障事件
        avoidance_data = {
            "type": "avoidance_event",
            "task_name": self._nav_task_name,
            "index": 0,  # 由后端 SLAM 协调器维护计数
            "trigger_x": round(trigger_x, 3),
            "trigger_y": round(trigger_y, 3),
            "turn_angle": OBSTACLE_TURN,
            "forward_distance": OBSTACLE_FORWARD,
            "new_x": round(new_x, 3),
            "new_y": round(new_y, 3),
            "new_yaw": round(new_yaw, 1),
            "corrected_yaw": round(corrected_yaw, 1),
            "remaining_distance": round(remaining, 3),
            "timestamp": time.time(),
        }

        dead = []
        for writer in list(self._clients):
            try:
                await self._send(writer, avoidance_data)
                # 同时也发送位置更新
                await self._send_nav_update(writer)
            except Exception:
                dead.append(writer)
        for writer in dead:
            self._clients.discard(writer)

        print(f"[RobotSimulator]    避障完成: 新位置=({new_x:.2f}, {new_y:.2f}) "
              f"剩余距离={remaining:.2f}m", flush=True)

        # 避障后冷却 2 步（避免连续触发）
        self._avoid_cooldown = 2
        self._avoiding = False
        self._state = "navigating"

    async def _send_nav_update(self, writer: asyncio.StreamWriter):
        """发送 2D 导航位置更新"""
        dist = math.hypot(self._target_x - self._current_x, self._target_y - self._current_y)
        await self._send(writer, {
            "type": "nav_position_update",
            "task_name": self._nav_task_name,
            "current_x": round(self._current_x, 3),
            "current_y": round(self._current_y, 3),
            "yaw": round(self._current_yaw, 1),
            "distance_to_target": round(dist, 3),
            "status": self._state,
            "step": self._nav_step,
            "timestamp": time.time(),
        })

    # ═══════════════════════════════════════════════════════════════
    # 状态广播
    # ═══════════════════════════════════════════════════════════════

    async def _status_loop(self):
        """定期广播模拟状态给所有已连接后端"""
        while True:
            await asyncio.sleep(STATUS_INTERVAL)
            if not self._clients:
                continue
            status = {
                "type": "status",
                "status": {
                    "state": self._state,
                    "battery": 85.0,
                    "position": {"x": self._current_x, "y": self._current_y, "z": 0.0},
                    "orientation": {"roll": 0.0, "pitch": 0.0, "yaw": self._current_yaw},
                    "velocity": self._speed,
                    "error_code": 0,
                    "cpu": 15.0,
                    "obstacle_dist": None,
                    "joints": {},
                    "connected": True,
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

    async def _send(self, writer: asyncio.StreamWriter, msg: dict):
        data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        writer.write(struct.pack(">I", len(data)) + data)
        await writer.drain()


def main():
    sim = RobotSimulator(ROBOT_HOST, ROBOT_PORT)
    try:
        asyncio.run(sim.run())
    except KeyboardInterrupt:
        pass
    print("[RobotSimulator] 已退出", flush=True)


if __name__ == "__main__":
    main()
