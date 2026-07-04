"""可靠指令发送：CommandSender"""
import asyncio
import time
from typing import Optional, Dict, List
from backend.common.models import Command
from backend.communication.socket_client import SocketClient


class CommandSender:
    """指令队列缓存 + 超时重试 + 发送限速"""

    def __init__(self, socket_client: SocketClient,
                 timeout: float = 5.0, max_retry: int = 3, rate_limit: int = 10):
        self._socket = socket_client
        self.timeout = timeout
        self.max_retry = max_retry
        self.rate_limit = rate_limit
        self._queue: List[Command] = []
        self._pending: Dict[int, Command] = {}
        self._seq_counter = 1000
        self._last_send_time = 0.0
        self._running = False
        self._task: Optional[asyncio.Task] = None
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = asyncio.get_event_loop_policy().get_event_loop()

    def start(self) -> None:
        self._running = True
        self._task = self._loop.create_task(self._send_loop())

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    def send_command(self, command: Command) -> bool:
        """入队发送指令"""
        if not command.seq:
            self._seq_counter += 1
            command.seq = self._seq_counter
        self._queue.append(command)
        return True

    def send_action(self, action_type: str, params: dict) -> bool:
        cmd = Command(type="command", action=action_type, params=params,
                      seq=self._next_seq(), priority="NORMAL")
        return self.send_command(cmd)

    def emergency_stop(self) -> bool:
        cmd = Command(type="command", action="stop", params={"emergency": True},
                      seq=self._next_seq(), priority="HIGH")
        return self.send_command(cmd)

    def _next_seq(self) -> int:
        self._seq_counter += 1
        return self._seq_counter

    async def _send_loop(self):
        while self._running:
            if not self._queue:
                await asyncio.sleep(0.05)
                continue
            # 限速
            now = time.time()
            interval = 1.0 / self.rate_limit
            if now - self._last_send_time < interval:
                await asyncio.sleep(interval - (now - self._last_send_time))
            cmd = self._queue.pop(0)
            success = await self._try_send(cmd)
            if not success:
                print(f"[CommandSender] 指令发送失败 seq={cmd.seq}")
            self._last_send_time = time.time()

    async def _try_send(self, cmd: Command) -> bool:
        import json
        data = json.dumps(cmd.model_dump(exclude_none=True), ensure_ascii=False).encode("utf-8")
        for attempt in range(self.max_retry):
            if not self._socket.is_connected():
                await asyncio.sleep(0.5)
                continue
            success = self._socket.send(data)
            if success:
                return True
            await asyncio.sleep(0.5 * (attempt + 1))
        return False
