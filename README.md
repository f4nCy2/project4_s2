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

或使用三区域 ROI / YOLO 版本：

```powershell
python src/obstacle_avoidance_yolo.py
```

详细说明见 [`d435i_obstacle_avoidance/README.md`](d435i_obstacle_avoidance/README.md)。

---

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
