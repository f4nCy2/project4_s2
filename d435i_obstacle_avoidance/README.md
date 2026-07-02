# D435i 视觉避障障碍物识别系统 (Windows)

本项目使用 Intel RealSense D435i 深度相机在 Windows 平台上实现实时视觉避障与障碍物识别。

---

## 一、环境准备

### 1.1 安装 Python
1. 访问 https://www.python.org/downloads/
2. 下载 **Python 3.9/3.10/3.11** (推荐 3.10)
3. **关键**: 安装时勾选 ☑️ `Add Python to PATH`
4. 验证安装：打开 PowerShell，输入：
   ```powershell
   python --version
   pip --version
   ```

### 1.2 安装 Intel RealSense SDK
1. 访问 https://github.com/IntelRealSense/librealsense/releases
2. 下载 `Intel.RealSense.SDK-WIN10-x.x.x.xxx.exe`
3. 双击安装，保持默认选项即可
4. 安装完成后，将相机通过 USB3.0 接入电脑

### 1.3 验证相机连接
打开 `Intel RealSense Viewer`（开始菜单搜索），能看到彩色和深度画面即表示连接成功。

---

## 二、项目安装

### 2.1 克隆/解压项目
```powershell
cd d435i_obstacle_avoidance
```

### 2.2 安装 Python 依赖
```powershell
pip install -r requirements.txt
```

> 如果遇到 `pyrealsense2` 安装问题，尝试：
> ```powershell
> pip install pyrealsense2 --no-cache-dir
> ```

---

## 三、快速上手

### 3.1 运行 VFH 栅格避障（推荐，几何规划）
```powershell
python src/obstacle_avoidance.py
```
显示：
- 左侧：JET 伪彩色深度图
- 右侧：占用栅格鸟瞰图 + 机器人位置 + 红色行进方向箭头

按 `Q` 退出，`S` 保存深度帧，`[`/`]` 微调相机俯角。

### 3.2 运行三区域 ROI / YOLO 避障（规则式）
```powershell
python src/obstacle_avoidance_yolo.py
```
显示：
- 上方：RGB 图像 + 左/中/右 ROI 区域标记
- 下方：深度热力图 + 避障建议文字

可选开启 `USE_YOLO = True`，只分析 YOLOv8 检测到的目标区域。

---

## 四、项目结构

```
d435i_obstacle_avoidance/
├── README.md                       # 本文件
├── requirements.txt                # Python 依赖
├── src/
│   ├── obstacle_avoidance.py       # 方法 1：点云栅格 + VFH 局部规划
│   └── obstacle_avoidance_yolo.py  # 方法 2：三区域 ROI 中位数 + 可选 YOLO
└── docs/
    ├── algorithm_vfh.md            # VFH 栅格方法原理
    └── algorithm_yolo.md           # 三区域 ROI / YOLO 方法原理
```

---

## 五、避障原理简介

本项目提供两种独立的避障实现，可按需选择：

### 方法 1：点云栅格 + VFH 局部规划（`src/obstacle_avoidance.py`）

1. **深度图获取**：D435i 主动红外投射 + 双目计算，获取每个像素的距离。
2. **点云生成**：通过相机内参将深度图反投影为相机坐标系点云。
3. **倾斜补偿与地面滤除**：根据安装俯角和相机高度剔除地面点。
4. **占用栅格**：将障碍物投影为 2D 鸟瞰栅格，并按机器人半径膨胀。
5. **VFH 方向选择**：将前方 180° 划分为 36 个扇区，选择最宽、最深、最接近正前方的可通行山谷。
6. **速度映射**：根据山谷平均深度输出快/中/慢/后退速度。

详见 [`docs/algorithm_vfh.md`](docs/algorithm_vfh.md)。

### 方法 2：三区域 ROI + 可选 YOLO 过滤（`src/obstacle_avoidance_yolo.py`）

1. **深度图获取与对齐**：获取彩色图并对齐深度图。
2. **可选 YOLO 过滤**：若启用 YOLOv8，只保留检测框内的深度区域参与统计。
3. **ROI 划分**：将画面水平分为左/中/右三个区域。
4. **距离统计**：计算每个区域的中位距离。
5. **障碍物判断**：若某区域中位距离 < 安全阈值，判定为有障碍物。
6. **避障决策**：
   - 中间近 → 停止 / 后退
   - 左边近 → 右转
   - 右边近 → 左转
   - 都远 → 前进

详见 [`docs/algorithm_yolo.md`](docs/algorithm_yolo.md)。

---

## 六、参数调节

### 方法 1：VFH 栅格避障

编辑 `src/obstacle_avoidance.py` 顶部的参数：

```python
CAM_HEIGHT = 0.45        # 相机离地高度（米）
CAM_TILT = 10.0          # 相机俯角（度），正值向下
GRID_RES = 0.05          # 栅格分辨率（米）
GRID_SIZE = 80           # 栅格尺寸
ROBOT_R = 0.25           # 机器人半径（米）
SAFE_DIST = 0.50         # 安全余量（米）
```

### 方法 2：三区域 ROI / YOLO 避障

编辑 `src/obstacle_avoidance_yolo.py` 顶部的参数：

```python
SAFE_DISTANCE = 1.0      # 安全距离（米），根据场景调整
WARNING_DISTANCE = 2.0   # 警告距离（米）
ROI_WIDTH_RATIO = 0.33   # 左/中/右区域宽度比例
USE_YOLO = True          # 是否启用 YOLOv8 过滤
YOLO_CONF = 0.35         # YOLO 置信度阈值
```

---

## 七、常见问题

| 问题 | 解决 |
|------|------|
| `No device connected` | 检查 USB 是否为 3.0 口，重新插拔 |
| 画面卡顿 | 降低分辨率或帧率 |
| 深度图有很多黑洞 | 是正常的，黑色表示无法测距的区域 |
| `pyrealsense2` 装不上 | 确认 Python 是 64 位版本 |

---

## 八、进阶方向

- 结合 IMU 数据做视觉-惯性融合
- 使用 OpenCV 的立体匹配替代 SDK 深度图
- 为 YOLO 分支扩展自定义目标类别或分割模型
- 将 VFH 输出接入 ROS / 机器人底盘实现真实运动控制
