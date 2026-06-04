# WatchRec — OPPO Watch 3 Pro 录音应用

## 设备信息

| 项目         | 值                                       |
| ------------ | ---------------------------------------- |
| 设备         | OPPO Watch 3 Pro                         |
| 系统         | ColorOS Watch（基于 Android 定制）        |
| 屏幕         | 1.91 英寸，480×480 方屏                   |
| 最低 API     | 26（Android 8.0）                        |
| 目标 API     | 30（Android 11）                         |
| 格式         | AAC / .m4a，44100Hz，128kbps             |
| 存储位置     | `getExternalFilesDir("recordings")`       |

---

## 一、手表开启开发者模式和 USB 调试

### 1. 开启开发者选项

1. 在手表上打开 **设置**
2. 滑到 **关于手表**
3. 连续点击 **版本号** 7 次
4. 看到提示「您已处于开发者模式」即完成

### 2. 开启 USB 调试

1. 返回 **设置**
2. 滑到底部，进入 **开发者选项**
3. 开启 **USB 调试**
4. （可选）开启 **通过 WLAN 调试**，方便无线调试

### 3. 连接电脑

1. 使用手表磁吸充电底座上的 USB 线连接电脑
2. 手表上弹出「允许 USB 调试？」时点击 **允许**
3. 运行以下命令确认设备已连接：

```bash
adb devices
# 应输出：
# List of devices attached
# XXXXXXXX    device
```

---

## 二、确认设备 API Level

```bash
adb shell getprop ro.build.version.sdk
# OPPO Watch 3 Pro 通常输出 30（对应 Android 11）

adb shell getprop ro.build.version.release
# 输出 Android 版本号，如 11
```

---

## 三、环境要求

| 工具         | 版本要求       | 检查命令                     |
| ------------ | -------------- | ---------------------------- |
| JDK          | 17+            | `java -version`              |
| Android SDK  | Platform 34, Build-Tools 34.0.0 | `sdkmanager --list`  |
| adb          | 任意最新版     | `adb version`                |

Android SDK 路径需通过以下方式之一告知项目：

- 设置环境变量 `ANDROID_HOME`
- 或在项目根目录的 `local.properties` 中写入：
  ```
  sdk.dir=/你的/SDK/路径
  ```

---

## 四、编译安装

### 方式 A：使用一键脚本（推荐）

```bash
chmod +x install.sh
./install.sh
```

脚本会自动：检测 adb 连接 → 编译 → 安装 → 失败时给出排查提示。

### 方式 B：手动编译安装

```bash
# 编译 debug APK
./gradlew assembleDebug

# 安装到手表
adb install -r app/build/outputs/apk/debug/app-debug.apk

# 启动应用
adb shell am start -n com.watchrec.app/.MainActivity
```

### 卸载

```bash
adb uninstall com.watchrec.app
```

---

## 五、UI 显示异常排查思路

### 问题：按钮超出屏幕 / 布局错乱

**原因**：OPPO Watch 3 Pro 屏幕 480px，实际 density 可能是 2.0（240dp）或 2.75（约 175dp）。

**排查步骤**：

```bash
# 查看实际 density
adb shell wm density
# 输出示例：Physical density: 320（即 2.0x）

# 如果默认 density 不准，尝试调整
adb shell wm density 320   # 设置为 2.0x
adb shell wm density reset # 恢复默认
```

本应用的录音按钮使用 `ConstraintLayout` 百分比约束（60% 屏幕宽度），理论上不受 density 影响。如果仍有问题：

```bash
# 确认实际屏幕尺寸
adb shell wm size
# 输出示例：Physical size: 480x480

# 查看当前 activity 布局（需要 Android Studio Layout Inspector 或）
adb shell dumpsys activity top | grep -A 5 "View Hierarchy"
```

### 问题：状态栏/导航栏遮挡内容

**排查**：应用使用 `SYSTEM_UI_FLAG_IMMERSIVE_STICKY` 全屏模式。如果手表系统不支持：

```bash
# 查看当前 activity
adb shell dumpsys activity activities | grep "mResumedActivity"

# 检查是否有 overlay window
adb shell dumpsys window windows | grep "StatusBar\|NavigationBar"
```

### 问题：录音无声音 / 录音文件为空

**排查**：

```bash
# 检查权限是否授予
adb shell dumpsys package com.watchrec.app | grep "permission"

# 查看录音文件
adb shell ls -la /storage/emulated/0/Android/data/com.watchrec.app/files/recordings/

# 拉取录音文件到电脑检查
adb pull /storage/emulated/0/Android/data/com.watchrec.app/files/recordings/ ./recordings/
```

### 问题：列表页显示「暂无录音」

**排查**：确认录音确实保存了：

```bash
adb shell "ls -la /sdcard/Android/data/com.watchrec.app/files/recordings/"
# 如果目录不存在或为空，说明录音未成功保存
# 检查 logcat:
adb logcat -s AudioRecorder
```

### 问题：播放无声音

手表扬声器可能音量较低。尝试：

```bash
# 调高媒体音量（需要 root 或特殊权限）
adb shell media volume --show --stream 3 --set 15

# 检查 MediaPlayer 错误
adb logcat -s AudioPlayer
```

---

## 六、项目结构

