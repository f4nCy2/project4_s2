"""视觉桥接：连接 D435i 避障程序，转发视觉帧到 WebSocket"""
import asyncio
import json
import base64
from typing import Optional, Callable
import websockets as ws_lib


class VisionBridge:
    """作为 WebSocket 客户端连接 D435i 避障程序，将图像帧广播给前端"""

    def __init__(self, url: str = "ws://127.0.0.1:8765",
                 on_frame: Optional[Callable] = None):
        self.url = url
        self.on_frame = on_frame
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._connect_loop())

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _connect_loop(self):
        while self._running:
            try:
                async with ws_lib.connect(self.url) as ws:
                    print(f"[VisionBridge] 已连接视觉端: {self.url}")
                    while self._running:
                        raw = await ws.recv()
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="replace")
                        try:
                            parsed = json.loads(raw)
                            b64 = parsed.get("frame_b64", "")
                        except (json.JSONDecodeError, AttributeError):
                            b64 = raw.strip()
                        if b64 and self.on_frame:
                            self.on_frame({"frame": b64, "type": "vision_frame"})
            except Exception as e:
                print(f"[VisionBridge] 连接断开: {e}，3s 后重连")
                await asyncio.sleep(3)
