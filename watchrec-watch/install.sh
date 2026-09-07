#!/usr/bin/env bash
# Explicit installation helper. It only targets the selected device.
set -euo pipefail
cd "$(dirname "$0")"
command -v adb >/dev/null || { echo "请先安装 Android Platform Tools。"; exit 1; }
serial="${1:-}"
if [[ -z "$serial" ]]; then
  echo "先运行 adb devices 查看序列号，再运行：bash install.sh 设备序列号"
  adb devices
  exit 1
fi
bash gradlew assembleDebug
adb -s "$serial" install -r app/build/outputs/apk/debug/app-debug.apk
printf '%s\n' "安装成功。请在手表打开 WatchRec 开源版，在连接设置填写地址和密钥。"
