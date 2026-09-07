#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  echo "先运行 python3 -m venv .venv，再运行 .venv/bin/pip install -r requirements.txt"
  exit 1
fi
exec .venv/bin/python server.py
