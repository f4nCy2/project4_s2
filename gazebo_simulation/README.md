# Gazebo 仿真集成方案

用 **Gazebo 11 + ROS Noetic** 替换你现有的 `robot_simulator.py` 纯物理模拟，提供 **3D 可视化、真实物理碰撞、可配置障碍物场景**，来验证你的调度系统在各种真实环境下的可行性。

---

## 架构对比

### 原来的仿真（2D 自研物理）

```
主控电脑                          仿真电脑 (Ubuntu 20.04)
├─ 后端 FastAPI 8080              ├─ robot_simulator.py (TCP 9090)
├─ 前端 control/scheduler         │   自研 2D 物理引擎
│                                 ├─ obstacle_avoidance.py (D435i)
│                                 └─ vision_server.py (WS 8765)
└───── TCP 9090 ─────────────────►│
    4字节前缀 + JSON              │  ← 简单运动学，无碰撞检测
        WS 8765 ◄─────────────────┘
```

### Gazebo 仿真（3D 真实物理）

```
主控电脑                          仿真电脑 (Ubuntu 20.04)
├─ 后端 FastAPI 8080              ├─ Gazebo 11 (3D 物理引擎)
├─ 前端 control/scheduler         │   ├─ 轮式机器人模型 (URDF)
│                                 │   ├─ 差速驱动插件
│                                 │   ├─ RGB-D 相机插件 (模拟 D435i)
│                                 │   └─ 障碍物场景 (world)
│                                 │
│                                 ├─ ROS Noetic 节点
│                                 │   ├─ /cmd_vel → 控制机器人
│                                 │   ├─ /odom → 里程计反馈
│                                 │   └─ /camera/* → 视觉数据
│                                 │
│                                 └─ gazebo_bridge.py (TCP 9090)
│                                     替换 robot_simulator.py
│                                     协议完全一致，无缝兼容
│
└───── TCP 9090 ─────────────────►│  ← 真实物理碰撞、摩擦、惯性
    4字节前缀 + JSON              │  ← Odometry 闭环反馈
        WS 8765 ◄─────────────────┘  ← 相机帧实时回传
```

**关键优势**：
- **真实物理**：Gazebo 的 ODE 物理引擎，碰撞、摩擦、惯性全部真实
- **3D 可视化**：实时查看机器人在场景中的运动，比 2D 坐标更直观
- **可配置场景**：world 文件可以自由添加/修改障碍物、墙壁、地形
- **D435i 模拟**：RGB-D 相机插件直接输出 640×480 的彩色和深度图
- **零改动后端**：你的 `backend/server.py` 一行代码不用改，TCP 协议完全一致

---

## 快速开始（在 Ubuntu 20.04 仿真电脑上）

### 第一步：安装 ROS Noetic + Gazebo 11

```bash
# 1. 添加 ROS 源
sudo sh -c 'echo "deb http://packages.ros.org/ros/ubuntu $(lsb_release -sc) main" > /etc/apt/sources.list.d/ros-latest.list'

# 2. 添加密钥
sudo apt-key adv --keyserver 'hkp://keyserver.ubuntu.com:80' --recv-key C1CF6E31E6BADE8868B172B4F42ED6FBAB17C654

# 3. 更新并安装（约 2-3GB，需要一些时间）
sudo apt update
sudo apt install -y ros-noetic-desktop-full

# 4. 安装额外依赖
sudo apt install -y python3-pip python3-rosdep python3-rosinstall python3-rosinstall-generator python3-wstool build-essential
sudo apt install -y ros-noetic-gazebo-ros-pkgs ros-noetic-robot-state-publisher ros-noetic-cv-bridge

# 5. 初始化 rosdep
sudo rosdep init
rosdep update

# 6. 配置环境变量（加到 ~/.bashrc）
echo "source /opt/ros/noetic/setup.bash" >> ~/.bashrc
source ~/.bashrc
```

### 第二步：创建 ROS 工作空间

```bash
# 创建工作空间
mkdir -p ~/catkin_ws/src
cd ~/catkin_ws/src

# 把 gazebo_simulation 复制进来（从主控电脑复制到 Ubuntu 20.04）
# 假设你已经把项目复制到仿真电脑
ln -s /path/to/project4_stage2/gazebo_simulation .

# 或者：直接克隆项目
git clone <你的项目仓库>
ln -s $(pwd)/project4_stage2/gazebo_simulation ~/catkin_ws/src/

# 编译
cd ~/catkin_ws
catkin_make

# Source 工作空间（加到 ~/.bashrc）
echo "source ~/catkin_ws/devel/setup.bash" >> ~/.bashrc
source ~/catkin_ws/devel/setup.bash
```

