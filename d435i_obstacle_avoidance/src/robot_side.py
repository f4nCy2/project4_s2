#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机器人端一键启动器

把 D435i 电脑上的进程统一起跑：
  1. robot_simulator.py  - 模拟机器人底层（TCP 9090）
  2. obstacle_avoidance*.py - 视觉避障算法（向后端发 control_cmd / vision_frame）

用法：
    cd d435i_obstacle_avoidance
    python src/robot_side.py

环境变量：
    ROBOT_AVOIDANCE       - 启动哪个避障程序：vfh / yolo / none，默认 vfh
    ROBOT_ENABLE_SIM      - 是否启动 robot_simulator，默认 true
    其余环境变量（TCP_HOST 等）会透传给子进程
"""

import os
import sys
import time
import signal
import subprocess
import threading
from pathlib import Path


_running = True
_shutting_down = False


ROBOT_AVOIDANCE = os.getenv("ROBOT_AVOIDANCE", "vfh").lower()
ROBOT_ENABLE_SIM = os.getenv("ROBOT_ENABLE_SIM", "true").lower() == "true"
AUTO_RESTART = os.getenv("ROBOT_AUTO_RESTART", "true").lower() == "true"

SRC_DIR = Path(__file__).resolve().parent
ROOT_DIR = SRC_DIR.parent


class ProcessWrapper:
    def __init__(self, name, cmd):
        self.name = name
        self.cmd = cmd
        self.proc = None
        self._stop = False
        self._thread = None

    def start(self):
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        # 让子进程知道自己是被 launcher 启动的，避免重复嵌套
        env["ROBOT_SIDE_LAUNCHED"] = "1"
        self.proc = subprocess.Popen(
            self.cmd,
            cwd=str(ROOT_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            # Windows 需要这个才能正确发信号终止子进程
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        self._thread = threading.Thread(target=self._log_loop, daemon=True)
        self._thread.start()
        print(f"[RobotSide] ✅ 启动 {self.name} (pid={self.proc.pid})")

    def _log_loop(self):
        if not self.proc or not self.proc.stdout:
            return
        for line in self.proc.stdout:
            line = line.rstrip()
            if line:
                print(f"[{self.name}] {line}")

    def is_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def terminate(self):
        self._stop = True
        if not self.proc:
            return
        try:
            if sys.platform == "win32":
                self.proc.terminate()
            else:
                # Unix 子进程大多捕获 SIGINT（KeyboardInterrupt）
                self.proc.send_signal(signal.SIGINT)
            # 等待 3 秒优雅退出
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                print(f"[RobotSide] ⚠️ {self.name} 未响应，强制结束")
                self.proc.kill()
                self.proc.wait(timeout=3)
        except Exception as e:
            print(f"[RobotSide] 结束 {self.name} 时出错: {e}")


def build_processes():
    processes = []

    if ROBOT_ENABLE_SIM:
        processes.append(ProcessWrapper(
            "RobotSim",
            [sys.executable, "-u", str(SRC_DIR / "robot_simulator.py")]
        ))

    if ROBOT_AVOIDANCE == "vfh":
        processes.append(ProcessWrapper(
            "AvoidVFH",
            [sys.executable, "-u", str(SRC_DIR / "obstacle_avoidance.py")]
        ))
    elif ROBOT_AVOIDANCE == "yolo":
        processes.append(ProcessWrapper(
            "AvoidYOLO",
            [sys.executable, "-u", str(SRC_DIR / "obstacle_avoidance_yolo.py")]
        ))
    elif ROBOT_AVOIDANCE != "none":
        print(f"[RobotSide] ⚠️ 未知 ROBOT_AVOIDANCE={ROBOT_AVOIDANCE}，已跳过避障程序")

    return processes


def main():
    if os.getenv("ROBOT_SIDE_LAUNCHED") == "1":
        # 防止 launcher 被嵌套调用
        print("[RobotSide] ❌ 检测到嵌套启动，请直接运行 robot_side.py")
        return 1

    processes = build_processes()
    if not processes:
        print("[RobotSide] ❌ 没有要启动的进程，请检查环境变量")
        return 1

    print("=" * 60)
    print("  机器人端一键启动器")
    print("=" * 60)
    print(f"  避障程序: {ROBOT_AVOIDANCE}")
    print(f"  底层模拟: {'开启' if ROBOT_ENABLE_SIM else '关闭'}")
    print(f"  工作目录: {ROOT_DIR}")
    print("=" * 60)

    for p in processes:
        p.start()

    def shutdown(signum, frame):
        global _running, _shutting_down
        if _shutting_down:
            return
        _shutting_down = True
        _running = False
        if signum == signal.SIGINT:
            # Ctrl+C 会发给整个前台进程组，子进程会自然收到，这里只等待
            print("\n[RobotSide] 收到 Ctrl+C，等待子进程退出...")
        else:
            # SIGTERM 不会自动传播给子进程，需要主动发送
            print("\n[RobotSide] 收到 SIGTERM，正在关闭所有子进程...")
            for p in processes:
                p.terminate()

    # 重置可能继承的 SIG_IGN（例如被 nohup/后台启动时）
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while _running:
            time.sleep(0.5)
            # 检查子进程健康
            for p in processes:
                if not p.is_alive():
                    if _shutting_down:
                        continue
                    if AUTO_RESTART and not p._stop:
                        print(f"[RobotSide] ⚠️ {p.name} 已退出，尝试重启...")
                        p.start()
                    else:
                        print(f"[RobotSide] ⚠️ {p.name} 已退出")
                        # 如果是核心进程退出，结束全部
                        if p.name == "RobotSim":
                            print("[RobotSide] 核心进程退出，关闭整个机器人端")
                            shutdown(None, None)
                            return 1

        # 正常退出时等待所有子进程结束
        print("[RobotSide] 等待子进程清理...")
        for p in processes:
            if p.is_alive():
                p.proc.wait(timeout=5)
    except Exception as e:
        print(f"[RobotSide] 运行异常: {e}")
        shutdown(None, None)
        return 1


if __name__ == "__main__":
    sys.exit(main())
