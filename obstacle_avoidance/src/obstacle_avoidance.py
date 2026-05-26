"""
D435i 视觉避障系统（完整版）
功能：实时分析左/中/右三个区域的距离，给出避障决策
用法：python src/obstacle_avoidance.py
按 Q 退出
"""

import pyrealsense2 as rs
import numpy as np
import cv2
import time
try:
    from ultralytics import YOLO
except Exception:
    YOLO = None


# =============================
# 可调参数区
# =============================
SAFE_DISTANCE = 1.0         # 安全距离（米），小于此值需要避障
WARNING_DISTANCE = 2.0      # 警告距离（米），小于此值需警惕
ROI_WIDTH_RATIO = 0.33      # 左/中/右区域各占画面宽度的比例
MIN_VALID_DEPTH = 0.3       # 最小有效深度（过滤噪点）
MAX_VALID_DEPTH = 10.0      # 最大有效深度

# YOLOv8 参数（用于过滤误判）
USE_YOLO = True
YOLO_MODEL_PATH = "yolov8s.pt"
YOLO_CONF = 0.35
YOLO_IOU = 0.45
YOLO_MIN_BOX_AREA = 600

# 显示参数
FONT = cv2.FONT_HERSHEY_SIMPLEX
LINE_HEIGHT = 30


def analyze_zones(depth_image, width, height):
    """
    将深度图划分为左/中/右三个区域，计算各区域平均距离
    
    返回:
        zones: dict, {'left': 距离, 'center': 距离, 'right': 距离}
        如果某区域无有效深度值，距离设为 inf
    """
    roi_w = int(width * ROI_WIDTH_RATIO)
    
    # 左区域
    left_roi = depth_image[:, 0:roi_w]
    # 中区域
    center_start = (width - roi_w) // 2
    center_roi = depth_image[:, center_start:center_start+roi_w]
    # 右区域
    right_roi = depth_image[:, width-roi_w:width]
    
    zones = {}
    for name, roi in [('left', left_roi), ('center', center_roi), ('right', right_roi)]:
        valid = roi[(roi > MIN_VALID_DEPTH) & (roi < MAX_VALID_DEPTH)]
        if len(valid) > 0:
            # 使用中位数抗噪，比均值更鲁棒
            zones[name] = float(np.median(valid))
        else:
            zones[name] = float('inf')
    
    return zones, roi_w, center_start


def make_decision(zones, safe_dist, warn_dist):
    """
    根据三个区域的距离做出避障决策
    
    决策逻辑：
    - center < safe: 停止/后退（危险）
    - center < warn: 减速，并选择 left/right 中更远的一侧转向
    - left < safe: 右转
    - right < safe: 左转
    - 全部 > warn: 前进
    
    返回:
        decision: str, 决策文字
        decision_type: str, 决策类型编码 ('stop', 'left', 'right', 'forward', 'slow')
        info: str, 详细距离信息
    """
    left_d = zones['left']
    center_d = zones['center']
    right_d = zones['right']
    
    info = f"L:{left_d:.2f}m C:{center_d:.2f}m R:{right_d:.2f}m"
    
    # 最优先：正前方危险
    if center_d < safe_dist:
        if left_d > right_d and left_d > safe_dist:
            return "STOP -> TURN LEFT", "left", info
        elif right_d > left_d and right_d > safe_dist:
            return "STOP -> TURN RIGHT", "right", info
        else:
            return "STOP / BACK", "stop", info
    
    # 正前方警告
    if center_d < warn_dist:
        if left_d > right_d:
            return "SLOW -> TURN LEFT", "left", info
        else:
            return "SLOW -> TURN RIGHT", "right", info
    
    # 侧方危险
    if left_d < safe_dist and right_d < safe_dist:
        return "STOP (Both sides blocked)", "stop", info
    if left_d < safe_dist:
        return "TURN RIGHT", "right", info
    if right_d < safe_dist:
        return "TURN LEFT", "left", info
    
    # 全部安全
    return "FORWARD", "forward", info


