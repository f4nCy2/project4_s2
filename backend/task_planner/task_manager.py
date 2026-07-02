"""任务管理器：任务生命周期 + 队列管理"""
import uuid
import asyncio
from datetime import datetime
from typing import List, Optional, Callable, Dict
from backend.common.models import Task, Action, TaskStatus, TaskPriority
from backend.common.enums import TaskStatus as TaskStatusEnum
from backend.task_planner.action_scheduler import ActionScheduler
from backend.task_planner.motion_planner import MotionPlanner


class TaskManager:
    """任务管理器：创建、队列、启动、暂停、恢复、停止"""

    def __init__(self, scheduler: ActionScheduler):
        self._scheduler = scheduler
        self._tasks: Dict[str, Task] = {}
        self._queue: List[str] = []  # 待执行的任务 ID 队列
        self._current_task_id: Optional[str] = None
        self._subscribers: List[Callable] = []
        self._scheduler.set_callbacks(
            on_step=self._on_action_step,
            on_complete=self._on_task_complete,
            on_fail=self._on_task_fail
        )

    # ── 订阅 ──
    def subscribe_task_events(self, callback: Callable) -> None:
        self._subscribers.append(callback)

    def _notify(self, event: str, task: Task, action_index: int = 0):
        for cb in self._subscribers:
            try:
                cb({
                    "event": event,
                    "task_id": task.id,
                    "task_name": task.name,
                    "action_index": action_index,
                    "total_actions": len(task.actions)
                })
            except Exception:
                pass

    # ── 任务 CRUD ──
    def create_task(self, name: str, actions: List[Action],
                    priority: TaskPriority = TaskPriority.NORMAL) -> Task:
        task = Task(
            id=f"task_{uuid.uuid4().hex[:8]}",
            name=name,
            priority=priority,
            actions=MotionPlanner.build_sequence(*actions),
            created_at=datetime.now().isoformat(),
            status=TaskStatus.PENDING
        )
        self._tasks[task.id] = task
        self._queue.append(task.id)
        self._notify("created", task)
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> List[Task]:
        return list(self._tasks.values())

    def get_current_task(self) -> Optional[Task]:
        if self._current_task_id:
            return self._tasks.get(self._current_task_id)
        return None

    # ── 任务控制 ──
    def start_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.status not in (TaskStatus.PENDING, TaskStatus.PAUSED):
            return False
        if task_id in self._queue:
            self._queue.remove(task_id)
        self._current_task_id = task_id
        asyncio.create_task(self._scheduler.run_task(task))
        self._notify("started", task)
        return True

    def stop_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False
        if task_id == self._current_task_id:
            self._scheduler.stop()
        task.status = TaskStatus.CANCELLED
        self._notify("stopped", task)
        return True

    def pause_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.status != TaskStatus.RUNNING:
            return False
        self._scheduler.pause()
        task.status = TaskStatus.PAUSED
        self._notify("paused", task)
        return True

    def resume_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.status != TaskStatus.PAUSED:
            return False
        self._scheduler.resume()
        task.status = TaskStatus.RUNNING
        self._notify("resumed", task)
        return True

    def emergency_stop(self) -> None:
        """紧急停止所有"""
        if self._current_task_id:
            self._scheduler.stop()
            task = self._tasks.get(self._current_task_id)
            if task:
                task.status = TaskStatus.CANCELLED
                self._notify("stopped", task)
        self._current_task_id = None

    # ── 回调 ──
    def _on_action_step(self, task: Task, action_idx: int):
        self._notify("running", task, action_idx)

    def _on_task_complete(self, task: Task):
        task.status = TaskStatus.COMPLETED
        task.completed_at = datetime.now().isoformat()
        self._current_task_id = None
        self._notify("completed", task, len(task.actions))
        # 自动执行队列中下一个
        if self._queue:
            next_id = self._queue.pop(0)
            self.start_task(next_id)

    def _on_task_fail(self, task: Task, action):
        task.status = TaskStatus.FAILED
        self._current_task_id = None
        self._notify("failed", task)
