# 人形机器人控制中心

基于 FastAPI + WebSocket 的人形机器人远程控制与任务调度系统，集成 Intel RealSense D435i 视觉实时避障。

---

## 系统架构

项目采用三层架构：

```
┌─────────────────────────────────────────────────────────────┐
│                        前端 (Frontend)                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  /control   │  │ /scheduler  │  │      /files2        │  │
│  │  控制面板   │  │  任务调度   │  │  模块化控制台       │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
└─────────┼────────────────┼────────────────────┼─────────────┘
          │                │                    │
          └────────────────┴────────────────────┘
                             │ WebSocket
                    ┌────────┴────────┐
                    │  backend/server │
                    │   (FastAPI)     │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
        ┌─────┴─────┐  ┌─────┴─────┐  ┌────┴────┐
        │ TaskManager│  │ TCP Client │  │ Vision  │
        │  任务调度  │  │  机器人通信 │  │  Bridge │
        └───────────┘  └─────┬─────┘  └────┬────┘
                             │ TCP 9090    │ WS 8765
                    ┌────────┴────────┐    │
                    │    机器人底层    │◄───┘
                    └─────────────────┘
                             ▲
                             │ control_cmd
                    ┌────────┴────────┐
                    │ d435i_obstacle  │
                    │ _avoidance      │
                    │ (VFH / YOLO)    │
                    └─────────────────┘
```

- **D435i 视觉避障**：独立算法进程，输出 `control_cmd` 给后端。
- **后端**：负责任务调度、机器人 TCP 通信、视频流转发、前端 WS 服务。
- **前端**：纯静态页面，提供控制面板、任务调度、控制台视图。

---

## 目录结构

```
project4_stage2/
├── backend/                    # FastAPI 后端
│   ├── server.py               # 服务入口
│   ├── config.py               # 配置（端口、心跳、D435i 等）
│   ├── common/                 # 数据模型与枚举
│   ├── communication/          # TCP / WebSocket 通信
│   ├── task_planner/           # 任务、动作、避障调度
│   └── vision/                 # D435i 视觉桥接
├── frontend/                   # 前端静态页面
│   ├── control/                # 机器人控制面板
│   ├── scheduler/              # 任务调度系统
│   └── files2/                 # 模块化控制台
├── d435i_obstacle_avoidance/   # D435i 视觉避障算法
│   ├── src/
│   │   ├── obstacle_avoidance.py       # VFH 栅格避障
│   │   └── obstacle_avoidance_yolo.py  # 三区域 ROI / YOLO 避障
│   ├── docs/                   # 算法原理文档
│   └── requirements.txt        # 视觉模块依赖
├── start.sh                    # 后端一键启动脚本
└── README.md                   # 本文件
```

---

## 快速开始

### 1. 环境准备

项目根目录下需要有一个名为 `.project4` 的 Python 虚拟环境：

```bash
python3 -m venv .project4
```

### 2. 启动后端

```bash
./start.sh
```

脚本会自动检查并安装后端依赖：`fastapi`、`uvicorn`、`websockets`、`pydantic`、`numpy`。

启动后访问：

- 控制面板：`http://127.0.0.1:8080/control`
- 任务调度：`http://127.0.0.1:8080/scheduler`
- API 根路径：`http://127.0.0.1:8080/`

### 3. 启动 D435i 视觉避障（可选）

在 Windows 环境下：

```powershell
cd d435i_obstacle_avoidance
pip install -r requirements.txt
python src/obstacle_avoidance.py
```

详细说明见 [`d435i_obstacle_avoidance/README.md`](d435i_obstacle_avoidance/README.md)。

---

## 跨机部署：用另一台电脑 + D435i 模拟机器人端

如果你没有 NUC 或真实机器人底盘，可以用一台带 D435i 的电脑模拟“机器人端”，主控电脑跑后端+前端，两台电脑通过局域网连接。

### 网络拓扑

