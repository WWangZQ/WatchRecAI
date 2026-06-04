#!/usr/bin/env bash
# WatchRec 音频接收服务 — 一键启动脚本
# 使用 conda ics 环境
set -e
cd "$(dirname "$0")"

echo "=== WatchRec Server ==="

# 激活 conda ics 环境
CONDA_ENV="ics"
CONDA_BASE=$(conda info --base 2>/dev/null)
if [ -z "$CONDA_BASE" ]; then
    echo "[ERROR] 未找到 conda，请先安装 Anaconda/Miniconda"
    exit 1
fi

source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV"
echo "  环境：$CONDA_ENV ($(python --version 2>&1))"

# 检查关键依赖
echo "  检查依赖..."
python -c "import fastapi, uvicorn, funasr, torch" 2>/dev/null || {
    echo "  安装依赖..."
    pip install -r requirements.txt
}

# 检查 GPU
python -c "
import torch
if torch.cuda.is_available():
    print(f'  GPU: {torch.cuda.get_device_name(0)} ({torch.cuda.get_device_properties(0).total_memory // 1024**2} MB)')
else:
    print('  ⚠ GPU 不可用，转写将使用 CPU（非常慢）')
"

# 启动服务
python server.py
