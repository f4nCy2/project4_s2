"""统一通信 API：APIService"""
import asyncio
import json
from typing import Optional, Callable
from backend.common.interfaces import ICommandSender, ICommunication
from backend.common.models import Command, RobotStatus, TaskEventMessage
from backend.communication.socket_client import SocketClient
from backend.communication.command_sender import CommandSender
from backend.communication.heartbeat_manager import HeartbeatManager


class APIService:
    """统一通信 API，实现 ICommunication 接口，解耦模块依赖"""

    def __init__(self, host: str = "127.0.0.1", port: int = 9090):
        self._socket = SocketClient(host=host, port=port, reconnect_interval=3.0)
        self._sender = CommandSender(self._socket, timeout=5.0, max_retry=3, rate_limit=10)
        self._heartbeat = HeartbeatManager(
            interval=1.0, timeout=3.0, max_miss=3,
            on_dead=self._on_dead
        )
        self._heartbeat.set_ping_callback(self._send_ping)
        self._on_status: Optional[Callable] = None
        self._on_task_event: Optional[Callable] = None

    def set_callbacks(self, on_status=None, on_task_event=None):
        self._on_status = on_status
        self._on_task_event = on_task_event

    def connect(self) -> bool:
        ok = self._socket.connect()
        if ok:
            self._socket.on_message(self._on_raw_message)
            self._sender.start()
            self._heartbeat.start()
        return ok

    def disconnect(self) -> None:
        self._heartbeat.stop()
        self._sender.stop()
        self._socket.disconnect()

    def is_connected(self) -> bool:
        return self._socket.is_connected() and self._heartbeat.is_alive()

    def send_raw(self, data: bytes) -> bool:
        """直接发送原始字节数据（用于 d435i 实时控制指令转发）"""
        return self._socket.send(data)

    def send_command(self, command: Command) -> bool:
        return self._sender.send_command(command)

    def send_action(self, action_type: str, params: dict) -> bool:
        return self._sender.send_action(action_type, params)

    def emergency_stop(self) -> bool:
        return self._sender.emergency_stop()

    def _send_ping(self, seq: int, ts: float):
        import json
        ping = json.dumps({"type": "heartbeat", "seq": seq, "timestamp": ts}).encode("utf-8")
        self._socket.send(ping)

    def _on_raw_message(self, data: bytes):
        """解析 TCP 消息（4字节前缀已去掉）"""
        try:
            msg = json.loads(data.decode("utf-8"))
            mtype = msg.get("type", "")
            if mtype == "status" and self._on_status:
                self._on_status(msg)
            elif mtype == "task_event" and self._on_task_event:
                self._on_task_event(msg)
            elif mtype == "heartbeat":
                self._heartbeat.on_pong()
            # ── 2D 导航消息路由 ──
            elif mtype == "nav_position_update":
                # 将导航位置更新包装为 status 消息转发到后端
                if self._on_status:
                    self._on_status({"type": "status", "status": {}, "nav_position": msg})
            elif mtype == "avoidance_event":
                # 避障事件转发
                if self._on_status:
                    self._on_status({"type": "status", "status": {}, "avoidance_event": msg})
            elif mtype == "nav_task_completed":
                # 导航任务完成 — 通过 on_status 路由
                if self._on_status:
                    self._on_status({"type": "status", "status": {}, "nav_completed": msg})
        except Exception as e:
            print(f"[APIService] 消息解析失败: {e}")

    def _on_dead(self):
        print("[APIService] 连接已断开，尝试重连...")
        self._socket.connect()
