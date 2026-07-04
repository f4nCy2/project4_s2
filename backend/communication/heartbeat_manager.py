"""心跳管理器：HeartbeatManager"""
import asyncio
import time
from typing import Optional, Callable


class HeartbeatManager:
    """心跳检测：1 s 间隔 / 3 s 超时 / 连续 3 次丢失判为断连"""

    def __init__(self, interval: float = 1.0, timeout: float = 3.0,
                 max_miss: int = 3, on_dead: Optional[Callable] = None):
        self.interval = interval
        self.timeout = timeout
        self.max_miss = max_miss
        self.on_dead = on_dead
        self._alive = False
        self._last_pong = 0.0
        self._miss_count = 0
        self._seq = 0
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._on_ping: Optional[Callable] = None
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = asyncio.get_event_loop_policy().get_event_loop()

    def set_ping_callback(self, callback: Callable) -> None:
        self._on_ping = callback

    def start(self) -> None:
        self._running = True
        self._alive = True
        self._last_pong = time.time()
        self._miss_count = 0
        self._task = self._loop.create_task(self._heartbeat_loop())

    def stop(self) -> None:
        self._running = False
        self._alive = False
        if self._task and not self._task.done():
            self._task.cancel()

    def on_pong(self) -> None:
        """收到对方心跳回包"""
        self._last_pong = time.time()
        self._miss_count = 0
        self._alive = True

    def is_alive(self) -> bool:
        return self._alive

    async def _heartbeat_loop(self):
        while self._running:
            self._seq += 1
            if self._on_ping:
                self._on_ping(self._seq, time.time())
            # 检查超时
            elapsed = time.time() - self._last_pong
            if elapsed > self.timeout:
                self._miss_count += 1
                if self._miss_count >= self.max_miss:
                    self._alive = False
                    print(f"[HeartbeatManager] 心跳超时，判定断连")
                    if self.on_dead:
                        self.on_dead()
                    # 重置计数，避免 on_dead 成功后仍被重复调用
                    self._miss_count = 0
                    self._last_pong = time.time()
            await asyncio.sleep(self.interval)