```
┌──────────────────────┐                      ┌────────────────────────────────┐
│    主控电脑          │                      │       机器人模拟电脑           │
│  (后端 + 前端)       │  ───── TCP 9090 ───► │  robot_simulator.py            │
│  backend/server.py   │ ◄──── 状态/心跳 ─────│  （模拟机器人底层）            │
│  监听 0.0.0.0:8080   │                      │                                │
│                      │ ◄── WS /ws/robot ─── │  obstacle_avoidance.py         │
│                      │   (d435i 控制指令)   │  （D435i 视觉避障）            │
│                      │                      │                                │
│                      │ ◄─── WS 8765 ────── │  vision_server.py              │
│                      │   (RGB/深度视频流)   │  （D435i 视觉帧回传）          │
└──────────────────────┘                      └────────────────────────────────┘
```

### 1. 配置主控电脑（后端）

假设机器人模拟电脑的 IP 是 `192.168.1.200`，启动后端时指定 TCP 目标：

```bash
# 在项目根目录
export TCP_HOST=192.168.1.200
export TCP_PORT=9090
export WS_HOST=0.0.0.0
./start.sh
```

### 2. 配置机器人模拟电脑（D435i 端）

**推荐：一键启动机器人端三件套**

```bash
cd d435i_obstacle_avoidance

# 基础：机器人模拟 + VFH 避障 + RGB 视频流回传
export D435I_BACKEND_URL=ws://192.168.1.100:8080/ws/robot
export VISION_WS_HOST=0.0.0.0
python src/robot_side.py

# 只跑底层模拟（不接 D435i 测试用）
export ROBOT_ENABLE_VISION=false
export ROBOT_AVOIDANCE=none
python src/robot_side.py
```

`robot_side.py` 会同时启动：
- `robot_simulator.py`（TCP 9090）
- `vision_server.py`（WS 8765）
- `obstacle_avoidance.py`（VFH 栅格避障，默认）

按 `Ctrl+C` 即可统一关闭。

**也可以分别启动（调试用）：**

```bash
# 机器人底层模拟器
python src/robot_simulator.py

# 避障程序
export D435I_BACKEND_URL=ws://192.168.1.100:8080/ws/robot
python src/obstacle_avoidance.py

# 视觉帧服务器（默认 RGB，改 STREAM_DEPTH=true 传深度图）
export VISION_WS_HOST=0.0.0.0
python src/vision_server.py
```

### 3. 环境变量汇总

| 环境变量 | 默认值 | 作用位置 | 说明 |
|----------|--------|----------|------|
| `WS_HOST` | `0.0.0.0` | 主控电脑 | 后端 WebSocket 监听地址 |
| `WS_PORT` | `8080` | 主控电脑 | 后端 WebSocket 端口 |
| `TCP_HOST` | `127.0.0.1` | 主控电脑 | 机器人底层 TCP 目标地址 |
| `TCP_PORT` | `9090` | 主控电脑 | 机器人底层 TCP 目标端口 |
| `ROBOT_HOST` | `0.0.0.0` | 机器人电脑 | 模拟器监听地址 |
| `ROBOT_PORT` | `9090` | 机器人电脑 | 模拟器监听端口 |
| `D435I_BACKEND_URL` | `ws://127.0.0.1:8080/ws/robot` | 机器人电脑 | D435i 避障程序连接的后端地址 |
| `D435I_BACKEND_ENABLED` | `true` | 机器人电脑 | 是否启用向后端发送控制指令 |
| `D435I_SEND_INTERVAL` | `0.1` | 机器人电脑 | 控制指令最小发送间隔 (s) |
| `VISION_WS_URL` | `ws://127.0.0.1:8765` | 主控电脑 | 后端连接 D435i 视觉流的地址 |
| `VISION_WS_HOST` | `0.0.0.0` | 机器人电脑 | 视觉帧服务器监听地址 |
| `VISION_WS_PORT` | `8765` | 机器人电脑 | 视觉帧服务器监听端口 |
| `VISION_FPS` | `10` | 机器人电脑 | 视觉帧回传帧率上限 |
| `VISION_QUALITY` | `70` | 机器人电脑 | JPEG 质量 1-100 |
| `STREAM_DEPTH` | `false` | 机器人电脑 | 是否回传深度伪彩图 |
| `ROBOT_AVOIDANCE` | `vfh` | 机器人电脑 | robot_side 启动的避障程序：`vfh` / `none` |
| `ROBOT_ENABLE_VISION` | `true` | 机器人电脑 | robot_side 是否启动 vision_server |
| `ROBOT_ENABLE_SIM` | `true` | 机器人电脑 | robot_side 是否启动 robot_simulator |
| `ROBOT_AUTO_RESTART` | `true` | 机器人电脑 | robot_side 是否自动重启崩溃的子进程 |

