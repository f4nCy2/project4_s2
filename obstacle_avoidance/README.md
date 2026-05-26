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

### 3.1 运行基础查看器（先看画面）
```powershell
python src/basic_viewer.py
```
按 `Q` 退出。这会显示：
- 左侧：RGB 彩色图像
- 右侧：深度伪彩色图像

### 3.2 运行障碍物检测
```powershell
python src/obstacle_detection.py
```
显示：深度图中识别出的障碍物区域（绿色框）。

### 3.3 运行避障系统（完整版）
```powershell
python src/obstacle_avoidance.py
```
显示：
- 上方：RGB + 障碍物标记
- 下方：距离热力图 + 避障建议文字

---

## 四、项目结构

```
d435i_obstacle_avoidance/
├── README.md                  # 本文件
├── requirements.txt           # Python 依赖
├── src/
│   ├── basic_viewer.py        # 基础画面查看
│   ├── obstacle_detection.py  # 障碍物检测
│   └── obstacle_avoidance.py  # 避障主程序
└── docs/
    └── algorithm.md           # 算法原理说明
```

---

## 五、避障原理简介

1. **深度图获取**：D435i 主动红外投射 + 双目计算，获取每个像素的距离（单位：米）
2. **ROI 划分**：将画面水平分为左/中/右三个区域
3. **距离统计**：计算每个区域的平均距离
4. **障碍物判断**：若某区域平均距离 < 安全阈值，判定为有障碍物
5. **避障决策**：
   - 中间近 → 停止 / 后退
   - 左边近 → 右转
   - 右边近 → 左转
   - 都远 → 前进

---

## 六、参数调节

编辑 `src/obstacle_avoidance.py` 顶部的参数：

```python
SAFE_DISTANCE = 1.0      # 安全距离（米），根据场景调整
WARNING_DISTANCE = 2.0   # 警告距离（米）
ROI_WIDTH_RATIO = 0.33   # 左/中/右区域宽度比例
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
- 加入目标检测（YOLO）识别障碍物类型
- 接入机器人底盘实现真实避障控制
