# D435i 避障算法原理：三区域 ROI + 可选 YOLO 过滤

本文档介绍 `src/obstacle_avoidance_yolo.py` 使用的规则式避障方法。该方法直接将深度图与彩色图对齐，把画面水平划分为左、中、右三个 ROI，通过计算每个区域的中位深度，结合简单的优先级规则输出避障决策。此外，还可选启用 YOLOv8 目标检测，只关注检测框内的深度区域。

---

## 1. 深度图与彩色图采集

使用 Intel RealSense D435i 同步捕获：

- 彩色图：`bgr8`，640×480，30 fps
- 深度图：`z16`，640×480，30 fps

深度图原始值为 16 位无符号整数，需乘以 `depth_scale`（约 0.001）转换为米。

### 对齐（Alignment）

彩色摄像头和深度摄像头的光心位置不同，像素不重合。使用 `rs.align(rs.stream.color)` 将深度图投影到彩色图坐标系，确保每个 RGB 像素都有对应的深度值。

### 滤波

对齐后依次应用：

- **Spatial Filter**：平滑深度图，填充小孔。
- **Temporal Filter**：利用多帧信息减少抖动。

---

## 2. 可选 YOLO 目标检测过滤

设置 `USE_YOLO = True` 时，程序会加载 YOLOv8 模型，仅对检测到的目标区域进行深度分析。

```python
model = YOLO(YOLO_MODEL_PATH)   # 默认 yolov8s.pt
boxes = detect_yolo_boxes(model, color_image)
mask = build_detection_mask(boxes, width, height)
```

### 检测参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `YOLO_MODEL_PATH` | `yolov8s.pt` | 预训练模型路径 |
| `YOLO_CONF` | 0.35 | 置信度阈值 |
| `YOLO_IOU` | 0.45 | NMS IoU 阈值 |
| `YOLO_MIN_BOX_AREA` | 600 px² | 最小检测框面积，过滤过远或过小的误检 |

### 掩码生成

将检测框合并为二值掩码。掩码外（非目标区域）的深度值被置为 `MAX_VALID_DEPTH + 1.0`，使其在后续区域统计中被视为无效：

```python
depth_for_zones = np.where(mask, depth_image, MAX_VALID_DEPTH + 1.0)
```

若当前帧没有检测到任何目标，则回退为全图分析。

> **注意**：使用 YOLO 需要安装 `ultralytics` 包。若未安装或设置为 `USE_YOLO = False`，程序将直接使用完整深度图。

---

## 3. 区域划分

将深度图水平分为三个 ROI（Region of Interest）：

```
+----------------+----------------+----------------+
|     LEFT       |     CENTER     |     RIGHT      |
|   (33% width)  |   (33% width)  |   (33% width)  |
|                |                |                |
|   检测左侧     |   检测前方     |   检测右侧     |
|   障碍物       |   障碍物       |   障碍物       |
+----------------+----------------+----------------+
```

每个区域宽度由 `ROI_WIDTH_RATIO = 0.33` 决定，高度为整幅图像高度。

---

## 4. 距离计算

对每个 ROI 内的有效深度值取**中位数**：

```python
valid = roi[(roi > MIN_VALID_DEPTH) & (roi < MAX_VALID_DEPTH)]
zone_distance = np.median(valid)
```

默认有效深度范围为 0.3 m ~ 10.0 m。

### 为什么用中位数？

深度图中常有噪点（0 值或异常飞点），中位数比均值更鲁棒，能有效抵抗少量离群值的干扰。

---

## 5. 决策逻辑

规则按以下优先级执行：

```
IF center < SAFE_DISTANCE:
    # 正前方有近距离障碍
    IF left > right AND left > SAFE:
        -> TURN LEFT
    ELSE IF right > left AND right > SAFE:
        -> TURN RIGHT
    ELSE:
        -> STOP / BACKWARD

ELIF center < WARNING_DISTANCE:
    # 正前方有中距离障碍，提前转向
    IF left > right:
        -> SLOW + TURN LEFT
    ELSE:
        -> SLOW + TURN RIGHT

ELIF left < SAFE_DISTANCE:
    -> TURN RIGHT

ELIF right < SAFE_DISTANCE:
    -> TURN LEFT

ELSE:
    -> FORWARD
```

### 输出示例

程序会在画面上显示具体决策文字，例如：

- `FORWARD`
- `SLOW -> TURN LEFT`
- `SLOW -> TURN RIGHT`
- `STOP -> TURN LEFT`
- `STOP -> TURN RIGHT`
- `STOP / BACK`

---

## 6. 可视化

运行时会显示一个组合画面：

- **上半部分**：彩色图像，叠加三个 ROI 的分割线与状态标签。
- **下半部分**：JET 伪彩色深度热力图。

### ROI 颜色

| 颜色 | 含义 |
|------|------|
| 绿色 | 安全距离 |
| 橙色 | 警告距离 |
| 红色 | 危险/停止 |

### 深度热力图

| 颜色 | 含义 |
|------|------|
| 蓝色/青色 | 远距离 |
| 黄色/红色 | 近距离 |

### YOLO 开启时

画面会用青色边框绘制检测到的目标，并标注类别与置信度。

---

## 7. 参数调优

### 核心参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `SAFE_DISTANCE` | 1.0 m | 必须转向/停止的距离阈值 |
| `WARNING_DISTANCE` | 2.0 m | 减速并准备转向的距离阈值 |
| `ROI_WIDTH_RATIO` | 0.33 | 每个 ROI 占画面宽度的比例 |
| `MIN_VALID_DEPTH` | 0.3 m | 有效深度下限 |
| `MAX_VALID_DEPTH` | 10.0 m | 有效深度上限 |
| `USE_YOLO` | `True` | 是否启用 YOLOv8 过滤 |

### 场景建议

| 场景 | SAFE_DISTANCE | WARNING_DISTANCE | 备注 |
|------|---------------|------------------|------|
| 室内低速机器人 | 0.8 m | 1.5 m | 狭窄空间，反应距离短 |
| 室外 AGV | 2.0 m | 4.0 m | 速度快，需要提前避让 |
| 无人机 | 1.5 m | 3.0 m | 三维空间，需结合高度 |
| 机械臂防护 | 0.5 m | 1.0 m | 极近距离，高精度要求 |

---

## 8. 局限性与扩展

### 局限性

1. **透明/反光物体**：玻璃、水面、镜面无法正确测距。
2. **强光干扰**：户外阳光过强会淹没红外投射。
3. **纹理缺失区域**：白墙、天空等缺乏纹理的区域精度下降。
4. **快速运动**：物体移动过快会产生运动模糊。
5. **YOLO 依赖**：开启 YOLO 后需要 GPU 或较强的 CPU 才能保持实时性；若检测不到目标，系统会回退到全图分析。

### 扩展方向

- **IMU 融合**：D435i 内置 IMU，可做视觉惯性里程计（VIO）。
- **点云处理**：将深度图转为 3D 点云，做三维障碍物建模。
- **语义分割**：用更精细的分割模型替代检测框，获取障碍物轮廓。
- **路径规划**：将避障决策接入 ROS / 机器人底盘控制。
