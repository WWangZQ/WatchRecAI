#!/usr/bin/env bash
# WatchRec 电脑端 — VPS 轮询 + 本地转写
set -e
cd "$(dirname "$0")"

CONDA_ENV="ics"
CONDA_BASE=$(conda info --base 2>/dev/null)
if [ -z "$CONDA_BASE" ]; then
    echo "[ERROR] conda not found"
    exit 1
fi

source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV"
echo "  Env: $CONDA_ENV ($(python --version 2>&1))"

# 检查 .env
if [ ! -f .env ]; then
    echo "[ERROR] .env not found. Copy .env.example and fill in APP_TOKEN."
    exit 1
fi

python poller.py
