#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D435i 避障验证程序 (修正版 + 后端集成)
修复: 箭头方向、俯角补偿符号、栅格图等比显示
新增: 通过 WebSocket 向后端发送实时控制指令 (steer/speed)
"""

import pyrealsense2 as rs
import numpy as np
import cv2
import math
import time
import json
import base64
import threading
from typing import Optional


# ========================== 后端通信配置 ==========================
import os

class BackendConfig:
    """WebSocket 后端地址。支持环境变量覆盖，方便跨机部署。"""
    # 后端 /ws/robot 端点，跨机时改成主控电脑 IP，例如 ws://192.168.1.100:8080/ws/robot
    WS_URL = os.getenv("D435I_BACKEND_URL", "ws://127.0.0.1:8080/ws/robot")
    ENABLED = os.getenv("D435I_BACKEND_ENABLED", "true").lower() != "false"
    SEND_INTERVAL = float(os.getenv("D435I_SEND_INTERVAL", "0.1"))  # 最少发送间隔 (s)


class BackendClient:
    """WebSocket 客户端：将 d435i 的实时控制指令发送给后端"""

    def __init__(self, url: str, enabled: bool = True):
        self.url = url
        self.enabled = enabled
        self._ws = None
        self._connected = False
        self._last_send = 0.0
        self._thread = None
        self._running = False
        self._loop = None          # 事件循环引用
        self._send_queue = None    # asyncio.Queue，用于线程安全发送
        self._last_frame_time = 0.0  # 帧发送限流

    def start(self):
        if not self.enabled:
            print("[BackendClient] 后端发送已禁用")
            return
        self._running = True
        self._thread = threading.Thread(target=self._connect_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._connected = False

    def _connect_loop(self):
        """使用 asyncio 在线程中运行 WebSocket 客户端"""
        import asyncio
        import websockets

        async def send_worker():
            """后台发送任务：从队列中取出消息并发送"""
            while self._running:
                try:
                    msg = await asyncio.wait_for(self._send_queue.get(), timeout=1.0)
                    if self._ws and self._connected:
                        await self._ws.send(msg)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    print(f"[BackendClient] 发送失败: {e}")
                    self._connected = False

        async def run():
            self._loop = asyncio.get_running_loop()
            self._send_queue = asyncio.Queue(maxsize=10)
            asyncio.create_task(send_worker())
            while self._running:
                try:
                    async with websockets.connect(self.url, ping_interval=None) as ws:
                        self._ws = ws
                        self._connected = True
                        print(f"[BackendClient] ✅ 已连接后端: {self.url}")
                        # 发送注册消息
                        await ws.send(json.dumps({"type": "register", "role": "d435i_vision"}))
                        # 保持连接，只发送不接收
                        while self._running:
                            await asyncio.sleep(1.0)
                except Exception as e:
                    self._connected = False
                    print(f"[BackendClient] ❌ 连接断开: {e}，3s 后重连")
                    await asyncio.sleep(3)

        asyncio.run(run())

    def _enqueue_latest(self, msg: str, tag: str):
        """在线程安全地入队；队列满时丢弃最旧消息，优先保留最新帧/指令。"""
        if not self._loop or not self._send_queue:
            return

        def _put_now():
            try:
                self._send_queue.put_nowait(msg)
            except Exception:
                # 队列满时移除最旧一条，再塞入最新一条，避免持续积压导致画面卡住
                try:
                    _ = self._send_queue.get_nowait()
                    self._send_queue.put_nowait(msg)
                except Exception as e:
                    print(f"[BackendClient] {tag} 入队失败: {e}")

        self._loop.call_soon_threadsafe(_put_now)

    def send_frame(self, frame_b64: str):
        """发送视觉帧给后端（线程安全，限流 5fps）"""
        if not self.enabled or not self._connected or not self._ws or not self._loop:
            return
        now = time.time()
        if now - self._last_frame_time < 0.2:  # 最多 5fps
            return
        self._last_frame_time = now
        msg = json.dumps({
            "type": "vision_frame",
            "frame": frame_b64,
            "timestamp": now
        })
        self._enqueue_latest(msg, "视频帧")

    def send_control(self, steer: float, speed: float, obstacle_dist: Optional[float] = None):
        """发送实时控制指令（线程安全）"""
        if not self.enabled or not self._connected or not self._ws or not self._loop:
            return
        now = time.time()
        if now - self._last_send < BackendConfig.SEND_INTERVAL:
            return
        self._last_send = now
        msg = json.dumps({
            "type": "control_cmd",
            "source": "d435i_vfh",
            "steer": round(steer, 2) if steer is not None else None,
            "speed": round(speed, 3),
            "obstacle_dist": round(obstacle_dist, 2) if obstacle_dist is not None else None,
            "front_distance": round(obstacle_dist, 2) if obstacle_dist is not None else None,
            "timestamp": now
        })
        self._enqueue_latest(msg, "控制指令")


# ========================== 用户可调参数 ==========================
class Config:
    # --- 相机安装参数 ---
    CAM_HEIGHT = 0.45          # 相机离地面高度 (米)
    CAM_TILT   = 10.0          # 【正值】= 向下俯角(度)。如相机水平则填 0，向下看 10° 填 10

    # --- 深度处理参数 ---
    DEPTH_WIDTH  = 640
    DEPTH_HEIGHT = 480
    DEPTH_FPS    = 30
    MAX_DEPTH    = 4.0         # 最大有效深度 (米)
    MIN_DEPTH    = 0.2         # 过滤相机自身噪声

    # --- 栅格地图参数 ---
    GRID_RES   = 0.05            # 5cm/格
    GRID_SIZE  = 80              # 80x80，覆盖前方4m x 左右各2m
    ROBOT_R    = 0.25            # 机器人半径 (米)，用于膨胀
    SAFE_DIST  = 0.50            # 前瞻安全距离 (米)

    # --- VFH 决策参数 ---
    NUM_SECTORS = 36             # -90°~+90°，每扇区5°
    SECTOR_MIN_WIDTH = 3         # 最窄允许山谷宽度 (3*5°=15°)
    TARGET_ANGLE = 0.0           # 默认目标方向：正前方

    # --- 速度规划 ---
    V_FAST  = 0.50
    V_MID   = 0.30
    V_SLOW  = 0.15
    V_BACK  = -0.15

    # --- 可视化 ---
    GRID_VIS_SIZE = 480          # 栅格图等比显示尺寸

    # --- 前方距离估计（用于前端显示）---
    FRONT_ROI_X_RATIO = 0.22     # 前方区域宽度占图像宽度比例
    FRONT_ROI_Y_TOP_RATIO = 0.38 # 前方区域上边界（占高度比例）
    FRONT_ROI_Y_BOT_RATIO = 0.86 # 前方区域下边界（占高度比例）
    FRONT_MIN_VALID = 0.15       # 有效最小距离(m)
    FRONT_MAX_VALID = 3.5        # 有效最大距离(m)
    FRONT_PERCENTILE = 30        # 抗噪分位数（比最小值稳）
    FRONT_MEDIAN_WINDOW = 5      # 中值窗口长度
    FRONT_EMA_ALPHA = 0.35       # EMA平滑系数
    FRONT_MAX_JUMP = 0.60        # 单帧最大变化限幅(m)


class FrontDistanceEstimator:
    """前方距离稳态估计：ROI分位数 + 中值窗口 + EMA + 限幅。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._window = []
        self._ema = None
        self._last = None

    def update(self, depth_frame) -> Optional[float]:
        if depth_frame is None:
            return self._last

        depth = np.asanyarray(depth_frame.get_data())
        if depth is None or depth.size == 0:
            return self._last

        h, w = depth.shape[:2]
        roi_w = int(w * self.cfg.FRONT_ROI_X_RATIO)
        x1 = max(0, (w - roi_w) // 2)
        x2 = min(w, x1 + roi_w)
        y1 = int(h * self.cfg.FRONT_ROI_Y_TOP_RATIO)
        y2 = int(h * self.cfg.FRONT_ROI_Y_BOT_RATIO)
        if x2 <= x1 or y2 <= y1:
            return self._last

        roi_m = depth[y1:y2, x1:x2].astype(np.float32) / 1000.0
        valid = roi_m[(roi_m >= self.cfg.FRONT_MIN_VALID) & (roi_m <= self.cfg.FRONT_MAX_VALID)]
        if valid.size < 30:
            return self._last

        raw = float(np.percentile(valid, self.cfg.FRONT_PERCENTILE))

        self._window.append(raw)
        if len(self._window) > self.cfg.FRONT_MEDIAN_WINDOW:
            self._window.pop(0)
        med = float(np.median(self._window))

        if self._ema is None:
            filtered = med
        else:
            filtered = self._ema + self.cfg.FRONT_EMA_ALPHA * (med - self._ema)
            delta = filtered - self._ema
            if abs(delta) > self.cfg.FRONT_MAX_JUMP:
                filtered = self._ema + (self.cfg.FRONT_MAX_JUMP if delta > 0 else -self.cfg.FRONT_MAX_JUMP)

        self._ema = filtered
        self._last = round(float(filtered), 2)
        return self._last


# ========================== RealSense 封装 ==========================
class RealSenseCamera:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.pipeline = rs.pipeline()
        self.config = rs.config()
        self.config.enable_stream(
            rs.stream.depth, cfg.DEPTH_WIDTH, cfg.DEPTH_HEIGHT, rs.format.z16, cfg.DEPTH_FPS
        )
        self.config.enable_stream(
            rs.stream.color, cfg.DEPTH_WIDTH, cfg.DEPTH_HEIGHT, rs.format.bgr8, cfg.DEPTH_FPS
        )
        self.spatial = rs.spatial_filter()
        self.temporal = rs.temporal_filter()
        self.hole_filling = rs.hole_filling_filter()
        self._align = rs.align(rs.stream.color)
        self._encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 70]

    def start(self):
        self.profile = self.pipeline.start(self.config)
        depth_stream = self.profile.get_stream(rs.stream.depth).as_video_stream_profile()
        color_stream = self.profile.get_stream(rs.stream.color).as_video_stream_profile()
        print(f"[Camera] 已启动: depth={depth_stream.width()}x{depth_stream.height()} | color={color_stream.width()}x{color_stream.height()}")

    def stop(self):
        self.pipeline.stop()
        print("[Camera] 已关闭")

    def get_frames(self):
        """获取对齐后的彩色帧和深度帧"""
        try:
            frames = self.pipeline.wait_for_frames(timeout_ms=10000)
        except RuntimeError as e:
            print(f"[Camera] 获取帧超时: {e}")
            return None, None
        aligned = self._align.process(frames)
        depth = aligned.get_depth_frame()
        color = aligned.get_color_frame()
        if not depth or not color:
            return None, None
        d = self.spatial.process(depth)
        d = self.temporal.process(d)
        d = self.hole_filling.process(d)
        return color, d

    def get_filtered_depth(self):
        _, depth = self.get_frames()
        return depth

    def encode_color_frame(self, color_frame) -> Optional[str]:
        """将彩色帧编码为 JPEG base64"""
        if not color_frame:
            return None
        img = np.asanyarray(color_frame.get_data())
        ret, buf = cv2.imencode(".jpg", img, self._encode_param)
        if not ret:
            return None
        return base64.b64encode(buf.tobytes()).decode("utf-8")


