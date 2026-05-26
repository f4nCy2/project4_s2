"""
D435i 基础查看器
功能：显示 RGB 彩色图和深度伪彩色图
用法：python src/basic_viewer.py
按 Q 退出
"""

import pyrealsense2 as rs
import numpy as np
import cv2


def main():
    # -----------------------------
    # 1. 配置 RealSense 管道
    # -----------------------------
    pipeline = rs.pipeline()
    config = rs.config()

    # 设置分辨率与帧率（可根据需要调整）
    # D435i 常见配置：640x480@30fps 或 1280x720@30fps
    width, height, fps = 640, 480, 30

    config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
    config.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)

    # 尝试启动相机
    try:
        profile = pipeline.start(config)
        print("[INFO] 相机已连接并启动")
    except Exception as e:
        print(f"[ERROR] 无法启动相机: {e}")
        print("[HINT] 请检查:")
        print("  1. D435i 是否通过 USB3.0 连接")
        print("  2. RealSense Viewer 能否正常打开")
        print("  3. 是否有其他程序占用了相机")
        return

    # 获取深度传感器，设置深度单位和对齐
    align = rs.align(rs.stream.color)

    # 可选：设置孔填充滤波，让深度图更平滑
    spatial = rs.spatial_filter()
    spatial.set_option(rs.option.filter_magnitude, 2)
    spatial.set_option(rs.option.filter_smooth_alpha, 0.5)
    spatial.set_option(rs.option.filter_smooth_delta, 20)

    temporal = rs.temporal_filter()

    print("[INFO] 按 Q 键退出程序")

    try:
        while True:
            # -----------------------------
            # 2. 等待并获取帧
            # -----------------------------
            frames = pipeline.wait_for_frames()

            # 对齐深度帧到彩色帧（让深度和彩色像素一一对应）
            aligned_frames = align.process(frames)

            color_frame = aligned_frames.get_color_frame()
            depth_frame = aligned_frames.get_depth_frame()

            if not color_frame or not depth_frame:
                continue

            # 应用滤波
            depth_frame = spatial.process(depth_frame).as_depth_frame()
            depth_frame = temporal.process(depth_frame).as_depth_frame()

            # -----------------------------
            # 3. 转换为 numpy 数组
            # -----------------------------
            color_image = np.asanyarray(color_frame.get_data())
            depth_image = np.asanyarray(depth_frame.get_data())

            # 深度图转伪彩色，便于人眼观察
            # cv2.COLORMAP_JET: 近=红，远=蓝
            depth_colormap = cv2.applyColorMap(
                cv2.convertScaleAbs(depth_image, alpha=0.03),
                cv2.COLORMAP_JET
            )

            # -----------------------------
            # 4. 显示画面
            # -----------------------------
            # 获取画面中心点距离
            cx, cy = width // 2, height // 2
            center_distance = depth_frame.get_distance(cx, cy)

            # 在彩色图上标注中心距离
            label = f"Center: {center_distance:.2f}m"
            cv2.putText(color_image, label, (cx - 60, cy - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            cv2.circle(color_image, (cx, cy), 5, (0, 255, 0), -1)

            # 水平拼接两幅图
            combined = np.hstack((color_image, depth_colormap))

            # 添加标题栏
            h, w = combined.shape[:2]
            bar = np.zeros((30, w, 3), dtype=np.uint8)
            cv2.putText(bar, "RGB Image", (w // 4 - 50, 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.putText(bar, "Depth Map", (3 * w // 4 - 50, 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            combined = np.vstack((bar, combined))

            cv2.imshow("D435i Basic Viewer", combined)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                print("[INFO] 用户退出")
                break

    finally:
        pipeline.stop()
        cv2.destroyAllWindows()
        print("[INFO] 资源已释放")


if __name__ == "__main__":
    main()
