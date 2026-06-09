#!/usr/bin/env bash
# ================================================================
# WatchRec — 一键编译安装脚本
# 用于 OPPO Watch 3 Pro (ColorOS Watch)
# ================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APK_PATH="$SCRIPT_DIR/app/build/outputs/apk/debug/app-debug.apk"
ADB="adb"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ── 1. 检测 adb ──────────────────────────────────────────────────
if ! command -v "$ADB" &>/dev/null; then
    error "adb 未找到，请确认 Android SDK platform-tools 已加入 PATH。"
    echo "    下载地址: https://developer.android.com/studio/releases/platform-tools"
    exit 1
fi

info "adb 版本: $(adb version | head -1)"

# ── 2. 检测设备连接 ──────────────────────────────────────────────
DEVICE_COUNT=$(adb devices | grep -cw "device" || true)
if [ "$DEVICE_COUNT" -lt 1 ]; then
    error "未检测到已连接的设备。"
    echo ""
    echo "  排查步骤："
    echo "    1. 确认手表已开启「开发者选项」和「USB 调试」"
    echo "    2. 用磁吸充电底座的 USB 线连接电脑"
    echo "    3. 手表上弹出「允许 USB 调试？」时点「允许」"
    echo "    4. 运行 adb devices 确认设备出现"
    echo ""
    echo "  开启开发者选项的方法："
    echo "    设置 → 关于手表 → 连续点击「版本号」7 次"
    echo "    返回设置 → 开发者选项 → 开启「USB 调试」"
    exit 1
fi

info "已连接设备:"
adb devices -l | grep "device " | grep -v "List"
echo ""

# 确认设备 API Level
SDK_VERSION=$(adb shell getprop ro.build.version.sdk 2>/dev/null || echo "unknown")
info "设备 API Level: $SDK_VERSION"

# ── 3. 编译 ──────────────────────────────────────────────────────
info "开始编译 (assembleDebug) ..."
cd "$SCRIPT_DIR"

if [ ! -f "./gradlew" ]; then
    error "gradlew 不存在，请确认在项目根目录运行此脚本。"
    exit 1
fi

chmod +x ./gradlew

if ! ./gradlew assembleDebug; then
    error "编译失败。"
    echo ""
    echo "  排查步骤："
    echo "    1. 确认已安装 JDK 17+:  java -version"
    echo "    2. 确认 ANDROID_HOME 或 local.properties 中 sdk.dir 正确"
    echo "    3. 确认已安装 Android SDK Platform 34 和 Build-Tools 34.0.0"
    echo "    4. 尝试 ./gradlew assembleDebug --stacktrace 查看详细错误"
    exit 1
fi

info "编译成功！"

# ── 4. 安装 ──────────────────────────────────────────────────────
if [ ! -f "$APK_PATH" ]; then
    error "APK 文件不存在: $APK_PATH"
    echo "    编译可能未成功，请检查 build 输出。"
    exit 1
fi

info "安装 APK 到设备 ..."
if adb install -r "$APK_PATH"; then
    echo ""
    info "安装成功！"
    info "启动命令: adb shell am start -n com.watchrec.app/.MainActivity"
    echo ""
else
    error "安装失败。"
    echo ""
    echo "  排查步骤："
    echo "    1. 确认手表 USB 调试授权未过期（重新插拔线缆）"
    echo "    2. 如果提示 INSTALL_FAILED_OLDER_SDK，说明设备 API < 26，此应用不支持"
    echo "    3. 如果提示 INSTALL_FAILED_UPDATE_INCOMPATIBLE，先卸载旧版:"
    echo "       adb uninstall com.watchrec.app"
    echo "    4. 存储空间不足时清理手表空间"
    echo "    5. ColorOS Watch 可能需要通过 ADB sideload 模式安装:"
    echo "       adb sideload $APK_PATH"
    exit 1
fi