# ========================== 点云与坐标变换 ==========================
def depth_to_pointcloud(depth_frame):
    """深度帧 → Nx3 点云 (x右, y下, z前)"""
    pc = rs.pointcloud()
    points = pc.calculate(depth_frame)
    vtx = np.asanyarray(points.get_vertices())
    xyz = np.vstack([vtx['f0'], vtx['f1'], vtx['f2']]).T
    return xyz


def compensate_tilt(xyz, tilt_deg):
    """
    将相机坐标系点云转换到水平坐标系 (x右, y上, z前)。
    tilt_deg: 正值 = 相机向下俯角 (度)
    """
    t = math.radians(tilt_deg)
    c, s = math.cos(t), math.sin(t)
    y, z = xyz[:, 1], xyz[:, 2]

    xyz_new = xyz.copy()
    # 推导: 先绕X轴旋转俯角，再将Y翻转为向上正
    xyz_new[:, 1] = -y * c + z * s   # 高度 (向上为正)
    xyz_new[:, 2] =  y * s + z * c   # 水平前方距离
    return xyz_new


def filter_ground(xyz_level, cam_height, clearance=0.12):
    """
    地面分割: 只保留高于地面 clearance 的点。
    水平坐标系原点在相机中心，地面理论高度 ≈ -cam_height
    """
    ground_height = -cam_height
    return xyz_level[xyz_level[:, 1] > (ground_height + clearance)]


