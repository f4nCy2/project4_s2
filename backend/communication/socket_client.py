"""TCP 连接管理：SocketClient"""
import socket
import struct
import asyncio
from typing import Optional, Callable
from backend.common.interfaces import ICommunication


class SocketClient(ICommunication):
    """TCP 连接管理，自动重连，4 字节大端长度前缀 + JSON Body"""

    def __init__(self, host: str = "127.0.0.1", port: int = 9090,
                 reconnect_interval: float = 3.0):
        self.host = host
        self.port = port
        self.reconnect_interval = reconnect_interval
        self._sock: Optional[socket.socket] = None
        self._connected = False
        self._running = False
        self._connecting = False
        self._message_callback: Optional[Callable[[bytes], None]] = None
        self._reader_task: Optional[asyncio.Task] = None
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = asyncio.get_event_loop_policy().get_event_loop()

    def connect(self) -> bool:
        if self._connecting:
            print("[SocketClient] 连接正在进行中，跳过重复请求")
            return False
        self._connecting = True

        try:
            # 关闭旧 socket，避免 FD 泄露
            if self._sock:
                try:
                    self._sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
            if self._reader_task and not self._reader_task.done():
                self._reader_task.cancel()
                self._reader_task = None

            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(5.0)
            self._sock.connect((self.host, self.port))
            self._sock.settimeout(None)
            self._connected = True
            self._running = True
            self._reader_task = self._loop.create_task(self._read_loop())
            return True
        except Exception as e:
            print(f"[SocketClient] 连接失败: {e}")
            self._connected = False
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
            return False
        finally:
            self._connecting = False

    def disconnect(self) -> None:
        self._running = False
        self._connected = False
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def is_connected(self) -> bool:
        return self._connected and self._sock is not None

    def send(self, data: bytes) -> bool:
        if not self._connected or not self._sock:
            return False
        try:
            length_prefix = struct.pack(">I", len(data))
            self._sock.sendall(length_prefix + data)
            return True
        except Exception as e:
            print(f"[SocketClient] 发送失败: {e}")
            self._connected = False
            return False

    def receive(self) -> Optional[bytes]:
        """同步接收（阻塞）"""
        if not self._connected or not self._sock:
            return None
        try:
            # 读取 4 字节长度前缀
            prefix = self._recv_all(4)
            if not prefix:
                return None
            length = struct.unpack(">I", prefix)[0]
            body = self._recv_all(length)
            return body
        except Exception:
            self._connected = False
            return None

    def _recv_all(self, n: int) -> Optional[bytes]:
        data = b""
        while len(data) < n:
            try:
                chunk = self._sock.recv(n - len(data))
                if not chunk:
                    return None
                data += chunk
            except Exception:
                return None
        return data

    async def _read_loop(self):
        """异步读取循环"""
        sock = self._sock  # 保存局部引用，避免 connect 覆盖后关闭新 socket
        while self._running and self._connected:
            try:
                body = await asyncio.to_thread(self.receive)
                if body and self._message_callback:
                    self._message_callback(body)
                elif body is None:
                    print("[SocketClient] 连接已断开")
                    self._connected = False
                    break
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[SocketClient] 读取异常: {e}")
                self._connected = False
                break

        # 清理旧 socket（使用局部引用，避免关闭 connect 创建的新 socket）
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        # 不再自动重连，由外部（APIService._on_dead 或 _connect_apiservice）负责重连

    def on_message(self, callback: Callable[[bytes], None]) -> None:
        self._message_callback = callback