```
WatchRec/
├── install.sh                              # 一键编译安装
├── README.md
├── build.gradle.kts                        # 根构建脚本
├── settings.gradle.kts
├── gradle.properties
├── local.properties                        # SDK 路径（自动生成）
├── gradlew / gradlew.bat
├── watchrec-server/                        # 电脑端音频接收服务
│   ├── server.py                           # FastAPI 主服务
│   ├── config.py                           # 配置（端口、存储路径）
│   ├── requirements.txt
│   ├── start.sh                            # 一键启动
│   └── uploads/                            # 接收到的音频存放目录
└── app/
    ├── build.gradle.kts                    # 模块构建脚本
    └── src/main/
        ├── AndroidManifest.xml
        ├── res/                            # 资源文件
        │   ├── drawable/                   # 图标、按钮背景
        │   ├── layout/                     # 布局文件
        │   └── values/                     # 颜色、字符串、尺寸、主题
        └── java/com/watchrec/app/
            ├── MainActivity.kt             # 录音主界面
            ├── RecordingListActivity.kt    # 录音列表 + 播放
            ├── RecordingService.kt         # 前台录音服务
            ├── adapter/
            │   └── RecordingAdapter.kt     # RecyclerView 适配器
            ├── model/
            │   └── RecordingItem.kt        # 数据模型
            ├── player/
            │   └── AudioPlayer.kt          # MediaPlayer 封装
            ├── recorder/
            │   └── AudioRecorder.kt        # 文件命名工具
            ├── uploader/
            │   ├── Config.kt               # 服务器地址配置
            │   └── AudioUploader.kt        # HTTP 上传逻辑
            └── util/
                ├── FileUtils.kt            # 文件操作
                ├── TimeUtils.kt            # 时间格式化
                └── SwipeDismissFrameLayout.kt
```

---

## 七、局域网上传功能

录音完成后自动通过 WiFi 上传到电脑端。手表和电脑必须在**同一个局域网**下。

### 电脑端启动接收服务

```bash
cd watchrec-server
pip install -r requirements.txt
python server.py
# 输出：服务已启动：http://10.129.35.132:8765
```

或使用一键脚本：`./start.sh`

### 配置手表端服务器地址

修改 `app/src/main/java/com/watchrec/app/uploader/Config.kt`：

```kotlin
const val SERVER_URL = "http://10.129.35.132:8765"  // 改为你的电脑局域网 IP
```

### 上传流程

1. 录音结束后自动触发上传
2. 上传前先调 `GET /health` 检测服务器是否在线
3. 服务器不在线或上传失败 → 静默标记为「待上传」，不崩溃
4. 每次打开 App（`onResume`）自动扫描并重试所有未上传的录音
5. 上传成功后创建 `.uploaded` 标记文件，列表页显示 ✓

### API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查，返回 `{"status": "alive"}` |
| POST | `/upload` | 上传音频文件（multipart/form-data） |

### 排查

```bash
# 检查手表能否访问电脑
adb shell ping -c 3 <电脑IP>

# 查看上传日志
adb logcat -s AudioUploader

# 确认标记文件
adb shell ls /sdcard/Android/data/com.watchrec.app/files/recordings/*.uploaded
```

---

## 八、语音转写（FunASR + SenseVoice-Small）

上传的音频自动通过 FunASR SenseVoice-Small 模型转写为文字，结果存为 .json 边车文件。

### 环境要求

| 项 | 要求 |
|---|---|
| GPU | NVIDIA 显卡，VRAM ≥ 4GB（推荐 8GB+） |
| CUDA | 12.x+（需安装 GPU 版 PyTorch） |
| Python | 3.9+（用于 `zoneinfo`） |

### GPU 版 PyTorch 安装

如果 `torch.cuda.is_available()` 返回 `False`，需要安装 GPU 版：

```bash
# 查看你的 CUDA 版本
nvidia-smi  # 看右上角 CUDA Version

# 安装对应版本的 PyTorch（以 CUDA 12.6 为例）
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126

# 如果显卡较新（如 RTX 50 系列，需要 CUDA 13.0+）
pip install --pre torch torchaudio --index-url https://download.pytorch.org/whl/nightly/cu130
```

验证：
```bash
python -c "import torch; print(torch.cuda.is_available())"  # 应输出 True
```

### 依赖安装

```bash
cd watchrec-server
pip install -r requirements.txt
```

> **注意**：`editdistance` 包在 Windows 上可能编译失败（需要 C++ 编译器）。  
> FunASR 的 SenseVoice 模型实际上不依赖它。如果安装失败，可以创建一个 stub：
> ```bash
> python -c "import site; p=site.getusersitepackages()+'/editdistance'; import os; os.makedirs(p,exist_ok=True); open(p+'/__init__.py','w').write('def eval(a,b): raise NotImplementedError()')"
> ```

### 首次运行

首次启动 `server.py` 时会自动从 ModelScope 下载 SenseVoice-Small 模型（约 800MB），需要网络连接。下载完成后缓存在本地，后续启动不再下载。

### 转写结果

每条音频转写后，在同目录生成 `.json` 边车文件：

```
uploads/2026-06-04/
├── 2026-06-04_14-30-30_486997.m4a    ← 音频
└── 2026-06-04_14-30-30_486997.json   ← 转写结果
```

JSON 内容：
```json
{
  "audio_file": "2026-06-04_14-30-30_486997.m4a",
  "recorded_at": "2026-06-04 14:30:30",
  "duration_sec": 57.89,
  "language": "zh",
  "transcript": "清洗后的纯文本",
  "raw": "带情感/事件标记的原始输出",
  "transcribed_at": "2026-06-04 14:31:05"
}
```

### 批量补转已有文件

如果已有上传但未转写的音频：

```bash
cd watchrec-server
python transcribe_all.py
```

会扫描所有缺少 `.json` 的 `.m4a` 文件并逐个转写。