### 第三步：安装 Python 依赖

```bash
cd ~/catkin_ws/src/gazebo_simulation/scripts
pip3 install websockets opencv-python numpy
```

### 第四步：配置网络

确保两台电脑在同一个局域网内，互通：

```bash
# 在 Ubuntu 20.04 上查自己的 IP
ip addr show | grep "inet " | head -2

# 在 Mac 主控电脑上查自己的 IP
ifconfig | grep "inet " | head -2
```

**修改环境变量**（在 `run_simulation.sh` 中或手动 export）：

```bash
# 主控电脑 IP（后端监听地址）
export MAIN_HOST="192.168.1.100"  # 改成你的 Mac IP

# 仿真电脑 IP（后端连接的目标）
export TCP_HOST="192.168.1.200"   # 改成你的 Ubuntu IP
export TCP_PORT="9090"
```

### 第五步：启动仿真

```bash
cd ~/catkin_ws/src/gazebo_simulation/scripts

# 方式1: 一键启动（推荐）
./run_simulation.sh 192.168.1.100

# 方式2: 手动分步启动（调试用）
# 终端1: 启动 Gazebo + 机器人
roslaunch gazebo_simulation bringup.launch

# 终端2: 启动桥接脚本
source /opt/ros/noetic/setup.bash
source ~/catkin_ws/devel/setup.bash
python3 gazebo_bridge.py
```

Gazebo 窗口会弹出，你会看到：
- 一个蓝色底盘、白色头部的轮式机器人
- 灰色地面
- 红色/绿色/蓝色/黄色的障碍物
- 灰色墙壁围成的场景

### 第六步：主控电脑启动后端

```bash
# 在 Mac 上，在项目根目录
export TCP_HOST="192.168.1.200"  # Ubuntu 仿真电脑 IP
export TCP_PORT="9090"
export VISION_WS_URL="ws://192.168.1.200:8765"
./start.sh
```

浏览器打开 `http://127.0.0.1:8080/control` 和 `http://127.0.0.1:8080/scheduler`

---

## 操作验证

1. **控制面板**：点击动作按钮（前进、后退、转向），观察 Gazebo 中机器人是否运动
2. **任务调度**：创建任务序列（如：前进 3m → 右转 90° → 前进 2m），观察机器人按序执行
3. **避障测试**：在 Gazebo 中拖动障碍物到机器人前方，观察调度系统是否触发避障逻辑
4. **3D 可视化**：旋转 Gazebo 视角，从不同角度观察机器人运动轨迹

---

## 文件说明

```
gazebo_simulation/
├── README.md                          # 本文件
├── package.xml                        # ROS 包描述
├── robot_description/
│   └── urdf/
│       └── robot.urdf                 # 轮式机器人模型（带 RGB-D 相机）
├── worlds/
│   └── obstacle_world.world           # 带墙壁和障碍物的场景
├── launch/
│   └── bringup.launch                 # ROS launch 文件（启动 Gazebo + 机器人）
└── scripts/
    ├── gazebo_bridge.py               # 核心桥接脚本（替换 robot_simulator.py）
    └── run_simulation.sh              # 一键启动脚本
```

---

## 自定义场景

编辑 `worlds/obstacle_world.world`，添加你自己的障碍物：

```xml
<!-- 添加一个新盒子障碍物 -->
<model name="my_obstacle">
  <pose>3 1 0.25 0 0 0</pose>  <!-- x y z roll pitch yaw -->
  <static>true</static>
  <link name="link">
    <collision name="collision">
      <geometry>
        <box><size>0.5 0.5 0.5</size></box>
      </geometry>
    </collision>
    <visual name="visual">
      <geometry>
        <box><size>0.5 0.5 0.5</size></box>
      </geometry>
      <material>
        <ambient>1 0 0 1</ambient>  <!-- 红色 -->
      </material>
    </visual>
  </link>
</model>
```

或者直接在 Gazebo GUI 中操作：
- `Ctrl+B` → 插入模型
- 右键点击物体 → 移动/旋转/缩放
- `Ctrl+S` → 保存当前场景为新的 world 文件

---

## 自定义机器人模型

编辑 `robot_description/urdf/robot.urdf`：

| 修改内容 | 说明 |
|---------|------|
| `<box size="...">` | 底盘尺寸 |
| `<wheel_separation>` | 轮距（影响转弯半径） |
| `<wheel_diameter>` | 轮径（影响速度和里程计） |
| `<camera>` 中的 `<width>`/`<height>` | 相机分辨率 |
| `<horizontal_fov>` | 相机视野角 |
| `<plugin>` 中的 `<max_wheel_torque>` | 最大驱动力矩 |