def draw_ui(image, zones, decision, decision_type, info, roi_w, center_start, width, height):
    """在图像上绘制 UI：区域框、距离、决策文字"""
    result = image.copy()
    
    # 颜色定义
    COLOR_SAFE = (0, 255, 0)      # 绿
    COLOR_WARN = (0, 165, 255)    # 橙
    COLOR_DANGER = (0, 0, 255)    # 红
    COLOR_INFO = (255, 255, 255)  # 白
    
    # 绘制三个区域的竖线分隔
    cv2.line(result, (roi_w, 0), (roi_w, height), (255, 255, 0), 1)
    cv2.line(result, (width - roi_w, 0), (width - roi_w, height), (255, 255, 0), 1)
    
    # 绘制区域距离标签
    positions = {
        'left': (10, 30),
        'center': (center_start + 10, 30),
        'right': (width - roi_w + 10, 30)
    }
    
    for zone_name in ['left', 'center', 'right']:
        d = zones[zone_name]
        pos = positions[zone_name]
        
        if d < SAFE_DISTANCE:
            color = COLOR_DANGER
            label = f"{zone_name.upper()}: {d:.2f}m DANGER"
        elif d < WARNING_DISTANCE:
            color = COLOR_WARN
            label = f"{zone_name.upper()}: {d:.2f}m WARN"
        else:
            color = COLOR_SAFE
            label = f"{zone_name.upper()}: {d:.2f}m OK"
        
        # 背景框
        (tw, th), _ = cv2.getTextSize(label, FONT, 0.6, 2)
        cv2.rectangle(result, (pos[0]-2, pos[1]-th-4), (pos[0]+tw+4, pos[1]+4), (0, 0, 0), -1)
        cv2.putText(result, label, pos, FONT, 0.6, color, 2)
    
    # 绘制决策大文字（画面底部）
    if decision_type == 'stop':
        dec_color = COLOR_DANGER
    elif decision_type == 'forward':
        dec_color = COLOR_SAFE
    else:
        dec_color = COLOR_WARN
    
    # 决策文字背景
    (dw, dh), _ = cv2.getTextSize(decision, FONT, 1.2, 3)
    dx = (width - dw) // 2
    dy = height - 20
    cv2.rectangle(result, (dx - 10, dy - dh - 10), (dx + dw + 10, dy + 10), (0, 0, 0), -1)
    cv2.putText(result, decision, (dx, dy), FONT, 1.2, dec_color, 3)
    
    # 底部信息栏
    info_bar = np.zeros((30, width, 3), dtype=np.uint8)
    cv2.putText(info_bar, info, (10, 22), FONT, 0.6, COLOR_INFO, 1)
    result = np.vstack((result, info_bar))
    
    return result