# ========================== 栅格地图 ==========================
def build_occupancy_grid(xyz_level, cfg: Config):
    """生成已膨胀的2D鸟瞰栅格地图"""
    grid = np.zeros((cfg.GRID_SIZE, cfg.GRID_SIZE), dtype=np.uint8)

    xz = xyz_level[:, [0, 2]]
    x, z = xz[:, 0], xz[:, 1]

    half_range = cfg.GRID_SIZE * cfg.GRID_RES / 2.0
    ix = (x / cfg.GRID_RES + cfg.GRID_SIZE / 2).astype(np.int32)
    iz = (z / cfg.GRID_RES).astype(np.int32)

    valid = (ix >= 0) & (ix < cfg.GRID_SIZE) & (iz >= 0) & (iz < cfg.GRID_SIZE)
    ix, iz = ix[valid], iz[valid]
    grid[iz, ix] = 255

    # 按机器人半径膨胀
    r_cells = int(np.ceil(cfg.ROBOT_R / cfg.GRID_RES))
    if r_cells > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (r_cells * 2 + 1, r_cells * 2 + 1))
        grid = cv2.dilate(grid, kernel)

    return grid


# ========================== VFH 避障决策 ==========================
def vfh_decision(grid, cfg: Config):
    """
    简化VFH。返回: (steer_angle_deg, linear_speed)。
    steer=None 表示无路可走。
    """
    num = cfg.NUM_SECTORS
    sw = 180.0 / num
    robot_col = cfg.GRID_SIZE // 2
    robot_row = 0
    max_cells = int(cfg.MAX_DEPTH / cfg.GRID_RES)

    # 1. 扫描各扇区最大安全深度
    sectors = []
    for i in range(num):
        angle = -90.0 + i * sw + sw / 2.0
        rad = math.radians(angle)
        max_r = 0
        for r in range(1, max_cells):
            c = int(robot_col + r * math.sin(rad))
            r_idx = int(robot_row + r * math.cos(rad))
            if not (0 <= c < cfg.GRID_SIZE and 0 <= r_idx < cfg.GRID_SIZE):
                break
            if grid[r_idx, c] > 0:
                break
            max_r = r
        sectors.append((angle, max_r * cfg.GRID_RES))

    # 2. 阈值过滤
    threshold = cfg.ROBOT_R + cfg.SAFE_DIST
    free = [(a, d) for a, d in sectors if d >= threshold]

    if not free:
        return None, cfg.V_BACK

    # 3. 分组为连续山谷
    valleys = []
    cur = [free[0]]
    for i in range(1, len(free)):
        if abs(free[i][0] - free[i - 1][0]) < sw * 1.5:
            cur.append(free[i])
        else:
            valleys.append(cur)
            cur = [free[i]]
    valleys.append(cur)

    # 4. 选最优山谷
    best_valley = None
    best_score = -1e9
    for valley in valleys:
        if len(valley) < cfg.SECTOR_MIN_WIDTH:
            continue
        center = (valley[0][0] + valley[-1][0]) / 2.0
        avg_d = sum(d for _, d in valley) / len(valley)
        score = avg_d * 3.0 + len(valley) * 0.2 - abs(center - cfg.TARGET_ANGLE) * 0.5
        if score > best_score:
            best_score = score
            best_valley = valley

    if best_valley is None:
        a, d = max(free, key=lambda x: x[1])
        return a, cfg.V_SLOW

    center_angle = (best_valley[0][0] + best_valley[-1][0]) / 2.0
    avg_depth = sum(d for _, d in best_valley) / len(best_valley)

    if avg_depth > 2.0:
        v = cfg.V_FAST
    elif avg_depth > 1.0:
        v = cfg.V_MID
    else:
        v = cfg.V_SLOW

    return center_angle, v


