"""系统配置"""
import os

# WebSocket 服务
WS_HOST = os.getenv("WS_HOST", "0.0.0.0")
WS_PORT = int(os.getenv("WS_PORT", "8080"))

# TCP 通信（机器人底层）
TCP_HOST = os.getenv("TCP_HOST", "127.0.0.1")
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
VISION_WS_URL = os.getenv("VISION_WS_URL", "ws://127.0.0.1:8765")
D435I_ENABLED = os.getenv("D435I_ENABLED", "true").lower() == "true"

# 任务
TASK_MAX_QUEUE = 100
