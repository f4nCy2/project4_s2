"""系统配置"""
import os

# WebSocket 服务（后端监听地址，0.0.0.0 表示监听所有网卡）
WS_HOST = os.getenv("WS_HOST", "0.0.0.0")
WS_PORT = int(os.getenv("WS_PORT", "8080"))

# TCP 通信（机器人底层）
TCP_HOST = os.getenv("TCP_HOST", "192.168.1.2")
TCP_PORT = int(os.getenv("TCP_PORT", "9090"))

# 心跳
HEARTBEAT_INTERVAL = 1.0  # s
HEARTBEAT_TIMEOUT = 3.0   # s
HEARTBEAT_MAX_MISS = 3

# 指令发送
COMMAND_TIMEOUT = 5.0     # s
COMMAND_MAX_RETRY = 3
COMMAND_RATE_LIMIT = 10   # Hz

# UI 刷新
UI_REFRESH_RATE = 10      # Hz

# D435i
# 视觉帧流地址。跨机部署时改为 D435i 电脑 IP，例如 ws://192.168.1.200:8765
VISION_WS_URL = os.getenv("VISION_WS_URL", "ws://192.168.1.2:8765")
D435I_ENABLED = os.getenv("D435I_ENABLED", "true").lower() == "true"

# 任务
TASK_MAX_QUEUE = 100
