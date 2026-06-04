#!/usr/bin/env bash
# WatchRec 音频接收服务 — 一键启动脚本
set -e
cd "$(dirname "$0")"

echo "=== WatchRec Server ==="

# 检查 Python
if ! command -v python &>/dev/null && ! command -v python3 &>/dev/null; then
    echo "[ERROR] 未找到 Python，请先安装 Python 3.8+"
    exit 1
fi
PYTHON=$(command -v python3 2>/dev/null || command -v python)

# 检查并安装依赖
echo "检查依赖..."
$PYTHON -c "import fastapi, uvicorn" 2>/dev/null || {
    echo "安装依赖..."
    pip install -r requirements.txt || pip3 install -r requirements.txt
}

# 启动服务
$PYTHON server.py
