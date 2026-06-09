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

> 手表端 Android 工程位于 `watchrec-watch/`，以下命令均在该目录下执行。

### 方式 A：使用一键脚本（推荐）

```bash
cd watchrec-watch
chmod +x install.sh
./install.sh
```

脚本会自动：检测 adb 连接 → 编译 → 安装 → 失败时给出排查提示。

### 方式 B：手动编译安装

```bash
cd watchrec-watch

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

三端各自独立成目录：

```
WatchRecApp/
├── README.md
├── .gitignore
│
├── watchrec-watch/                         # 手表端（Android 工程）
│   ├── install.sh                          # 一键编译安装
│   ├── build.gradle.kts                    # 根构建脚本
│   ├── settings.gradle.kts
│   ├── gradle.properties
│   ├── local.properties                    # SDK 路径（自动生成）
│   ├── gradlew / gradlew.bat
│   ├── server.crt                          # VPS 自签名证书（钉扎用）
│   └── app/
│       ├── build.gradle.kts                # 模块构建脚本
│       └── src/main/
│           ├── AndroidManifest.xml
│           ├── res/                        # 资源文件（drawable/layout/values）
│           └── java/com/watchrec/app/
│               ├── MainActivity.kt         # 录音主界面
│               ├── RecordingListActivity.kt# 录音列表 + 播放
│               ├── RecordingService.kt     # 前台录音服务
│               ├── adapter/                # RecyclerView 适配器
│               ├── model/                  # 数据模型
│               ├── player/                 # MediaPlayer 封装
│               ├── recorder/               # 文件命名工具
│               ├── uploader/               # 上传逻辑 + 选路 + SSL
│               └── util/                   # 文件/时间/手势工具
│
├── watchrec-vps/                           # VPS 端（公网中转）
│   ├── server.py                           # 接收上传 + 供电脑端轮询
│   ├── config.py
│   └── requirements.txt
│
└── watchrec-server/                        # 电脑端（轮询 VPS + 本地转写）
    ├── server.py                           # FastAPI 主服务
    ├── vps_client.py                       # 从 VPS 拉取音频
    ├── transcriber.py                      # FunASR 转写
    ├── config.py                           # 配置（端口、存储路径）
    ├── requirements.txt
    ├── start.sh                            # 一键启动
    └── uploads/                            # 接收到的音频存放目录
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

### 桌面双击启动（推荐）

不想敲命令行 + 手动开网页，可用桌面入口 `desktop.py`：双击即在一个进程里
拉起服务（局域网接收 + VPS 轮询 + GPU 转写）并弹出一个**无地址栏的独立应用窗口**
显示查看界面，无黑色命令行窗口。服务状态/转写进度/日志直接显示在窗口内
（右上角状态点 + 「日志」面板），关闭窗口即停止服务。

```bash
# 直接跑（会弹应用窗口）
python desktop.py
```

实现要点：

- 窗口宿主优先用 **Chrome 的 `--app` 模式**（本机实测稳定留窗），其次 Edge，
  都没有则回退默认浏览器。需要本机装有 Chrome。
- 用 conda `ics` 环境的 **`pythonw`** 静默启动（无控制台）。
- `WatchRec.vbs` 是仓库内的静默启动器；桌面快捷方式可直接指向
  `pythonw.exe desktop.py`（工作目录设为 `watchrec-server`）。
- 状态/日志接口：`GET /api/status`、`GET /api/logs?since=<id>`。

### 配置手表端服务器地址

修改 `watchrec-watch/app/src/main/java/com/watchrec/app/uploader/Config.kt`：

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
  "transcript": "去标记的逐字稿（原文）",
  "raw": "带情感/事件标记的原始输出",
  "full_text": "AI 去噪整理后的通顺全文（未配置 LLM 时为 null）",
  "summary": "AI 总结（未配置 LLM 时为 null）",
  "transcribed_at": "2026-06-04 14:31:05"
}
```

> 电脑端轮询 VPS 时会自动转写所有缺少 `.json` 的音频，无需手动补转。

---

## 九、AI 整理（去噪全文 + 总结）

转写得到的是逐字稿，含口头禅、重复、错别字。可选接一层 **OpenAI 兼容的在线 LLM**，把它整理成可读「全文」并生成「AI 总结」：

```
原文（逐字稿） ──AI 去噪──▶ 全文（通顺可读） ──AI 提炼──▶ AI 总结（精炼有重点）
```

查看界面每条录音按 **AI 总结 / 全文 / 原文 / 原始标记（折叠）** 四层展示。

### 配置 LLM（两种方式，二选一）

**方式 A：页面里填（推荐，保存后立即生效、长期保留）**

打开查看界面右上角 **「设置」** → 填 Base URL、API Key、模型 → 保存。配置写入 `watchrec-server/settings.json`（已 gitignore，含密钥不会进仓库），重启服务依然保留。

**方式 B：`.env` 文件**

```ini
# watchrec-server/.env
LLM_BASE_URL=https://你的服务/v1
LLM_API_KEY=sk-xxxx
LLM_MODEL=gpt-4o-mini
```

> 优先级：页面保存的 `settings.json` > `.env`。两者都没配时，**只做转写**，全文/总结留空，不影响其它功能。

### 生成时机

- **新录音**：转写完成后自动去噪 + 总结，写入边车 JSON。
- **已有录音**：在详情页点 **「用 AI 生成全文与总结」** 按钮（调 `POST /api/enrich?id=`）按需补生成。

### 相关接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/api/settings` | 读取 LLM 配置（不回传 API Key 明文，只给 `api_key_set`） |
| POST | `/api/settings` | 保存 LLM 配置（API Key 留空 = 不修改，保留已存的） |
| POST | `/api/enrich?id=` | 对单条录音重新生成全文与总结 |

---

## 十、手动上传音频

不止手表录音 —— 任意音频文件都能拖进来转写。查看界面右上角 **「上传」** → 选文件即可。

- 支持格式：`.m4a .mp3 .wav .aac .ogg .flac .webm .mp4`
- 上传后走和手表录音**完全相同**的流水线：转写 → AI 去噪全文 → AI 总结
- 文件存为 `downloads/<日期>/<时间>_manual.<原扩展名>`，录制时间记为上传时刻
- 转写完成后自动出现在左侧列表（界面会轮询等待，约几秒~数十秒）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/upload` | 手动上传音频（multipart/form-data，本地查看页用，无需 token） |
