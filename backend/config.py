"""系统配置"""
import os

# WebSocket 服务（后端监听地址，0.0.0.0 表示监听所有网卡）
WS_HOST = os.getenv("WS_HOST", "0.0.0.0")
WS_PORT = int(os.getenv("WS_PORT", "8080"))

# TCP 通信（机器人底层）
TCP_HOST = os.getenv("TCP_HOST", "172.16.24.69")
TCP_PORT = int(os.getenv("TCP_PORT", "9090"))

# 视觉 WebSocket（D435i vision_server 端口）
VISION_WS_PORT = int(os.getenv("VISION_WS_PORT", "8765"))

# 导航模拟配置
NAV_STEP_INTERVAL = 1.0          # 坐标回传间隔 (s)
NAV_SPEED_DEFAULT = 1.0          # 默认导航速度 (m/s)
OBSTACLE_TRIGGER_CHANCE = 0.15   # 每秒障碍物触发概率
OBSTACLE_TURN_ANGLE = 45.0       # 避障左转角度 (度)
OBSTACLE_FORWARD_DIST = 2.0      # 避障前进距离 (m)

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

# 任务
TASK_MAX_QUEUE = 100
