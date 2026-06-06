#!/usr/bin/env bash
# 本地开发启动脚本
# 生产环境请用 systemd，见 README
set -e
cd "$(dirname "$0")"

if [ -z "$APP_TOKEN" ]; then
    if [ -f .env ]; then
        export $(grep -v '^#' .env | xargs)
    else
        echo "ERROR: APP_TOKEN not set. Copy .env.example to .env and fill in a token."
        exit 1
    fi
fi

pip install -r requirements.txt 2>/dev/null
python server.py
