"""任务管理器：任务生命周期 + 优先级队列调度 + 自动调度（v2.1）"""
import uuid
import asyncio
from datetime import datetime
from typing import List, Optional, Callable, Dict
from backend.common.models import Task, Action, TaskStatus, TaskPriority
from backend.common.enums import TaskStatus as TaskStatusEnum
from backend.task_planner.action_scheduler import ActionScheduler
from backend.task_planner.motion_planner import MotionPlanner


class TaskManager:
    """任务管理器：创建、队列、启动、暂停、恢复、停止
    
    v2.1 改造：
      - 队列按优先级排序（EMERGENCY > HIGH > NORMAL > LOW）
      - 创建任务后自动调度（空闲时立即执行）
      - 任务完成/失败/被抢占后自动调度下一个
      - 新增队列状态广播
    """

    def __init__(self, scheduler: ActionScheduler):
        self._scheduler = scheduler
        self._tasks: Dict[str, Task] = {}
        self._queue: List[str] = []  # 待执行的任务 ID 队列（按优先级排序）
        self._current_task_id: Optional[str] = None
        self._subscribers: List[Callable] = []
        self._auto_schedule = True  # 自动调度开关
        self._scheduler.set_callbacks(
            on_step=self._on_action_step,
            on_complete=self._on_task_complete,
            on_fail=self._on_task_fail,
            on_preempted=self._on_task_preempted
        )

    # ── 订阅 ──
    def subscribe_task_events(self, callback: Callable) -> None:
        self._subscribers.append(callback)

    def _notify(self, event: str, task: Optional[Task] = None, action_index: int = 0, extra: dict = None):
        """通知所有订阅者，支持 extra 字段"""
        for cb in self._subscribers:
            try:
                payload = {
                    "event": event,
                    "action_index": action_index,
                    "total_actions": len(task.actions) if task else 0
                }
                if task:
                    payload["task_id"] = task.id
                    payload["task_name"] = task.name
                if extra:
                    payload.update(extra)
                cb(payload)
            except Exception:
                pass

    def _notify_queue_changed(self):
        """广播队列状态变更"""
        queue_tasks = []
        for tid in self._queue:
            t = self._tasks.get(tid)
            if t and t.status in (TaskStatus.PENDING, TaskStatus.PAUSED):
                queue_tasks.append(t.model_dump(mode='json'))
        self._notify("queue_changed", extra={
            "queue": queue_tasks,
            "queue_length": len(queue_tasks)
        })

    # ── 队列排序 ──
    def _sort_queue(self):
        """按优先级排序队列（高优先级在前）"""
        self._queue.sort(key=lambda tid: self._tasks[tid].priority.value, reverse=True)

    def _get_next_task(self) -> Optional[str]:
        """从队列中获取最高优先级的可调度任务"""
        valid = [tid for tid in self._queue if self._tasks[tid].status in (TaskStatus.PENDING, TaskStatus.PAUSED)]
        if not valid:
            return None
        valid.sort(key=lambda tid: self._tasks[tid].priority.value, reverse=True)
        return valid[0]

    def _schedule_next(self) -> Optional[str]:
        """调度下一个最高优先级任务（支持抢占当前低优先级任务）"""
        next_id = self._get_next_task()
        if not next_id:
            return None
        current = self.get_current_task()
        if current and self._tasks[next_id].priority.value <= current.priority.value:
            # 当前任务优先级更高或相等，不抢占
            return None
        return self.start_task(next_id)

    # ── 任务 CRUD ──
    def create_task(self, name: str, actions: List[Action],
                    priority: TaskPriority = TaskPriority.NORMAL,
                    auto_start: Optional[bool] = None) -> Task:
        """创建任务。

        Args:
            auto_start: 是否创建后立即执行。None 表示跟随 TaskManager
                的自动调度开关；True/False 显式覆盖。
        """
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
        self._sort_queue()
        self._notify("created", task)
        self._notify_queue_changed()
        # 自动调度：空闲时立即执行
        should_auto = self._auto_schedule if auto_start is None else auto_start
        if should_auto:
            self._schedule_next()
        return task

    def set_auto_schedule(self, enabled: bool) -> None:
        """设置是否开启自动调度"""
        self._auto_schedule = enabled
        if enabled:
            self._schedule_next()

    def start_scheduling(self) -> Optional[str]:
        """手动触发调度：从等待队列中选择最高优先级任务执行"""
        return self._schedule_next()

    def is_auto_schedule(self) -> bool:
        return self._auto_schedule

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> List[Task]:
        return list(self._tasks.values())

    def get_current_task(self) -> Optional[Task]:
        if self._current_task_id:
            return self._tasks.get(self._current_task_id)
        return None

    def get_queue(self) -> List[str]:
        """返回当前队列（按优先级排序）"""
        return list(self._queue)

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
        self._notify_queue_changed()
        return True

    def stop_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False
        if task_id == self._current_task_id:
            self._scheduler.stop()
        task.status = TaskStatus.CANCELLED
        self._notify("stopped", task)
        self._notify_queue_changed()
        if self._auto_schedule:
            self._schedule_next()
        return True

    def pause_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.status != TaskStatus.RUNNING:
            return False
        self._scheduler.pause()
        task.status = TaskStatus.PAUSED
        self._notify("paused", task)
        self._notify_queue_changed()
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
        self._notify_queue_changed()

    # ── 动作事件转发（闭环确认）──
    def on_action_event(self, msg: dict):
        """接收机器人 action_event 消息，转发给 ActionScheduler 进行闭环确认"""
        self._scheduler.on_action_event(msg)

    # ── 回调 ──
    def _on_action_step(self, task: Task, action_idx: int):
        self._notify("running", task, action_idx)

    def _on_task_complete(self, task: Task):
        task.status = TaskStatus.COMPLETED
        task.completed_at = datetime.now().isoformat()
        self._current_task_id = None
        self._notify("completed", task, len(task.actions))
        self._notify_queue_changed()
        # 自动调度下一个
        if self._auto_schedule:
            self._schedule_next()

    def _on_task_fail(self, task: Task, action):
        task.status = TaskStatus.FAILED
        self._current_task_id = None
        self._notify("failed", task)
        self._notify_queue_changed()
        # 失败后也自动调度下一个
        if self._auto_schedule:
            self._schedule_next()

    def _on_task_preempted(self, task: Task):
        """任务被高优先级抢占"""
        task.status = TaskStatus.CANCELLED
        # 仅当抢占的是当前任务时才清空 current_task_id
        # （start_task 已经提前将 current_task_id 设为新任务）
        if self._current_task_id == task.id:
            self._current_task_id = None
        self._notify("preempted", task)
        self._notify_queue_changed()
        # 抢占后自动调度下一个（通常抢占者自己已经在运行）
        if self._auto_schedule:
            self._schedule_next()