def draw_depth_heatmap(depth_image, width, height):
    """生成下方显示的距离热力图"""
    # 裁剪到有效范围并转为 8bit
    clipped = np.clip(depth_image, 0, WARNING_DISTANCE * 1.5)
    normalized = cv2.normalize(clipped, None, 0, 255, cv2.NORM_MINMAX)
    heatmap = cv2.applyColorMap(normalized.astype(np.uint8), cv2.COLORMAP_JET)
    heatmap = cv2.resize(heatmap, (width, height // 4))
    
    # 添加刻度
    cv2.putText(heatmap, "0m", (5, 20), FONT, 0.5, (255, 255, 255), 1)
    cv2.putText(heatmap, f"{WARNING_DISTANCE * 1.5:.1f}m", (width - 60, 20), FONT, 0.5, (255, 255, 255), 1)
    cv2.putText(heatmap, "Distance Heatmap", (width // 2 - 80, 20), FONT, 0.5, (255, 255, 255), 1)
    
    return heatmap


def detect_yolo_boxes(model, image):
    """运行 YOLO 并返回检测框列表"""
    results = model(image, conf=YOLO_CONF, iou=YOLO_IOU, verbose=False)[0]
    if results.boxes is None:
        return []

    names = results.names or {}
    detections = []
    for box in results.boxes:
        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
        conf = float(box.conf[0].cpu().numpy())
        cls_id = int(box.cls[0].cpu().numpy())

        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(image.shape[1] - 1, x2)
        y2 = min(image.shape[0] - 1, y2)

        bw = x2 - x1
        bh = y2 - y1
        if bw <= 0 or bh <= 0:
            continue
        if bw * bh < YOLO_MIN_BOX_AREA:
            continue

        detections.append({
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "conf": conf,
            "name": names.get(cls_id, str(cls_id))
        })

    return detections


def build_detection_mask(detections, width, height):
    """将检测框转为二值掩码"""
    mask = np.zeros((height, width), dtype=np.uint8)
    for det in detections:
        mask[det["y1"]:det["y2"], det["x1"]:det["x2"]] = 1
    return mask


def draw_yolo_boxes(image, detections):
    """在图像上绘制 YOLO 检测框"""
    result = image.copy()
    for det in detections:
        x1, y1, x2, y2 = det["x1"], det["y1"], det["x2"], det["y2"]
        label = f"{det['name']} {det['conf']:.2f}"
        cv2.rectangle(result, (x1, y1), (x2, y2), (255, 255, 0), 2)
        cv2.putText(result, label, (x1, max(20, y1 - 5)),
                    FONT, 0.5, (255, 255, 0), 2)
    return result


def main():
    pipeline = rs.pipeline()
    config = rs.config()
    
    width, height, fps = 640, 480, 30
    config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
    config.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)
    
    try:
        profile = pipeline.start(config)
        print("=" * 50)
        print("  D435i 视觉避障系统")
        print("=" * 50)
    except Exception as e:
        print(f"[ERROR] 相机启动失败: {e}")
        print("请检查相机是否连接，以及 RealSense SDK 是否正确安装。")
        return
    
    align = rs.align(rs.stream.color)
    depth_scale = profile.get_device().first_depth_sensor().get_depth_scale()

    model = None
    if USE_YOLO:
        if YOLO is None:
            print("[WARN] 未安装 ultralytics，已关闭 YOLO 过滤")
        else:
            try:
                model = YOLO(YOLO_MODEL_PATH)
                print(f"[INFO] YOLO 模型加载成功: {YOLO_MODEL_PATH}")
            except Exception as e:
                print(f"[WARN] YOLO 模型加载失败: {e}")
                model = None
    
    # 可选滤波器
    spatial = rs.spatial_filter()
    spatial.set_option(rs.option.filter_magnitude, 2)
    temporal = rs.temporal_filter()
    
    print(f"[CONFIG] 分辨率: {width}x{height}@{fps}fps")
    print(f"[CONFIG] 安全距离: {SAFE_DISTANCE}m | 警告距离: {WARNING_DISTANCE}m")
    print("[INFO] 按 Q 键退出程序")
    print("-" * 50)
    
    fps_counter = 0
    fps_time = time.time()
    current_fps = 0
    
    try:
        while True:
            frames = pipeline.wait_for_frames()
            aligned = align.process(frames)
            
            color_frame = aligned.get_color_frame()
            depth_frame = aligned.get_depth_frame()
            
            if not color_frame or not depth_frame:
                continue
            
            # 滤波
            depth_frame = spatial.process(depth_frame).as_depth_frame()
            depth_frame = temporal.process(depth_frame).as_depth_frame()
            
            color_image = np.asanyarray(color_frame.get_data())
            depth_image = np.asanyarray(depth_frame.get_data()).astype(np.float32) * depth_scale
            
            detections = []
            depth_for_analysis = depth_image
            if model is not None:
                detections = detect_yolo_boxes(model, color_image)
                if detections:
                    mask = build_detection_mask(detections, width, height)
                    depth_for_analysis = depth_image.copy()
                    depth_for_analysis[mask == 0] = MAX_VALID_DEPTH + 1.0

            # 1. 分析三个区域
            zones, roi_w, center_start = analyze_zones(depth_for_analysis, width, height)
            
            # 2. 避障决策
            decision, dec_type, info = make_decision(zones, SAFE_DISTANCE, WARNING_DISTANCE)
            
            # 3. 绘制 UI
            vis_image = draw_ui(
                color_image, zones, decision, dec_type, info,
                roi_w, center_start, width, height
            )
            if detections:
                vis_image = draw_yolo_boxes(vis_image, detections)
            
            # 4. 添加 FPS
            fps_counter += 1
            if time.time() - fps_time >= 1.0:
                current_fps = fps_counter
                fps_counter = 0
                fps_time = time.time()
            
            cv2.putText(vis_image, f"FPS: {current_fps}", (width - 100, height - 5),
                        FONT, 0.5, (0, 255, 0), 1)
            
            # 5. 下方拼接深度热力图
            heatmap = draw_depth_heatmap(depth_image, width, height)
            combined = np.vstack((vis_image, heatmap))
            
            cv2.imshow("D435i Obstacle Avoidance", combined)
            
            if cv2.waitKey(1) & 0xFF == ord('q'):
                print("[INFO] 用户退出")
                break
                
    except Exception as e:
        print(f"[ERROR] 运行时异常: {e}")
        
    finally:
        pipeline.stop()
        cv2.destroyAllWindows()
        print("[INFO] 资源已释放，程序结束")


if __name__ == "__main__":
    main()
