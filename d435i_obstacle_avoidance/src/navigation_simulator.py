#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2D SLAM 导航模拟器（可独立运行测试）

独立的导航模拟程序，可以不依赖 robot_simulator.py 单独运行。
接收后端通过 TCP 下发的 nav_task，模拟 2D 坐标收敛和避障。

用法：
    python src/navigation_simulator.py

环境变量：
    NAV_HOST        - 监听地址，默认 0.0.0.0
    NAV_PORT        - 监听端口，默认 9090
    NAV_SPEED       - 导航速度 (m/s)，默认 1.0
    OBSTACLE_CHANCE - 每秒障碍物触发概率，默认 0.15
    OBSTACLE_TURN   - 避障左转角度 (度)，默认 45
    OBSTACLE_FORWARD- 避障前进距离 (m)，默认 2.0
"""
import asyncio
import json
import math
import os
import random
import signal
import struct
import time

NAV_HOST = os.getenv("NAV_HOST", "0.0.0.0")
NAV_PORT = int(os.getenv("NAV_PORT", "9090"))
NAV_SPEED = float(os.getenv("NAV_SPEED", "1.0"))
OBSTACLE_CHANCE = float(os.getenv("OBSTACLE_CHANCE", "0.15"))
OBSTACLE_TURN = float(os.getenv("OBSTACLE_TURN", "45.0"))
OBSTACLE_FORWARD = float(os.getenv("OBSTACLE_FORWARD", "2.0"))
STEP_INTERVAL = 1.0  # 坐标回传间隔 (s)


class NavigationSimulator:
    """纯 2D 导航模拟器"""

    def __init__(self):
        self._clients = set()
        self._nav_active = False
        self._task_name = ""
        self._start_x = 0.0
        self._start_y = 0.0
        self._target_x = 0.0
        self._target_y = 0.0
        self._current_x = 0.0
        self._current_y = 0.0
        self._current_yaw = 0.0
        self._step = 0
        self._avoiding = False
        self._avoid_cooldown = 0

    async def run(self):
        server = await asyncio.start_server(
            self._handle_client, NAV_HOST, NAV_PORT
        )
        addr = server.sockets[0].getsockname()
        print(f"[NavSim] 2D 导航模拟器已启动: {addr[0]}:{addr[1]}", flush=True)
        print(f"[NavSim] 速度={NAV_SPEED}m/s 避障概率={OBSTACLE_CHANCE}", flush=True)

        nav_loop = asyncio.create_task(self._nav_loop())

        stop_event = asyncio.Event()

        def _stop():
            print("\n[NavSim] 收到终止信号", flush=True)
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal.SIG_DFL)
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass

        try:
            async with server:
                serve_task = asyncio.create_task(server.serve_forever())
                stop_task = asyncio.create_task(stop_event.wait())
                await asyncio.wait([serve_task, stop_task],
                                   return_when=asyncio.FIRST_COMPLETED)
                serve_task.cancel()
                try:
                    await serve_task
                except asyncio.CancelledError:
                    pass
        finally:
            nav_loop.cancel()

    async def _handle_client(self, reader, writer):
        peer = writer.get_extra_info("peername")
        print(f"[NavSim] 后端已连接: {peer}", flush=True)
        self._clients.add(writer)
        try:
            while True:
                prefix = await reader.readexactly(4)
                length = struct.unpack(">I", prefix)[0]
                body = await reader.readexactly(length)
                await self._process(json.loads(body.decode("utf-8")), writer)
        except asyncio.IncompleteReadError:
            print(f"[NavSim] 后端断开: {peer}", flush=True)
        except Exception as e:
            print(f"[NavSim] 异常: {e}", flush=True)
        finally:
            self._clients.discard(writer)

    async def _process(self, msg, writer):
        mtype = msg.get("type", "")
        if mtype == "heartbeat":
            await self._send(writer, {"type": "heartbeat", "seq": msg.get("seq", 0), "timestamp": time.time()})
        elif mtype == "nav_task":
            self._start(msg.get("start_x", 0), msg.get("start_y", 0),
                        msg.get("target_x", 0), msg.get("target_y", 0),
                        msg.get("task_name", "nav"),
                        msg.get("initial_yaw", 0))
            await self._send_update(writer)
        elif mtype == "emergency_stop":
            self._nav_active = False
            print("[NavSim] ⚠️ 紧急停止", flush=True)

    def _start(self, sx, sy, tx, ty, name, yaw):
        self._nav_active = True
        self._task_name = name
        self._start_x = self._current_x = float(sx)
        self._start_y = self._current_y = float(sy)
        self._target_x = float(tx)
        self._target_y = float(ty)
        self._current_yaw = float(yaw)
        self._step = 0
        self._avoid_cooldown = 0
        dist = math.hypot(self._target_x - self._start_x, self._target_y - self._start_y)
        print(f"[NavSim] 📍 {name}: ({sx},{sy})→({tx},{ty}) 距离={dist:.2f}m", flush=True)

    async def _nav_loop(self):
        while True:
            await asyncio.sleep(STEP_INTERVAL)
            if not self._nav_active or not self._clients:
                continue
            self._step += 1

            dist = math.hypot(self._target_x - self._current_x,
                              self._target_y - self._current_y)
            if dist < 0.05:
                self._current_x, self._current_y = self._target_x, self._target_y
                self._nav_active = False
                for w in list(self._clients):
                    try:
                        await self._send_update(w)
                        await self._send(w, {
                            "type": "nav_task_completed",
                            "task_name": self._task_name,
                            "final_x": self._current_x,
                            "final_y": self._current_y,
                            "final_yaw": self._current_yaw,
                            "total_steps": self._step,
                        })
                    except Exception:
                        self._clients.discard(w)
                print(f"[NavSim] ✅ {self._task_name} 完成 ({self._step}步)", flush=True)
                continue

            if self._avoid_cooldown > 0:
                self._avoid_cooldown -= 1

            # 随机避障
            if not self._avoiding and self._avoid_cooldown <= 0 and dist > 2.0:
                if random.random() < OBSTACLE_CHANCE:
                    await self._do_avoidance(dist)
                    continue

            # 正常收敛
            if not self._avoiding:
                step_dist = min(NAV_SPEED * STEP_INTERVAL, dist)
                dx = self._target_x - self._current_x
                dy = self._target_y - self._current_y
                self._current_x += (dx / dist) * step_dist
                self._current_y += (dy / dist) * step_dist
                self._current_yaw = math.degrees(math.atan2(dy, dx)) % 360

            for w in list(self._clients):
                try:
                    await self._send_update(w)
                except Exception:
                    self._clients.discard(w)

    async def _do_avoidance(self, dist_to_target):
        self._avoiding = True
        tx, ty, tyaw = self._current_x, self._current_y, self._current_yaw
        print(f"[NavSim] ⚠️ 避障触发 @({tx:.2f},{ty:.2f})", flush=True)

        new_yaw = (tyaw + OBSTACLE_TURN) % 360
        rad = math.radians(new_yaw)
        nx = tx + OBSTACLE_FORWARD * math.cos(rad)
        ny = ty + OBSTACLE_FORWARD * math.sin(rad)
        dx = self._target_x - nx
        dy = self._target_y - ny
        corrected_yaw = math.degrees(math.atan2(dy, dx)) % 360
        remaining = math.hypot(self._target_x - nx, self._target_y - ny)

        self._current_x = nx
        self._current_y = ny
        self._current_yaw = corrected_yaw

        avoid_msg = {
            "type": "avoidance_event",
            "task_name": self._task_name,
            "trigger_x": round(tx, 3),
            "trigger_y": round(ty, 3),
            "turn_angle": OBSTACLE_TURN,
            "forward_distance": OBSTACLE_FORWARD,
            "new_x": round(nx, 3),
            "new_y": round(ny, 3),
            "new_yaw": round(new_yaw, 1),
            "corrected_yaw": round(corrected_yaw, 1),
            "remaining_distance": round(remaining, 3),
            "timestamp": time.time(),
        }

        for w in list(self._clients):
            try:
                await self._send(w, avoid_msg)
                await self._send_update(w)
            except Exception:
                self._clients.discard(w)

        self._avoid_cooldown = 2
        self._avoiding = False

    async def _send_update(self, writer):
        dist = math.hypot(self._target_x - self._current_x,
                          self._target_y - self._current_y)
        await self._send(writer, {
            "type": "nav_position_update",
            "task_name": self._task_name,
            "current_x": round(self._current_x, 3),
            "current_y": round(self._current_y, 3),
            "yaw": round(self._current_yaw, 1),
            "distance_to_target": round(dist, 3),
            "status": "avoiding" if self._avoiding else "navigating",
            "step": self._step,
            "timestamp": time.time(),
        })

    async def _send(self, writer, msg):
        data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        writer.write(struct.pack(">I", len(data)) + data)
        await writer.drain()


def main():
    sim = NavigationSimulator()
    try:
        asyncio.run(sim.run())
    except KeyboardInterrupt:
        pass
    print("[NavSim] 已退出", flush=True)


if __name__ == "__main__":
    main()
