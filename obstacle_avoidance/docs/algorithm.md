# D435i 避障算法原理

## 1. 深度图获取

Intel RealSense D435i 使用**主动红外立体视觉**技术获取深度：

- 相机投射不可见红外图案到场景
- 左右红外摄像头捕捉图像
- 通过立体匹配算法计算每个像素的视差
- 根据视差转换为距离：`depth = (baseline * focal_length) / disparity`

深度图特点：
- 单位：16bit 无符号整数，需乘以 `depth_scale`（约 0.001）转换为米
- 无效区域（反射/吸收红外光的材质）显示为 0 值
- 有效范围：0.3m ~ 10m（最佳精度在 0.5m ~ 3m）

## 2. 帧对齐（Alignment）

彩色摄像头和深度摄像头的光心位置不同，像素不重合。使用 `rs.align` 将深度图投影到彩色图坐标系，确保每个 RGB 像素都有对应的深度值。

## 3. 区域划分

将画面水平分为三个 ROI（Region of Interest）：

```
+----------------+----------------+----------------+
|     LEFT       |     CENTER     |     RIGHT      |
|   (33% width)  |   (33% width)  |   (33% width)  |
|                |                |                |
|   检测左侧     |   检测前方     |   检测右侧     |
|   障碍物       |   障碍物       |   障碍物       |
+----------------+----------------+----------------+
```

## 4. 距离计算

对每个 ROI 内的有效深度值取**中位数**（非均值）：

```python
valid = roi[(roi > min_depth) & (roi < max_depth)]
zone_distance = np.median(valid)
```

使用中位数的原因：深度图中常有噪点（0值或异常值），中位数比均值更鲁棒。

## 5. 决策逻辑

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

## 6. 滤波处理

### 6.1 空间滤波（Spatial Filter）
- 平滑深度图，填充小孔
- 保留边缘信息

### 6.2 时域滤波（Temporal Filter）
- 利用多帧信息减少抖动
- 适合静态或缓慢变化的场景

### 6.3 形态学滤波（Morphology）
- 开运算：去除孤立噪点
- 闭运算：连接断裂的障碍物区域

## 7. 可视化

| 颜色 | 含义 |
|------|------|
| 绿色 | 安全距离 |
| 橙色 | 警告距离 |
| 红色 | 危险/停止 |
| 蓝色(深度图) | 远距离 |
| 红色(深度图) | 近距离 |

## 8. 参数调优建议

| 场景 | SAFE_DISTANCE | WARNING_DISTANCE | 备注 |
|------|---------------|------------------|------|
| 室内低速机器人 | 0.8m | 1.5m | 狭窄空间，反应距离短 |
| 室外AGV | 2.0m | 4.0m | 速度快，需要提前避让 |
| 无人机 | 1.5m | 3.0m | 三维空间，需结合高度 |
| 机械臂防护 | 0.5m | 1.0m | 极近距离，高精度要求 |

## 9. 局限性

1. **透明/反光物体**：玻璃、水面、镜面无法正确测距
2. **强光干扰**：户外阳光过强会淹没红外投射
3. **纹理缺失区域**：白墙、天空等缺乏纹理的区域精度下降
4. **快速运动**：物体移动过快会产生运动模糊

## 10. 扩展方向

- **IMU 融合**：D435i 内置 IMU，可做视觉惯性里程计（VIO）
- **点云处理**：将深度图转为 3D 点云，做三维障碍物建模
- **语义分割**：结合深度学习识别障碍物类别（人/车/墙）
- **路径规划**：将避障决策接入 ROS/机器人底盘控制
