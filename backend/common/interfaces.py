"""抽象接口定义"""
from abc import ABC, abstractmethod
from typing import Callable, Optional, List
from .models import RobotStatus, Task, Action, Command


class IStatusManager(ABC):
    """状态管理接口"""
    
    @abstractmethod
    def update_robot_status(self, status: RobotStatus) -> None:
        """更新机器人状态"""
        pass
    
    @abstractmethod
    def get_robot_status(self) -> RobotStatus:
        """获取当前状态"""
        pass
    
    @abstractmethod
    def subscribe_status(self, callback: Callable[[RobotStatus], None]) -> None:
        """订阅状态更新"""
        pass


class ITaskPlanner(ABC):
    """任务规划接口"""
    
    @abstractmethod
    def create_task(self, name: str, actions: List[Action], priority: str = "NORMAL") -> Task:
        """创建任务"""
        pass
    
    @abstractmethod
    def start_task(self, task_id: str) -> bool:
        """启动任务"""
        pass
    
    @abstractmethod
    def stop_task(self, task_id: str) -> bool:
        """停止任务"""
        pass
    
    @abstractmethod
    def pause_task(self, task_id: str) -> bool:
        """暂停任务"""
        pass
    
    @abstractmethod
    def resume_task(self, task_id: str) -> bool:
        """恢复任务"""
        pass
    
    @abstractmethod
    def get_task(self, task_id: str) -> Optional[Task]:
        """获取任务"""
        pass
    
    @abstractmethod
    def get_all_tasks(self) -> List[Task]:
        """获取所有任务"""
        pass
    
    @abstractmethod
    def get_current_task(self) -> Optional[Task]:
        """获取当前任务"""
        pass
    
    @abstractmethod
    def subscribe_task_events(self, callback: Callable) -> None:
        """订阅任务事件"""
        pass


class ICommunication(ABC):
    """通信接口"""
    
    @abstractmethod
    def connect(self) -> bool:
        """建立连接"""
        pass
    
    @abstractmethod
    def disconnect(self) -> None:
        """断开连接"""
        pass
    
    @abstractmethod
    def is_connected(self) -> bool:
        """检查连接状态"""
        pass
    
    @abstractmethod
    def send(self, data: bytes) -> bool:
        """发送数据"""
        pass
    
    @abstractmethod
    def receive(self) -> Optional[bytes]:
        """接收数据"""
        pass
    
    @abstractmethod
    def on_message(self, callback: Callable[[bytes], None]) -> None:
        """注册消息回调"""
        pass


class ICommandSender(ABC):
    """指令发送接口"""
    
    @abstractmethod
    def send_command(self, command: Command) -> bool:
        """发送指令"""
        pass
    
    @abstractmethod
    def send_action(self, action_type: str, params: dict) -> bool:
        """发送动作"""
        pass
    
    @abstractmethod
    def emergency_stop(self) -> bool:
        """紧急停止"""
        pass


class IHeartbeatManager(ABC):
    """心跳管理接口"""
    
    @abstractmethod
    def start(self) -> None:
        """启动心跳"""
        pass
    
    @abstractmethod
    def stop(self) -> None:
        """停止心跳"""
        pass
    
    @abstractmethod
    def is_alive(self) -> bool:
        """检查对端是否存活"""
        pass
