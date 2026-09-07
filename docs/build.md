# 自行构建

普通使用者优先使用预览包，设置地址无需构建。下面面向开发者，所有命令在自己的干净副本里执行。

## 手表 APK

本项目的 Android Gradle Plugin 8.1.4 使用 **JDK 17** 构建。另需 Android SDK Platform 34、Build Tools 34.0.0；不要在 `local.properties` 中放连接密钥，新版已移除编译时 Token。

先设置 `JAVA_HOME` 指向 JDK 17，`ANDROID_HOME` 指向 Android SDK。在 `watchrec-watch` 中运行：

```powershell
.\gradlew.bat --no-daemon assembleDebug testDebugUnitTest
```

Linux/macOS 可用 `bash gradlew --no-daemon assembleDebug testDebugUnitTest`。输出在 `app/build/outputs/apk/debug/app-debug.apk`。应用 ID 为 `com.watchrec.opensource`，与旧版分开。

当前分发的是调试签名预览包。正式版本可在 Android Studio 的 Generate Signed Bundle / APK 流程中使用自己持有的发布密钥，之后更新保持同一密钥。密钥库及密码不进入源码包。不要为了修复签名不一致而卸载有录音的应用；先导出录音。

## Windows 便携程序

PyInstaller 的产物与构建平台有关；Windows EXE 必须在 Windows 构建，见 [官方说明](https://pyinstaller.org/en/stable/operating-mode.html)。项目使用一个完整目录分发，EXE 和 `_internal` 一起保留。

使用 Python 3.10，在仓库根目录运行：

```powershell
python -m venv .build-env
.build-env\Scripts\python.exe -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cpu
.build-env\Scripts\python.exe -m pip install -r build\requirements-desktop.txt
.\build\build-windows.ps1
```

构建前确保 `ffmpeg` 和 `ffprobe` 在 PATH 中。构建脚本会将二者复制到包内 `bin/`；请保留对应二进制版本、许可及来源信息。输出是 `dist/WatchRec/WatchRec.exe`。

只有运行库进入程序，模型在首次转写时下载，录音、`.env`、设置和证书都不打包。构建所用依赖版本见 `build/requirements-desktop.txt`；完整构建环境记录位于发行目录的 `third-party-licenses/build-environment.txt`。

诊断命令：

```powershell
dist\WatchRec\WatchRec.exe --check
dist\WatchRec\WatchRec.exe --headless
```

因为 EXE 无控制台，日志位于数据目录 `watchrec.log`。`--check` 只检查运行库和音频工具，不下载模型；`--headless` 只启动服务，不打开浏览器。验证录音可使用 `--transcribe-file 测试音频绝对路径`，它会在音频旁边生成转写 JSON，只对自己的测试文件使用。

## 自动测试与压缩包

电脑和 VPS 有同名模块，测试分两个 Python 进程运行：

```powershell
.build-env\Scripts\python.exe -m pip install pytest httpx
.build-env\Scripts\python.exe -m pytest tests\test_desktop.py -q
.build-env\Scripts\python.exe -m pytest tests\test_vps.py -q
.build-env\Scripts\python.exe build\package-release.py
```

最后一条生成手表 APK、Windows ZIP、VPS ZIP、源码 ZIP 和 `SHA256SUMS.txt`。打包脚本按源码目录选择文件，排除录音、个人设置、开发环境、构建缓存及密钥库。

发布前用一份新的数据目录做实际测试：首次设置、错误密钥、VPS 上传与下载、短录音转写、重启后的配置、局域网直传，以及手表上的后台录音和重试。再把 `dist` 中的成品作为 GitHub Release 附件上传；不要上传工作目录或已有运行时数据。
