#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机器人底层 TCP 模拟器

用法（跑在带 D435i 的电脑上，模拟机器人底层）：
    python src/robot_simulator.py

环境变量：
    ROBOT_HOST  - 监听地址，默认 0.0.0.0
    ROBOT_PORT  - 监听端口，默认 9090

通信协议：与后端对齐
    4 字节大端长度前缀 + JSON Body
    接收：low_level_control / command / heartbeat / emergency_stop
    发送：status / heartbeat
"""

import asyncio
import json
import os
import signal
import struct
import time


ROBOT_HOST = os.getenv("ROBOT_HOST", "0.0.0.0")
ROBOT_PORT = int(os.getenv("ROBOT_PORT", "9090"))
STATUS_INTERVAL = 1.0  # 主动上报状态周期 (s)


class RobotSimulator:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._seq = 0
        self._state = "idle"
        self._speed = 0.0
        self._steer = 0.0
        self._clients = set()

    async def run(self):
        server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        addr = server.sockets[0].getsockname()
        print(f"[RobotSimulator] 机器人底层模拟器已启动: {addr[0]}:{addr[1]}", flush=True)
        print(f"[RobotSimulator] 等待后端 TCP 连接...", flush=True)

        # 启动状态广播任务
        status_task = asyncio.create_task(self._status_loop())

        # 注册信号处理，支持 Ctrl+C / SIGTERM 优雅退出
        stop_event = asyncio.Event()

        def _request_stop():
            print("\n[RobotSimulator] 收到终止信号", flush=True)
            stop_event.set()

        loop = asyncio.get_running_loop()
        # 先重置可能继承的 SIG_IGN
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal.SIG_DFL)
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _request_stop)

        try:
            async with server:
                serve_task = asyncio.create_task(server.serve_forever())
                stop_task = asyncio.create_task(stop_event.wait())
                # 任一任务完成即退出（通常是 stop_event）
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
            status_task.cancel()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)

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
            # 回送心跳，保持后端连接存活
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
            # 模拟动作执行，简单回传 task_event
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
            return

        print(f"[RobotSimulator] 未知消息类型: {mtype} | {msg}", flush=True)

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
                    "position": [0.0, 0.0],
                    "orientation": [0.0, 0.0, 0.0],
                    "velocity": self._speed,
                    "error_code": 0,
                    "cpu": 15.0,
                    "obstacle_dist": None,
                    "joints": {},
                    "connected": True,
                    "timestamp": time.time()
                }
            }
                "type": "status",
                "status": {
                    "state": self._state,
                    "battery": 85.0,
                    "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "orientation": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
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
