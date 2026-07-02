#!/bin/bash
# 启动机器人控制中心后端
# 使用 .project4 虚拟环境

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$PROJECT_ROOT/.project4/bin/activate"

echo "=================================="
echo "  人形机器人控制中心启动脚本"
echo "=================================="

if [ ! -f "$VENV" ]; then
    echo "❌ 虚拟环境未找到: $VENV"
    echo "请先创建 .project4 虚拟环境"
    exit 1
fi

source "$VENV"

cd "$PROJECT_ROOT"

# 检查依赖
python3 -c "import fastapi, uvicorn, websockets, pydantic, numpy" 2>/dev/null || {
    echo "⚠️ 依赖缺失，正在安装..."
    pip install fastapi uvicorn websockets pydantic numpy
}

echo ""
echo "🚀 启动 WebSocket 服务器..."
echo "   控制界面: http://127.0.0.1:8080/control"
echo "   调度界面: http://127.0.0.1:8080/scheduler"
echo "   API:      http://127.0.0.1:8080/"
echo ""

python3 -m backend.server
