"""动作调度器：闭环确认 + 优先级抢占 + 中断切换（v2.1 — 优先级调度版）"""
import asyncio
import time
from typing import List, Optional, Callable, Dict
from backend.common.models import Action, Task, TaskStatus, TaskPriority
from backend.common.enums import TaskStatus as TaskStatusEnum


class ActionScheduler:
    """动作调度器：管理当前任务的动作队列，支持闭环确认、优先级抢占
    
    v2.1 改造：
      - 修复：动作失败后不覆盖为 COMPLETED
      - 新增：on_preempted 回调，支持抢占事件通知
      - 优先级抢占：高优先级任务可中断低优先级任务
    """

    # 动作超时系数：预估时间 × 系数 + 基础余量
    TIMEOUT_FACTOR = 2.0
    TIMEOUT_BASE = 5.0

    def __init__(self, command_sender):
        self._command_sender = command_sender
        self._current_task: Optional[Task] = None
        self._current_action_idx: int = 0
        self._running = False
        self._paused = False
        self._task: Optional[asyncio.Task] = None
        self._on_step: Optional[Callable] = None
        self._on_complete: Optional[Callable] = None
        self._on_fail: Optional[Callable] = None
        self._on_preempted: Optional[Callable] = None

        # 闭环确认：等待 action_event
        self._action_waiter: Optional[asyncio.Event] = None
        self._action_result: Optional[Dict] = None
        self._action_lock = asyncio.Lock()

    def set_callbacks(self, on_step=None, on_complete=None, on_fail=None, on_preempted=None):
        self._on_step = on_step
        self._on_complete = on_complete
        self._on_fail = on_fail
        self._on_preempted = on_preempted

    # ═══════════════════════════════════════════════════════
    # 外部接口：动作事件处理（由 APIService 回调触发）
    # ═══════════════════════════════════════════════════════

    def on_action_event(self, msg: dict):
        """接收来自机器人的 action_event 消息"""
        event = msg.get("event", "")
        task_id = msg.get("task_id", "")
        action_id = msg.get("action_id", 0)
        progress = msg.get("progress", 0.0)
        detail = msg.get("detail", "")

        # 验证：是否当前执行的动作
        if not self._current_task:
            return
        if task_id != self._current_task.id:
            return
        current_action = self.get_current_action()
        if not current_action or action_id != current_action.id:
            return

        # 更新任务进度
        self._current_task.current_action_index = self._current_action_idx
        current_action._progress = progress
        current_action._detail = detail

        if event == "started":
            print(f"[ActionScheduler] 动作开始: task={task_id} action={action_id} type={msg.get('action_type')}")
            if self._on_step:
                self._on_step(self._current_task, self._current_action_idx)

        elif event == "progress":
            if self._on_step:
                self._on_step(self._current_task, self._current_action_idx)

        elif event == "completed":
            print(f"[ActionScheduler] 动作完成: task={task_id} action={action_id}")
            self._signal_action_done(True, msg)

        elif event == "failed":
            print(f"[ActionScheduler] ⚠️ 动作失败: task={task_id} action={action_id} detail={detail}")
            self._signal_action_done(False, msg)

    def _signal_action_done(self, success: bool, result: dict):
        """同步设置动作结果并唤醒等待者"""
        self._action_result = {"success": success, **result}
        if self._action_waiter:
            try:
                self._action_waiter.set()
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════
    # 任务执行主循环
    # ═══════════════════════════════════════════════════════

    async def run_task(self, task: Task) -> bool:
        """运行任务的动作序列。高优先级任务会抢占低优先级任务。"""
        if self._running and task.priority.value < self._current_task.priority.value:
            # 低优先级不抢占，返回 False
            return False
        # 抢占当前任务
        if self._running:
            await self._interrupt_current()
        self._current_task = task
        self._current_action_idx = 0
        self._running = True
        self._paused = False
        task.status = TaskStatus.RUNNING
        self._task = asyncio.create_task(self._execute_loop())
        return True

    async def _execute_loop(self):
        """任务执行循环：依次执行动作，闭环确认"""
        actions = self._current_task.actions
        has_failed = False
        while self._running and self._current_action_idx < len(actions):
            if self._paused:
                await asyncio.sleep(0.1)
                continue
            action = actions[self._current_action_idx]
            success = await self._execute_action_with_feedback(action)
            if not success and not self._paused:
                has_failed = True
                # 统一在循环外处理失败回调，避免重复
                if self._on_fail:
                    self._on_fail(self._current_task, action)
                break
            self._current_action_idx += 1
            if self._on_step:
                self._on_step(self._current_task, self._current_action_idx)
        # 任务结束：只有未失败时才标记为 COMPLETED
        if self._running and not self._paused and not has_failed:
            self._current_task.status = TaskStatus.COMPLETED
            if self._on_complete:
                self._on_complete(self._current_task)
        self._running = False

    async def _execute_action_with_feedback(self, action: Action) -> bool:
        """执行单个动作，发送指令并等待机器人闭环确认"""
        if self._on_step:
            self._on_step(self._current_task, action.id)

        # 特殊处理：避障模式切换
        if action.type.value == "avoid_obstacle":
            enable = not action.params.emergency
            if enable:
                print(f"[ActionScheduler] 启动 d435i 避障模式")
                if self._command_sender:
                    await self._send_mode_switch("avoidance", True)
            else:
                print(f"[ActionScheduler] 停止 d435i 避障模式")
                if self._command_sender:
                    await self._send_mode_switch("avoidance", False)
            return True

        # 发送动作指令给机器人
        if self._command_sender:
            from backend.common.models import Command
            cmd = Command(
                type="command",
                action=action.type.value,
                params=action.params.model_dump(exclude_none=True),
                seq=action.id,
                priority="NORMAL",
                task_id=self._current_task.id,
                action_id=action.id
            )
            self._command_sender.send_command(cmd)

        # 等待闭环确认
        return await self._wait_for_action_completion(action)

    async def _wait_for_action_completion(self, action: Action) -> bool:
        """等待机器人回传动作完成确认"""
        estimated = self._estimate_duration(action)
        timeout = estimated * self.TIMEOUT_FACTOR + self.TIMEOUT_BASE

        async with self._action_lock:
            self._action_waiter = asyncio.Event()
            self._action_result = None

        print(f"[ActionScheduler] 等待动作完成: action={action.id} type={action.type.value} "
              f"预估={estimated:.1f}s 超时={timeout:.1f}s")

        try:
            await asyncio.wait_for(self._action_waiter.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            print(f"[ActionScheduler] ⚠️ 动作超时: action={action.id} type={action.type.value}")
            # 超时返回 False，失败回调由 _execute_loop 统一处理，避免重复
            return False

        # 检查结果
        async with self._action_lock:
            result = self._action_result
            self._action_waiter = None
            self._action_result = None

        if result is None:
            return False

        return result.get("success", False)

    def _estimate_duration(self, action: Action) -> float:
        """预估动作执行时间"""
        p = action.params
        if action.type.value == "walk_straight":
            d, s = p.distance or 2.0, p.speed or 0.8
            return d / s if s > 0 else 2.0
        elif action.type.value == "turn_in_place":
            a, s = p.angle or 45.0, p.speed or 0.3
            return a / (s * 60) if s > 0 else 1.0
        elif action.type.value == "turn_walk":
            d, s = p.distance or 1.0, p.speed or 0.6
            return d / s if s > 0 else 2.0
        elif action.type.value == "walk_backward":
            d, s = p.distance or 1.0, p.speed or 0.5
            return d / s if s > 0 else 2.0
        elif action.type.value == "sidestep":
            d, s = p.distance or 0.5, p.speed or 0.3
            return (d / s if s > 0 else 2.0) + 4.0
        elif action.type.value == "avoid_obstacle":
            return 5.0
        elif action.type.value == "stop":
            return 0.5
        return 2.0

    async def _send_mode_switch(self, mode: str, enable: bool):
        """发送模式切换指令"""
        import json
        cmd = {
            "type": "mode_switch",
            "mode": mode,
            "enable": enable,
            "timestamp": time.time()
        }
        data = json.dumps(cmd, ensure_ascii=False).encode("utf-8")
        if hasattr(self._command_sender, '_socket'):
            self._command_sender._socket.send(data)
        elif hasattr(self._command_sender, 'send_raw'):
            self._command_sender.send_raw(data)

    async def _interrupt_current(self) -> None:
        """中断当前任务（被高优先级任务抢占）"""
        self._running = False
        if self._action_waiter:
            self._action_waiter.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._current_task:
            self._current_task.status = TaskStatus.CANCELLED
            if self._on_preempted:
                self._on_preempted(self._current_task)

    def pause(self) -> None:
        self._paused = True
        if self._current_task:
            self._current_task.status = TaskStatus.PAUSED

    def resume(self) -> None:
        self._paused = False
        if self._current_task:
            self._current_task.status = TaskStatus.RUNNING

    def stop(self) -> None:
        asyncio.create_task(self._interrupt_current())

    def get_current_action(self) -> Optional[Action]:
        if not self._current_task or self._current_action_idx >= len(self._current_task.actions):
            return None
        return self._current_task.actions[self._current_action_idx]

    def get_progress(self) -> tuple:
        """返回 (current_idx, total)"""
        if not self._current_task:
            return 0, 0
        return self._current_action_idx, len(self._current_task.actions)