### 4. 验证

- 主控电脑浏览器打开 `http://<主控IP>:8080/control`
- 在机器人模拟电脑上用手或障碍物在 D435i 前移动
- 主控前端应能看到 d435i 控制指令更新，模拟器终端会打印接收到的 `steer/speed`

## 后端模块说明

| 模块 | 核心文件 | 作用 |
|------|----------|------|
| 服务入口 | `backend/server.py` | FastAPI 入口，提供 `/ws/control`、`/ws/scheduler`、`/ws/robot` 三个 WebSocket 端点，挂载前端静态页面 |
| 配置 | `backend/config.py` | 服务端口、TCP 机器人地址、心跳、D435i 开关等 |
| 数据模型 | `backend/common/` | `RobotStatus`、`Task`、`Action` 等 Pydantic 模型与枚举 |
| 通信 | `backend/communication/` | TCP 客户端、心跳、指令队列、超时重试、WebSocket 桥接 |
| 任务调度 | `backend/task_planner/` | 任务生命周期、动作序列、运动规划、障碍物检测与避障 |
| 视觉桥接 | `backend/vision/vision_bridge.py` | 连接 D435i 算法，转发图像帧到前端 |

---

## 前端模块说明

| 目录 | 访问路径 | 作用 |
|------|----------|------|
| `frontend/control/` | `/control` | 机器人实时控制面板：状态监控、摄像头画面、手动动作、任务启停、系统日志、紧急停止 |
| `frontend/scheduler/` | `/scheduler` | 任务调度系统：创建任务、配置动作序列、查看队列、启停控制 |
| `frontend/files2/` | `/files2` | 模块化控制台：按功能拆分为 Dashboard、状态管理、控制面板、日志、避障模块 |

---

## 配置说明

主要配置在 `backend/config.py` 中，也可通过环境变量覆盖：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `WS_HOST` | `0.0.0.0` | WebSocket 服务监听地址 |
| `WS_PORT` | `8080` | WebSocket 服务端口 |
| `TCP_HOST` | `127.0.0.1` | 机器人底层 TCP 地址 |
| `TCP_PORT` | `9090` | 机器人底层 TCP 端口 |
| `VISION_WS_URL` | `ws://127.0.0.1:8765` | D435i 视觉算法 WebSocket 地址 |
| `D435I_ENABLED` | `true` | 是否启用 D435i 视觉连接 |

---

## 数据流

1. **手动控制**：前端 `/control` → 后端 `/ws/control` → TCP 9090 → 机器人底层
2. **任务调度**：前端 `/scheduler` → 后端 `TaskManager` → 动作序列 → TCP 9090 → 机器人底层
3. **视觉避障**：`d435i_obstacle_avoidance` → `control_cmd` → 后端 `/ws/robot` → TCP 9090 → 机器人底层
4. **状态/视频回传**：机器人底层 → TCP 9090 → 后端汇总 → 前端 `/control`

---

## 依赖说明

- **后端**：`fastapi`、`uvicorn`、`websockets`、`pydantic`、`numpy`（`start.sh` 自动检查安装）
- **D435i 视觉避障**：见 `d435i_obstacle_avoidance/requirements.txt`
- **前端**：纯静态页面，无需构建；`frontend/files2/mock-server.js` 本地模拟需要 Node.js 的 `ws` 包
