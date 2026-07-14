#!/bin/bash
# Gazebo 仿真一键启动脚本（roslaunch 版，确保 gazebo_ros 插件加载）
# 用法: ./run_simulation.sh [主控电脑IP]
# 例如: ./run_simulation.sh 192.168.1.100

set -e

# 默认主控电脑 IP
DEFAULT_HOST="192.168.1.100"
MAIN_HOST="${1:-$DEFAULT_HOST}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ── 检查 ROS 环境 ──
if [ -z "$ROS_DISTRO" ]; then
    if [ -f "/opt/ros/noetic/setup.bash" ]; then
        source "/opt/ros/noetic/setup.bash"
    else
        echo "未找到 ROS Noetic。请先安装 ROS Noetic"
        exit 1
    fi
fi

# Source 工作空间
if [ -f "$HOME/catkin_ws/devel/setup.bash" ]; then
    source "$HOME/catkin_ws/devel/setup.bash"
fi

echo "=================================="
echo "  Gazebo 仿真环境启动"
echo "=================================="
echo "  ROS 版本: $ROS_DISTRO"
echo "  主控电脑: $MAIN_HOST:8080"
echo "  TCP 监听: 0.0.0.0:9090"
echo "=================================="
echo ""

# ── 设置环境变量 ──
export ROBOT_HOST="0.0.0.0"
export ROBOT_PORT="9090"
export GAZEBO_ODOM_TOPIC="/odom"
export GAZEBO_CMD_TOPIC="/cmd_vel"
export GAZEBO_MODEL_NAME="robot"
export ENABLE_CAMERA="true"
export VISION_WS_URL="ws://$MAIN_HOST:8080/ws/robot"

# ── 步骤1: 用 roslaunch 启动 Gazebo（自动加载 gazebo_ros 插件）──
WORLD_FILE="$PROJECT_DIR/worlds/obstacle_world.world"
echo "启动 Gazebo（自动加载 ROS 插件）..."
roslaunch gazebo_ros empty_world.launch \
    world_name:="$WORLD_FILE" \
    paused:=false \
    use_sim_time:=true \
    gui:=true \
    headless:=false \
    debug:=false &
GAZEBO_PID=$!

# 等待 Gazebo 完全启动
MAX_WAIT=60
for i in $(seq 1 $MAX_WAIT); do
    if rosservice list 2>/dev/null | grep -q "/gazebo/spawn_urdf_model"; then
        echo "Gazebo spawn 服务已就绪"
        break
    fi
    echo "  等待 Gazebo 初始化... ($i/$MAX_WAIT)"
    sleep 1
done

# 检查是否真的就绪
if ! rosservice list 2>/dev/null | grep -q "/gazebo/spawn_urdf_model"; then
    echo "Gazebo spawn 服务未就绪，请检查 Gazebo 是否正常运行"
    kill $GAZEBO_PID 2>/dev/null || true
    exit 1
fi

# ── 步骤2: 加载 URDF 到参数服务器并 spawn 机器人 ──
echo "加载机器人模型并 spawn..."
URDF_FILE="$PROJECT_DIR/robot_description/urdf/robot.urdf"

# 用 Python API 加载 URDF 到参数服务器（绕过 rosparam 的 YAML 解析）
python3 -c "
import rospy
rospy.init_node('urdf_loader', anonymous=True)
with open('$URDF_FILE', 'r') as f:
    rospy.set_param('robot_description', f.read())
print('[urdf_loader] robot_description loaded')
"

# 然后 spawn
rosrun gazebo_ros spawn_model \
    -file "$URDF_FILE" \
    -urdf \
    -model robot \
    -x 0.0 -y 0.0 -z 0.1 -Y 0.0

echo "机器人 spawn 成功！"

# ── 步骤3: 启动 robot_state_publisher ──
echo "启动 TF 发布..."
rosrun robot_state_publisher robot_state_publisher &
RSP_PID=$!

# ── 步骤4: 启动 Gazebo 桥接脚本 ──
echo "启动 Gazebo 桥接 (TCP 9090)..."
cd "$PROJECT_DIR"
python3 scripts/gazebo_bridge.py &
BRIDGE_PID=$!

# ── 信号处理 ──
_cleanup() {
    echo ""
    echo "正在关闭仿真..."
    kill $BRIDGE_PID 2>/dev/null || true
    kill $RSP_PID 2>/dev/null || true
    kill $GAZEBO_PID 2>/dev/null || true
    rosnode kill -a 2>/dev/null || true
    killall gzserver gzclient 2>/dev/null || true
    echo "仿真已关闭"
}
trap _cleanup SIGINT SIGTERM

echo ""
echo "仿真已启动！"
echo ""
echo "连接信息:"
echo "  后端 -> 仿真: TCP $MAIN_HOST:9090"
echo "  仿真 -> 后端: WS $MAIN_HOST:8080/ws/robot"
echo ""
echo "操作指南:"
echo "  - 在主控电脑浏览器打开 http://$MAIN_HOST:8080/control"
echo "  - 在调度器创建任务，机器人将在 Gazebo 中执行"
echo "  - 按 Ctrl+C 停止仿真"
echo ""

# 等待
wait $BRIDGE_PID
wait $GAZEBO_PID
