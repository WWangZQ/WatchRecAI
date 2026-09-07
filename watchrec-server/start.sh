#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  echo "先按 docs/desktop.md 安装 .venv 环境。"
  exit 1
fi
exec .venv/bin/python server.py
