"""WebSocket 服务器（FastAPI）

架构设计：
  d435i避障算法 → 输出实时控制指令 (steer/speed) → 后端仅转发 → TCP → 机器人底层
  后端负责任务调度，不直接规划避障路径。

端点：
  /ws/control  — 机器人控制界面（前端 B1）
  /ws/scheduler — 任务调度界面（前端 B2）
  /ws/robot   — 机器人底层连接：接收 d435i 控制指令 + 状态回传

功能：
  - 接收 d435i 实时控制指令 → 直接通过 TCP 转发给机器人
  - 接收任务创建 → 交给 TaskManager → 调度执行（高层动作）
  - 接收状态回传 → 广播给所有前端
  - 视觉帧 → 广播给控制端
"""
import asyncio
import json
import time
import os
import sys
import re
import base64
from contextlib import asynccontextmanager

# 添加项目根到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.config import WS_HOST, WS_PORT, TCP_HOST, TCP_PORT
from backend.common.models import (
    Command, TaskControlCommand, StatusMessage, TaskEventMessage,
    ObstacleMessage, VisionFrameMessage, AckMessage, ErrorMessage,
    RobotStatus, Action, ActionParams, TaskStatus
)
from backend.common.enums import ActionType, RobotState, TaskPriority
from backend.task_planner.task_manager import TaskManager
from backend.task_planner.action_scheduler import ActionScheduler
from backend.task_planner.motion_planner import MotionPlanner
from backend.communication.api_service import APIService

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：初始化模块 → 运行 → 清理"""
    await init_modules_async()
    yield
    # 关闭连接
    if state.apiservice:
        state.apiservice.disconnect()


app = FastAPI(title="人形机器人控制中心", description="三层架构控制后端", lifespan=lifespan)

# ── 挂载静态文件 ──
app.mount("/control", StaticFiles(directory="frontend/control", html=True), name="control")
app.mount("/scheduler", StaticFiles(directory="frontend/scheduler", html=True), name="scheduler")

# ── 全局状态 ──
class ServerState:
    def __init__(self):
        self.control_clients: set = set()
        self.scheduler_clients: set = set()
        self.robot_ws_clients: set = set()  # d435i / 机器人底层
        self.apiservice: Optional[APIService] = None
        self.task_manager: Optional[TaskManager] = None
        self.robot_status = RobotStatus()
        self._seq = 0
        self._avoidance_mode = False  # 是否由 d435i 接管避障控制

state = ServerState()

# base64 简单校验正则
_B64_RE = re.compile(r"^[A-Za-z0-9+/=]+$")


def _is_valid_frame(frame: str) -> bool:
    """校验视频帧 base64 字符串是否合法"""
    if not isinstance(frame, str) or not frame:
        return False
    if len(frame) > 4 * 1024 * 1024:  # 4MB b64 上限
        return False
    if not _B64_RE.match(frame):
        return False
    # 进一步校验能否解码
    try:
        base64.b64decode(frame, validate=True)
    except Exception:
        return False
    return True

# ── 初始化模块 ──
async def init_modules_async():
    # 通信层：连接机器人底层 TCP（后台重连，避免阻塞启动）
    apiservice = APIService(host=TCP_HOST, port=TCP_PORT)
    print(f"[Server] 机器人 TCP 目标: {TCP_HOST}:{TCP_PORT}")
    apiservice.set_callbacks(
        on_status=lambda msg: asyncio.create_task(_handle_robot_status(msg)),
        on_task_event=lambda msg: asyncio.create_task(_handle_robot_task_event(msg))
    )
    state.apiservice = apiservice
    # 在后台任务中尝试连接/重连，不阻塞服务启动
    asyncio.create_task(_connect_apiservice(apiservice))

    # 任务调度层：高层动作序列管理
    scheduler = ActionScheduler(command_sender=apiservice)
    task_manager = TaskManager(scheduler=scheduler)
    task_manager.subscribe_task_events(lambda evt: asyncio.create_task(_broadcast_task_event(evt)))
    state.task_manager = task_manager

    print("[Server] 所有后端模块初始化完成")
    print("[Server] 避障架构: d435i → VFH → 控制指令 → 后端转发 → TCP → 机器人底层")
    print("[Server] 后端不规划避障路径，只做指令转发和任务调度")


async def _connect_apiservice(apiservice: APIService):
    """后台持续重连机器人 TCP，直到成功"""
    while True:
        # 如果 on_dead 已经重连成功，直接退出
        if apiservice.is_connected():
            print("[Server] 机器人 TCP 连接成功 (由 on_dead 重连)")
            break
        ok = await asyncio.to_thread(apiservice.connect)
        if ok:
            print("[Server] 机器人 TCP 连接成功")
            break
        print("[Server] 机器人 TCP 连接失败，3s 后重试...")
        await asyncio.sleep(3.0)


async def _handle_robot_status(msg):
    """处理机器人底层状态上报"""
    raw = msg.get("status", {})

    # 兼容数组格式的 position/orientation（robot_simulator.py 发送 [x,y] / [r,p,y]）
    def _normalize(val, keys):
        if isinstance(val, list):
            return dict(zip(keys, val))
        return val

    raw = dict(raw)  # 避免修改原 dict
    raw["position"] = _normalize(raw.get("position"), ["x", "y", "z"])
    raw["orientation"] = _normalize(raw.get("orientation"), ["roll", "pitch", "yaw"])

    status = RobotStatus(**raw)
    state.robot_status = status
    await _broadcast_control({"type": "status", "status": status.model_dump()})


async def _handle_robot_task_event(msg):
    """处理机器人任务事件"""
    await _broadcast_scheduler({"type": "task_event", **msg})


async def _broadcast_task_event(evt: dict):
    """广播任务事件给所有前端"""
    await _broadcast_control({"type": "task_event", **evt})
    await _broadcast_scheduler({"type": "task_event", **evt})


async def _broadcast_vision(data: dict):
    """广播视觉帧给控制端"""
    frame = data.get("frame", "")
    if not _is_valid_frame(frame):
        print(f"[Server] ⚠️ 收到非法视频帧，长度={len(frame) if isinstance(frame, str) else 'N/A'}，已丢弃")
        return
    await _broadcast_control({"type": "vision_frame", "frame": frame})


async def _broadcast_control(payload: dict):
    """广播给控制端"""
    dead = []
    data = json.dumps(payload, ensure_ascii=False, default=str)
    for ws in state.control_clients:
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        state.control_clients.discard(ws)


async def _broadcast_scheduler(payload: dict):
    """广播给调度端"""
    dead = []
    data = json.dumps(payload, ensure_ascii=False, default=str)
    for ws in state.scheduler_clients:
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        state.scheduler_clients.discard(ws)


# ── WebSocket 端点 ──

@app.websocket("/ws/control")
async def ws_control(websocket: WebSocket):
    """控制端 WebSocket"""
    await websocket.accept()
    state.control_clients.add(websocket)
    await websocket.send_json({"type": "connected", "role": "control"})
    print(f"[WS] 控制端已连接，当前 {len(state.control_clients)} 个")

    try:
        while True:
            msg = await websocket.receive_json()
            await _handle_control_message(msg)
    except WebSocketDisconnect:
        pass
    finally:
        state.control_clients.discard(websocket)
        print(f"[WS] 控制端断开，当前 {len(state.control_clients)} 个")


@app.websocket("/ws/scheduler")
async def ws_scheduler(websocket: WebSocket):
    """调度端 WebSocket"""
    await websocket.accept()
    state.scheduler_clients.add(websocket)
    await websocket.send_json({"type": "connected", "role": "scheduler"})

    # 发送当前任务列表
    tasks = state.task_manager.get_all_tasks() if state.task_manager else []
    await websocket.send_json({
        "type": "task_list",
        "tasks": [t.model_dump() for t in tasks]
    })

    try:
        while True:
            msg = await websocket.receive_json()
            await _handle_scheduler_message(msg)
    except WebSocketDisconnect:
        pass
    finally:
        state.scheduler_clients.discard(websocket)


@app.websocket("/ws/robot")
async def ws_robot(websocket: WebSocket):
    """
    机器人底层 / d435i 连接的 WebSocket 端点。

    接收：
      - d435i 发来的 control_cmd: { "type": "control_cmd", "steer": 5.0, "speed": 0.3 }
        → 后端直接转发给 TCP 机器人底层，不修改指令
      - 机器人底层状态回传
    """
    await websocket.accept()
    state.robot_ws_clients.add(websocket)
    await websocket.send_json({"type": "connected", "role": "robot"})
    print(f"[WS] 机器人端已连接，当前 {len(state.robot_ws_clients)} 个")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                mtype = msg.get("type", "")

                # ── d435i 发来的实时控制指令 ──
                if mtype == "control_cmd":
                    steer = msg.get("steer")
                    speed = msg.get("speed")
                    source = msg.get("source", "unknown")

                    # 直接转发给 TCP 机器人底层
                    if state.apiservice:
                        await _forward_d435i_control(steer, speed, source)

                    # 同时广播给控制端，让前端显示 d435i 的实时决策
                    await _broadcast_control({
                        "type": "d435i_control",
                        "steer": steer,
                        "speed": speed,
                        "source": source,
                        "timestamp": msg.get("timestamp", time.time())
                    })
                    continue

                # ── d435i 发来的视觉帧 ──
                if mtype == "vision_frame":
                    frame_b64 = msg.get("frame", "")
                    if _is_valid_frame(frame_b64):
                        await _broadcast_vision({"frame": frame_b64, "type": "vision_frame"})
                    else:
                        print(f"[WS/robot] ⚠️ 收到非法视频帧，长度={len(frame_b64) if isinstance(frame_b64, str) else 'N/A'}，已丢弃")
                    continue

                # ── d435i 注册消息 ──
                if mtype == "register":
                    role = msg.get("role", "")
                    print(f"[WS/robot] 注册: {role}")
                    await websocket.send_json({"type": "registered", "role": role})
                    continue

                # ── 机器人底层状态回传 ──
                task_id = int(msg.get("task_id", -1))
                status = str(msg.get("status", "RUNNING"))
                step_id = msg.get("step_id")

                # 广播给前端
                await _broadcast_control({
                    "type": "robot_step_status",
                    "task_id": task_id,
                    "step_id": step_id,
                    "status": status,
                    "detail": msg.get("detail", "")
                })

                # 回执 ack
                await websocket.send_json({
                    "type": "ack",
                    "task_id": task_id,
                    "status": status
                })

            except Exception as e:
                await websocket.send_json({"type": "error", "message": str(e)})

    except WebSocketDisconnect as e:
        print(f"[WS] 机器人端断开: code={e.code}, reason={e.reason}")
    except Exception as e:
        print(f"[WS] 机器人端异常断开: {type(e).__name__}: {e}")
    finally:
        state.robot_ws_clients.discard(websocket)
        print(f"[WS] 机器人端断开，当前 {len(state.robot_ws_clients)} 个")


async def _forward_d435i_control(steer, speed, source):
    """
    将 d435i 的实时控制指令直接转发给 TCP 机器人底层。
    后端不修改、不规划，只做透明转发。
    """
    if not state.apiservice:
        return
    # 构造底层控制消息（与机器人底层协议对齐）
    cmd = {
        "type": "low_level_control",
        "source": source,
        "steer": steer,    # 转向角度 (度)
        "speed": speed,    # 线速度 (m/s)
        "timestamp": time.time()
    }
    # 通过 TCP 发送（4 字节长度前缀 + JSON Body）
    data = json.dumps(cmd, ensure_ascii=False).encode("utf-8")
    state.apiservice.send_raw(data)
    # 不等待回包，d435i 是高频实时控制，不需要确认


async def _handle_control_message(msg: dict):
    """处理控制端消息"""
    mtype = msg.get("type", "")

    if mtype == "command":
        # 高层动作指令（来自前端手动控制）
        action = msg.get("action", "")
        params = msg.get("params", {})
        if state.apiservice:
            state.apiservice.send_action(action, params)

    elif mtype == "task_control":
        # 任务控制
        cmd = msg.get("command", "")
        task_id = msg.get("task_id", "")
        if not state.task_manager:
            return
        if cmd == "start":
            state.task_manager.start_task(task_id)
        elif cmd == "stop":
            state.task_manager.stop_task(task_id)
        elif cmd == "pause":
            state.task_manager.pause_task(task_id)
        elif cmd == "resume":
            state.task_manager.resume_task(task_id)

    elif mtype == "emergency_stop":
        if state.task_manager:
            state.task_manager.emergency_stop()
        if state.apiservice:
            state.apiservice.emergency_stop()

    elif mtype == "heartbeat":
        await _broadcast_control({"type": "heartbeat", "seq": msg.get("seq", 0)})


async def _handle_scheduler_message(msg: dict):
    """处理调度端消息"""
    mtype = msg.get("type", "")

    if mtype == "create_task":
        # 创建任务
        name = msg.get("name", "新任务")
        actions_data = msg.get("actions", [])
        priority_str = msg.get("priority", "NORMAL")
        priority = TaskPriority[priority_str] if priority_str in TaskPriority.__members__ else TaskPriority.NORMAL

        actions = []
        for i, a in enumerate(actions_data):
            atype = a.get("type", "")
            try:
                action_type = ActionType(atype)
            except ValueError:
                action_type = ActionType.WALK_STRAIGHT
            actions.append(Action(
                id=i+1, type=action_type, device=a.get("device", "底盘"),
                params=ActionParams(**a.get("params", {}))
            ))

        task = state.task_manager.create_task(name=name, actions=actions, priority=priority)
        await _broadcast_scheduler({
            "type": "task_created",
            "task": task.model_dump()
        })

    elif mtype == "start_task":
        state.task_manager.start_task(msg.get("task_id", ""))

    elif mtype == "stop_task":
        state.task_manager.stop_task(msg.get("task_id", ""))

    elif mtype == "pause_task":
        state.task_manager.pause_task(msg.get("task_id", ""))

    elif mtype == "resume_task":
        state.task_manager.resume_task(msg.get("task_id", ""))

    elif mtype == "delete_task":
        pass

    elif mtype == "get_tasks":
        tasks = state.task_manager.get_all_tasks()
        await _broadcast_scheduler({
            "type": "task_list",
            "tasks": [t.model_dump() for t in tasks]
        })


# ── HTTP API ──

@app.get("/", response_class=HTMLResponse)
async def root():
    return """
    <html><body style="font-family:Arial;padding:40px">
        <h1>人形机器人控制中心</h1>
        <p>后端服务运行中</p>
        <p>架构：d435i避障 → 后端转发 → TCP → 机器人底层</p>
        <ul>
            <li><a href="/control">控制界面</a></li>
            <li><a href="/scheduler">任务调度</a></li>
        </ul>
    </html>
    """

@app.get("/api/status")
async def api_status():
    """获取当前机器人状态"""
    return {"success": True, "status": state.robot_status.model_dump()}

@app.get("/api/tasks")
async def api_tasks():
    """获取所有任务"""
    if not state.task_manager:
        return {"success": False, "tasks": []}
    tasks = state.task_manager.get_all_tasks()
    return {"success": True, "tasks": [t.model_dump() for t in tasks]}


# ── 入口 ──

if __name__ == "__main__":
    import uvicorn
    # ws_max_size: 允许前后端之间传输大视频帧（单位：字节）
    WS_MAX_SIZE = int(os.getenv("WS_MAX_SIZE", str(8 * 1024 * 1024)))
    uvicorn.run(app, host=WS_HOST, port=WS_PORT, ws_max_size=WS_MAX_SIZE)