---

## 从 Gazebo 获取 D435i 视觉数据

Gazebo 中的 RGB-D 相机已经模拟了 D435i：

| Topic | 类型 | 说明 |
|-------|------|------|
| `/camera/color/image_raw` | sensor_msgs/Image | 彩色图像 (640×480) |
| `/camera/color/camera_info` | sensor_msgs/CameraInfo | 相机内参 |
| `/camera/depth/image_raw` | sensor_msgs/Image | 深度图像 (32FC1, 单位: 米) |
| `/camera/depth/camera_info` | sensor_msgs/CameraInfo | 深度相机内参 |

`gazebo_bridge.py` 会自动订阅这些 topic，将彩色帧压缩为 JPEG base64，通过 WebSocket 发给后端。

**如果你想在 Ubuntu 上直接跑 D435i 避障算法**：可以写一个新的 ROS 节点，订阅 `/camera/depth/image_raw`，做 VFH 栅格避障，然后发布 `geometry_msgs/Twist` 到 `/cmd_vel`。`gazebo_bridge.py` 支持接收 d435i 发来的 `low_level_control` 指令，也会直接转发到 Gazebo。

---

## 常见问题

### Q1: Gazebo 启动黑屏/闪退
```bash
# 更新显卡驱动
sudo apt update
sudo apt install --reinstall libgl1-mesa-glx

# 或者禁用 GPU 加速
export LIBGL_ALWAYS_SOFTWARE=1
```

### Q2: 机器人不动
```bash
# 检查 ROS 话题
rostopic list | grep cmd_vel
rostopic list | grep odom

# 手动发布测试指令
rostopic pub /cmd_vel geometry_msgs/Twist "linear: {x: 0.5}"
```

### Q3: 后端连不上仿真
```bash
# 检查 Ubuntu 防火墙
sudo ufw status
sudo ufw allow 9090/tcp

# 检查端口监听
ss -tlnp | grep 9090
```

### Q4: 相机画面不显示在前端
```bash
# 检查 gazebo_bridge.py 是否连接了后端 WebSocket
# 检查 VISION_WS_URL 是否正确
# 检查后端是否配置了 VISION_WS_URL 指向 Ubuntu 电脑
```

### Q5: 动作执行完毕但机器人还在滑
```bash
# 这是 Gazebo 的物理惯性，正常现象。可以在 URDF 中增加阻尼：
# <gazebo reference="base_link">
#   <damping>0.1</damping>
# </gazebo>
```

---

## 进阶方向

1. **更真实的机器人模型**：用人形机器人 URDF（如 NAO、Pepper、Digit 开源模型）替换轮式机器人
2. **SLAM 仿真**：添加 `libgazebo_ros_laser` 激光雷达插件，接入 ROS 的 SLAM 算法（gmapping、cartographer）
3. **多机器人仿真**：在 launch 中 `spawn_model` 多次，模拟多机调度
4. **动态障碍物**：用 Gazebo 的 ModelPlugin 写移动障碍物，测试避障算法
5. **地形测试**：更换地面为斜坡、楼梯、草地（修改摩擦系数），测试不同地形下的运动能力
6. **强化学习**：用 Gazebo 作为环境，训练端到端的导航策略

---

## 协议兼容性说明

`gazebo_bridge.py` 完全兼容原有的 TCP 协议：

**接收（从后端 → 仿真）：**
- `type: "heartbeat"` → 回复心跳
- `type: "command"` → 解析 action 参数，驱动物理引擎执行
- `type: "low_level_control"` → 直接设置线速度/角速度（d435i 避障模式）
- `type: "emergency_stop"` → 立即停止机器人

**发送（从仿真 → 后端）：**
- `type: "status"` → 位姿、速度、状态、当前动作进度
- `type: "action_event"` → started / progress / completed / failed 闭环事件
- `type: "heartbeat"` → 心跳回复

**数据格式**：4 字节大端长度前缀 + UTF-8 JSON Body（与 `robot_simulator.py` 完全一致）

---

## 总结

这套 Gazebo 仿真方案让你**不需要真实机器人底盘**，就能在真实物理环境中验证调度系统的可行性。3D 可视化让你直观看到机器人的行为，可配置的障碍物场景让你测试各种边界情况，而你的后端代码**完全不需要修改**。

下一步：跑通基础仿真 → 在 world 中增加更多复杂场景 → 接入真实避障算法 → 逐步过渡到真实机器人。
