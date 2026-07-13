"""动作调度器：优先级抢占 + 中断切换"""
import asyncio
import time
from typing import List, Optional, Callable
from backend.common.models import Action, Task, TaskStatus, TaskPriority, ActionType


class ActionScheduler:
    """动作调度器：管理当前任务的动作队列，支持优先级抢占"""

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

    def set_callbacks(self, on_step=None, on_complete=None, on_fail=None):
        self._on_step = on_step
        self._on_complete = on_complete
        self._on_fail = on_fail

    async def run_task(self, task: Task) -> None:
        """运行任务的动作序列"""
        if self._running and task.priority.value < self._current_task.priority.value:
            # 低优先级不抢占
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
        actions = self._current_task.actions
        while self._running and self._current_action_idx < len(actions):
            if self._paused:
                await asyncio.sleep(0.1)
                continue
            action = actions[self._current_action_idx]
            success = await self._execute_action(action)
            if not success and not self._paused:
                # 失败处理
                if self._on_fail:
                    self._on_fail(self._current_task, action)
                break
            self._current_action_idx += 1
            if self._on_step:
                self._on_step(self._current_task, self._current_action_idx)
        # 任务结束
        if self._running and not self._paused:
            self._current_task.status = TaskStatus.COMPLETED
            if self._on_complete:
                self._on_complete(self._current_task)
        self._running = False

    async def _execute_action(self, action: Action) -> bool:
        """执行单个动作，发送指令并等待确认（模拟）"""
        if self._on_step:
            self._on_step(self._current_task, action.id)
        
        # 特殊处理：避障模式切换
        if action.type == ActionType.AVOID_OBSTACLE:
            enable = not action.params.emergency  # emergency=True 表示停止
            if enable:
                # 启动 d435i 避障模式：后端不再发送高层动作，让 d435i 接管
                print(f"[ActionScheduler] 启动 d435i 避障模式")
                # 发送模式切换指令给机器人底层
                if self._command_sender:
                    await self._send_mode_switch("avoidance", True)
            else:
                print(f"[ActionScheduler] 停止 d435i 避障模式")
                if self._command_sender:
                    await self._send_mode_switch("avoidance", False)
            return True
        
        # 普通高层动作：发送给机器人底层执行
        if self._command_sender:
            from backend.common.models import Command
            cmd = Command(
                type="command",
                action=action.type.value,
                params=action.params.model_dump(exclude_none=True),
                seq=action.id,
                priority="NORMAL",
                task_id=self._current_task.id
            )
            self._command_sender.send_command(cmd)
        # 模拟执行时间（实际应由机器人回传确认）
        await asyncio.sleep(1.0)
        return True

    async def _send_mode_switch(self, mode: str, enable: bool):
        """发送模式切换指令给机器人底层"""
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
        """中断当前任务"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._current_task:
            self._current_task.status = TaskStatus.CANCELLED

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
