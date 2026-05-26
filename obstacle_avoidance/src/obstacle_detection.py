"""
D435i 障碍物检测
功能：在深度图中检测障碍物区域并绘制边框
用法：python src/obstacle_detection.py
按 Q 退出
"""

import pyrealsense2 as rs
import numpy as np
import cv2
try:
    from ultralytics import YOLO
except Exception:
    YOLO = None


# -----------------------------
# 可调参数
# -----------------------------
MIN_DISTANCE = 0.3          # 最小有效距离（米），过滤太近噪点
MAX_DISTANCE = 5.0          # 最大检测距离（米）
OBSTACLE_THRESHOLD = 1.5    # 障碍物距离阈值（米），小于此值视为障碍
MORPH_KERNEL_SIZE = 5       # 形态学运算核大小

# YOLOv8 参数
USE_YOLO = True
YOLO_MODEL_PATH = "yolov8s.pt"
YOLO_CONF = 0.35
YOLO_IOU = 0.45
YOLO_MIN_BOX_AREA = 600
MIN_VALID_RATIO = 0.2


def get_depth_at_pixel(depth_frame, x, y):
    """获取指定像素点的距离（米）"""
    return depth_frame.get_distance(int(x), int(y))


def detect_obstacles(depth_image, color_image, min_dist, max_dist, threshold):
    """
    基于深度图检测障碍物
    
    参数:
        depth_image: 原始深度图（单位：米，浮点）
        color_image: 对应的彩色图（用于绘制结果）
        min_dist: 最小有效距离
        max_dist: 最大检测距离
        threshold: 障碍物距离阈值
    
    返回:
        result_image: 绘制了检测结果的图像
        obstacles: 障碍物列表 [(x, y, w, h, avg_distance), ...]
    """
    h, w = depth_image.shape
    
    # 1. 生成障碍物掩码：距离在 [min_dist, threshold] 范围内的像素
    obstacle_mask = cv2.inRange(depth_image, min_dist, threshold)
    
    # 2. 形态学运算：先开运算去噪点，再闭运算连接断裂区域
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (MORPH_KERNEL_SIZE, MORPH_KERNEL_SIZE))
    obstacle_mask = cv2.morphologyEx(obstacle_mask, cv2.MORPH_OPEN, kernel)
    obstacle_mask = cv2.morphologyEx(obstacle_mask, cv2.MORPH_CLOSE, kernel)
    
    # 3. 查找轮廓
    contours, _ = cv2.findContours(obstacle_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    obstacles = []
    result_image = color_image.copy()
    
    for cnt in contours:
        area = cv2.contourArea(cnt)
        # 过滤太小的区域（噪点）
        if area < 500:
            continue
        
        x, y, bw, bh = cv2.boundingRect(cnt)
        
        # 计算该区域的平均距离
        roi = depth_image[y:y+bh, x:x+bw]
        valid = roi[(roi > min_dist) & (roi < max_dist)]
        if len(valid) == 0:
            continue
        avg_dist = float(np.mean(valid))
        
        obstacles.append((x, y, bw, bh, avg_dist))
        
        # 绘制检测结果
        # 框颜色根据距离变化：越近越红
        if avg_dist < 1.0:
            color = (0, 0, 255)  # 红：危险
            label = f"DANGER {avg_dist:.2f}m"
        elif avg_dist < 1.5:
            color = (0, 165, 255)  # 橙：警告
            label = f"WARN {avg_dist:.2f}m"
        else:
            color = (0, 255, 0)  # 绿：安全
            label = f"SAFE {avg_dist:.2f}m"
        
        cv2.rectangle(result_image, (x, y), (x+bw, y+bh), color, 2)
        cv2.putText(result_image, label, (x, y - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    
    return result_image, obstacles


def detect_obstacles_yolo(model, depth_image, color_image, min_dist, max_dist, threshold):
    """
    使用 YOLO 目标检测 + 深度有效性筛选

    返回:
        result_image: 绘制了检测结果的图像
        obstacles: 障碍物列表 [(x, y, w, h, avg_distance), ...]
    """
    result_image = color_image.copy()
    obstacles = []

    results = model(color_image, conf=YOLO_CONF, iou=YOLO_IOU, verbose=False)[0]
    if results.boxes is None:
        return result_image, obstacles

    names = results.names or {}
    for box in results.boxes:
        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
        conf = float(box.conf[0].cpu().numpy())
        cls_id = int(box.cls[0].cpu().numpy())

        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(depth_image.shape[1] - 1, x2)
        y2 = min(depth_image.shape[0] - 1, y2)

        bw = x2 - x1
        bh = y2 - y1
        if bw <= 0 or bh <= 0:
            continue
        if bw * bh < YOLO_MIN_BOX_AREA:
            continue

        roi = depth_image[y1:y2, x1:x2]
        valid = roi[(roi > min_dist) & (roi < max_dist)]
        valid_ratio = len(valid) / max(1, roi.size)
        if len(valid) == 0 or valid_ratio < MIN_VALID_RATIO:
            continue

        avg_dist = float(np.mean(valid))
        obstacles.append((x1, y1, bw, bh, avg_dist))

        if avg_dist < 1.0:
            color = (0, 0, 255)
            dist_label = f"DANGER {avg_dist:.2f}m"
        elif avg_dist < threshold:
            color = (0, 165, 255)
            dist_label = f"WARN {avg_dist:.2f}m"
        else:
            color = (0, 255, 0)
            dist_label = f"SAFE {avg_dist:.2f}m"

        name = names.get(cls_id, str(cls_id))
        label = f"{name} {conf:.2f} {dist_label}"
        cv2.rectangle(result_image, (x1, y1), (x2, y2), color, 2)
        cv2.putText(result_image, label, (x1, max(20, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    return result_image, obstacles


def main():
    pipeline = rs.pipeline()
    config = rs.config()
    
    width, height, fps = 640, 480, 30
    config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
    config.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)
    
    try:
        profile = pipeline.start(config)
        print("[INFO] 相机已启动 - 障碍物检测模式")
    except Exception as e:
        print(f"[ERROR] 相机启动失败: {e}")
        return
    
    align = rs.align(rs.stream.color)
    
    # 深度比例：将 16bit 深度值转为米
    depth_scale = profile.get_device().first_depth_sensor().get_depth_scale()
    print(f"[INFO] 深度比例: {depth_scale:.6f} (1 单位 = {depth_scale:.4f} 米)")

    model = None
    if USE_YOLO:
        if YOLO is None:
            print("[WARN] 未安装 ultralytics，自动切换到深度检测模式")
        else:
            try:
                model = YOLO(YOLO_MODEL_PATH)
                print(f"[INFO] YOLO 模型加载成功: {YOLO_MODEL_PATH}")
            except Exception as e:
                print(f"[WARN] YOLO 模型加载失败: {e}")
                model = None
    
    print(f"[INFO] 检测参数:")
    print(f"  - 最小距离: {MIN_DISTANCE}m")
    print(f"  - 最大距离: {MAX_DISTANCE}m")
    print(f"  - 障碍物阈值: {OBSTACLE_THRESHOLD}m")
    print("[INFO] 按 Q 退出")
    
    try:
        while True:
            frames = pipeline.wait_for_frames()
            aligned = align.process(frames)
            
            color_frame = aligned.get_color_frame()
            depth_frame = aligned.get_depth_frame()
            
            if not color_frame or not depth_frame:
                continue
            
            color_image = np.asanyarray(color_frame.get_data())
            # 深度图转为 float32（单位：米）
            depth_image = np.asanyarray(depth_frame.get_data()).astype(np.float32) * depth_scale
            
            # 执行障碍物检测
            if model is not None:
                result, obstacles = detect_obstacles_yolo(
                    model, depth_image, color_image,
                    MIN_DISTANCE, MAX_DISTANCE, OBSTACLE_THRESHOLD
                )
            else:
                result, obstacles = detect_obstacles(
                    depth_image, color_image,
                    MIN_DISTANCE, MAX_DISTANCE, OBSTACLE_THRESHOLD
                )
            
            # 在顶部添加状态栏
            bar = np.zeros((35, width, 3), dtype=np.uint8)
            status_text = f"Obstacles detected: {len(obstacles)}"
            cv2.putText(bar, status_text, (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            result = np.vstack((bar, result))
            
            # 同时显示深度伪彩色图
            depth_colormap = cv2.applyColorMap(
                cv2.convertScaleAbs(depth_image, alpha=50),
                cv2.COLORMAP_JET
            )
            depth_colormap = cv2.resize(depth_colormap, (width // 2, height // 2))
            
            # 把深度小图贴在右下角
            rh, rw = result.shape[:2]
            dh, dw = depth_colormap.shape[:2]
            result[rh-dh:rh, rw-dw:rw] = depth_colormap
            cv2.rectangle(result, (rw-dw, rh-dh), (rw, rh), (255, 255, 255), 2)
            
            cv2.imshow("Obstacle Detection", result)
            
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
                
    finally:
        pipeline.stop()
        cv2.destroyAllWindows()
        print("[INFO] 程序已退出")


if __name__ == "__main__":
    main()
