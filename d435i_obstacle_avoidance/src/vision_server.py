#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D435i 视觉帧回传服务器

把 D435i 的彩色或深度图编码为 JPEG，通过 WebSocket 广播给后端 VisionBridge，
最终显示在前端 /control 的摄像头画面区域。

用法（跑在带 D435i 的电脑上）：
    python src/vision_server.py

环境变量：
    VISION_WS_HOST   - WebSocket 监听地址，默认 0.0.0.0
    VISION_WS_PORT   - WebSocket 监听端口，默认 8765
    VISION_FPS       - 帧率上限，默认 10
    VISION_QUALITY   - JPEG 质量 1-100，默认 70
    STREAM_DEPTH     - 是否传深度伪彩图，默认 false（传 RGB）

后端连接：
    主控电脑设置 VISION_WS_URL=ws://<D435i电脑IP>:8765
"""

import os
import asyncio
import json
import base64
import signal
import threading
import time

import cv2
import numpy as np


try:
    import websockets
except ImportError as e:
    raise ImportError("请先安装 websockets: pip install websockets") from e


VISION_WS_HOST = os.getenv("VISION_WS_HOST", "0.0.0.0")
VISION_WS_PORT = int(os.getenv("VISION_WS_PORT", "8765"))
VISION_FPS = int(os.getenv("VISION_FPS", "10"))
VISION_QUALITY = int(os.getenv("VISION_QUALITY", "70"))
STREAM_DEPTH = os.getenv("STREAM_DEPTH", "false").lower() == "true"
VISION_CAMERA_ENABLED = os.getenv("VISION_CAMERA_ENABLED", "true").lower() == "true"

WIDTH, HEIGHT, FPS = 640, 480, 30


class VisionServer:
    def __init__(self):
        self.clients = set()
        self.frame_b64 = None
        self._lock = threading.Lock()
        self._running = True

    # ── WebSocket 连接处理 ──
    async def handler(self, websocket, path=None):
        self.clients.add(websocket)
        print(f"[VisionServer] 客户端已连接: {websocket.remote_address}，当前 {len(self.clients)} 个")
        try:
            # 连上后立即发一帧，减少前端等待
            with self._lock:
                if self.frame_b64:
                    await websocket.send(json.dumps({"frame_b64": self.frame_b64}))
            await websocket.wait_closed()
        except Exception as e:
            print(f"[VisionServer] 客户端异常: {e}")
        finally:
            self.clients.discard(websocket)
            print(f"[VisionServer] 客户端断开: {websocket.remote_address}，当前 {len(self.clients)} 个")

    # ── 帧广播循环 ──
    async def broadcast_loop(self):
        interval = 1.0 / max(1, VISION_FPS)
        while self._running:
            await asyncio.sleep(interval)
            with self._lock:
                if not self.frame_b64 or not self.clients:
                    continue
                msg = json.dumps({"frame_b64": self.frame_b64})

            dead = []
            for ws in list(self.clients):
                try:
                    await ws.send(msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.clients.discard(ws)

    # ── D435i 采集循环（在独立线程中运行） ──
    def capture_loop(self):
        if not VISION_CAMERA_ENABLED:
            print("[VisionServer] 相机采集已禁用 (VISION_CAMERA_ENABLED=false)，仅提供 WebSocket 服务")
            return

        try:
            import pyrealsense2 as rs
        except ImportError:
            print("[VisionServer] ❌ 未安装 pyrealsense2，无法采集 D435i")
            # 不关闭服务器，保持 WebSocket 运行（便于调试连接）
            return

        pipeline = rs.pipeline()
        config = rs.config()
        config.enable_stream(rs.stream.color, WIDTH, HEIGHT, rs.format.bgr8, FPS)
        config.enable_stream(rs.stream.depth, WIDTH, HEIGHT, rs.format.z16, FPS)

        try:
            profile = pipeline.start(config)
            print(f"[VisionServer] D435i 已启动: {WIDTH}x{HEIGHT}@{FPS}fps")
        except Exception as e:
            print(f"[VisionServer] ❌ 相机启动失败: {e}")
            print("请检查 USB3.0 连接和 RealSense SDK 是否正确安装。")
            # 不关闭服务器，保持 WebSocket 运行（便于调试连接）
            return

        align = rs.align(rs.stream.color)
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), max(1, min(100, VISION_QUALITY))]
        frame_count = 0
        t0 = time.time()

        try:
            while self._running:
                frames = pipeline.wait_for_frames(timeout_ms=5000)
                aligned = align.process(frames)
                color_frame = aligned.get_color_frame()
                depth_frame = aligned.get_depth_frame()

                if not color_frame or not depth_frame:
                    continue

                if STREAM_DEPTH:
                    depth_image = np.asanyarray(depth_frame.get_data())
                    # 截断到 4m，转伪彩色
                    depth_vis = np.clip(depth_image, 0, 4000).astype(np.float32)
                    depth_norm = cv2.convertScaleAbs(depth_vis, alpha=255.0 / 4000.0)
                    img = cv2.applyColorMap(depth_norm, cv2.COLORMAP_JET)
                else:
                    img = np.asanyarray(color_frame.get_data())

                ret, buf = cv2.imencode(".jpg", img, encode_param)
                if not ret:
                    continue

                b64 = base64.b64encode(buf).decode("utf-8")
                with self._lock:
                    self.frame_b64 = b64

                frame_count += 1
                if frame_count % 60 == 0:
                    elapsed = time.time() - t0
                    print(f"[VisionServer] 采集 FPS: {frame_count / elapsed:.1f} | 客户端: {len(self.clients)}")

        except Exception as e:
            print(f"[VisionServer] 采集异常: {e}")
        finally:
            pipeline.stop()
            print("[VisionServer] D435i 已关闭")


async def main():
    server = VisionServer()

    # 在后台线程采集相机
    cap_thread = threading.Thread(target=server.capture_loop, daemon=True)
    cap_thread.start()

    if VISION_CAMERA_ENABLED:
        # 等待首帧
        print("[VisionServer] 等待 D435i 首帧...")
        for _ in range(30):
            await asyncio.sleep(0.1)
            if server.frame_b64 is not None:
                break
    else:
        print("[VisionServer] 相机采集已禁用，跳过首帧等待")

    print(f"[VisionServer] 启动 WebSocket: ws://{VISION_WS_HOST}:{VISION_WS_PORT}")
    print(f"[VisionServer] 流类型: {'深度伪彩图' if STREAM_DEPTH else 'RGB 彩色图'}")
    print(f"[VisionServer] 帧率上限: {VISION_FPS}fps | JPEG 质量: {VISION_QUALITY}")

    # 注册信号处理
    stop_event = asyncio.Event()

    def _request_stop():
        print("\n[VisionServer] 收到终止信号", flush=True)
        server._running = False
        stop_event.set()

    loop = asyncio.get_running_loop()
    # 先重置可能继承的 SIG_IGN
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, signal.SIG_DFL)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_stop)

    try:
        async with websockets.serve(server.handler, VISION_WS_HOST, VISION_WS_PORT):
            serve_task = asyncio.create_task(server.broadcast_loop())
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
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)

    print("[VisionServer] 已退出", flush=True)


def run():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[VisionServer] 已退出")


if __name__ == "__main__":
    run()