# ========================== 可视化 ==========================
def visualize(depth_frame, grid, steer, speed, cfg: Config):
    """
    左侧: 深度图伪彩色 (640x480)
    右侧: 等比栅格图 (480x480) + 机器人/方向箭头
    拼接总尺寸: 1120x480
    """
    # --- 左侧深度图 ---
    depth_img = np.asanyarray(depth_frame.get_data())
    depth_vis = np.clip(depth_img, 0, int(cfg.MAX_DEPTH * 1000)).astype(np.float32)
    depth_norm = cv2.convertScaleAbs(depth_vis, alpha=255.0 / (cfg.MAX_DEPTH * 1000))
    depth_color = cv2.applyColorMap(depth_norm, cv2.COLORMAP_JET)

    # --- 右侧栅格图 (等比缩放，不变形) ---
    grid_rgb = cv2.cvtColor(grid, cv2.COLOR_GRAY2BGR)
    # 垂直翻转：栅格数组 row=0 对应 z≈0（机器人附近），显示时机器人应在底部、前方朝上
    grid_rgb = cv2.flip(grid_rgb, 0)
    grid_vis = cv2.resize(grid_rgb, (cfg.GRID_VIS_SIZE, cfg.GRID_VIS_SIZE), interpolation=cv2.INTER_NEAREST)

    # 在等比图像上绘制 (避免不等比拉伸导致方向变形)
    S = cfg.GRID_VIS_SIZE
    rx = S // 2               # 水平中心
    ry = int(S * 0.95)        # 底部 95% 处 (留一点边距)
    arrow_len = int(S * 0.18) # 箭头长度

    # 机器人位置 (绿色圆)
    cv2.circle(grid_vis, (rx, ry), 10, (0, 255, 0), -1)

    # 决策方向箭头 (红色)
    if steer is not None:
        rad = math.radians(steer)
        # 关键修正: +sin 向右, -cos 向上(图像坐标y向下，前方是上方)
        lx = int(rx + arrow_len * math.sin(rad))
        ly = int(ry - arrow_len * math.cos(rad))
        cv2.arrowedLine(grid_vis, (rx, ry), (lx, ly), (0, 0, 255), 3, tipLength=0.3)
        info = f"Steer: {steer:+.1f} | V: {speed:+.2f}"
        color = (0, 255, 255)
    else:
        info = "STOP / REVERSE"
        color = (0, 0, 255)

    cv2.putText(grid_vis, info, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    cv2.putText(grid_vis, "Front", (S // 2 - 25, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
    cv2.putText(grid_vis, "Left", (10, S // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
    cv2.putText(grid_vis, "Right", (S - 55, S // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

    # --- 拼接 ---
    combined = np.hstack([depth_color, grid_vis])
    cv2.imshow("D435i Obstacle Avoidance", combined)


# ========================== 主程序 ==========================
def main():
    cfg = Config()
    cam = RealSenseCamera(cfg)
    front_estimator = FrontDistanceEstimator(cfg)

    print("=" * 50)
    print("D435i 避障验证程序 (修正版)")
    print("按 Q 退出 | 按 S 保存深度帧 | 按 [ / ] 微调俯角")
    print("=" * 50)

    try:
        cam.start()
        frame_count = 0
        t0 = time.time()

        # 启动后端通信（可选）
        backend = BackendClient(BackendConfig.WS_URL, BackendConfig.ENABLED)
        backend.start()

        while True:
            # 同时获取彩色帧和深度帧
            color_frame, depth = cam.get_frames()
            if depth is None:
                continue

            # 编码彩色帧并发送给后端（用于前端视频显示）
            frame_b64 = cam.encode_color_frame(color_frame)
            if frame_b64:
                backend.send_frame(frame_b64)

            # 1. 点云（深度帧）
            xyz = depth_to_pointcloud(depth)
            valid = (xyz[:, 2] > cfg.MIN_DEPTH) & (xyz[:, 2] < cfg.MAX_DEPTH)
            xyz = xyz[valid]

            # 1.1 估计前方距离（用于前端展示，非避障主决策）
            front_dist = front_estimator.update(depth)

            # 2. 坐标转换 + 地面分割
            xyz_level = compensate_tilt(xyz, cfg.CAM_TILT)
            obs = filter_ground(xyz_level, cfg.CAM_HEIGHT, clearance=0.12)

            # 3. 栅格
            grid = build_occupancy_grid(obs, cfg)

            # 4. 决策
            steer, speed = vfh_decision(grid, cfg)

            # 4.1 发送控制指令给后端
            backend.send_control(steer, speed, front_dist)

            # 5. 可视化
            visualize(depth, grid, steer, speed, cfg)

            frame_count += 1
            if frame_count % 30 == 0:
                fps = frame_count / (time.time() - t0)
                print(f"[FPS {fps:.1f}] Obs: {len(obs):4d} | Decision: steer={steer}, v={speed}, front={front_dist}")

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('s'):
                fname = f"depth_{int(time.time())}.png"
                cv2.imwrite(fname, np.asanyarray(depth.get_data()))
                print(f"[Save] {fname}")
            elif key == ord('['):
                cfg.CAM_TILT -= 1
                print(f"[Tilt] {cfg.CAM_TILT}")
            elif key == ord(']'):
                cfg.CAM_TILT += 1
                print(f"[Tilt] {cfg.CAM_TILT}")

    except Exception as e:
        print("[Error]", e)
        raise
    finally:
        backend.stop()
        cam.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()