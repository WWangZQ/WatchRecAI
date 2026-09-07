# 电脑端：安装、设置与排错

## Windows 便携包

将 `WatchRec-Windows-1.1.0-preview.zip` 完整解压到一个普通文件夹，双击 `WatchRec.exe`。保留 EXE 旁边的 `_internal/` 目录；它包含 Python 和依赖。便携包不需要 conda，也不会安装或修改你已有的网页应用。

窗口仍使用本地网页。启动器优先找到 Chrome，再找 Edge，并用开源版独立浏览器目录打开；找不到时打开默认浏览器。不要只双击 HTML 文件，页面依赖本地服务。

默认网页地址为 `http://127.0.0.1:18765`，只在应用启动期间有效。如果用浏览器「安装此网站为应用」创建快捷方式，之后仍需先启动 `WatchRec.exe`。网页快捷方式自身不能运行 Python 服务。

**首次转写**会在本机下载 SenseVoiceSmall 与 VAD 模型。等待时间取决于网络，之后使用缓存。便携包包含 CPU 运行环境；没有 NVIDIA 显卡也能使用。GPU 加速请使用后文源码安装方式。

## 连接设置

右上角「连接与设置」可填写 VPS 地址、连接密钥，测试连接。连接密钥对应 VPS 的 `APP_TOKEN`；不是 AI API Key。

保存后，等待当前录音处理完，关闭并重新打开开源版。网页上会显示「设置已保存 · 等待重启」。这样可以让正在进行的下载和回报继续使用同一台服务器。修改端口后，浏览器快捷方式也需改成新端口。

「测试连接」只检查 `/health` 的服务标识和密钥，不上传音频，也不启用同步。遇到证书错误请修好服务器证书；程序不会关闭证书校验。

局域网直传默认关闭。开启后服务监听本机所有 IPv4 网卡的已设定端口，只有带密钥的手表上传接口可从其他设备使用；录音网页、AI 设置和连接设置仍仅允许本机访问。防火墙只需在需要直传的私人网络放行此端口。

## AI 设置

左下角「设置 → AI 设置」填入你选择的兼容 API 的 Base URL、API Key 和模型名，保存后立即生效。模型名必须由该服务支持。AI 会接收转写文字并可能产生费用；未配置时不调用它。

原有长录音分段、下载断点续传、AI 整理断点续跑保留。AI 处理中的进度和失败信息仍显示在查看页；修改转写文字会使旧的 AI 断点失效。

## 数据与退出

打包版数据位于 `%LOCALAPPDATA%\WatchRecOpenSource`；在 PowerShell 中可输入 `explorer "$env:LOCALAPPDATA\WatchRecOpenSource"` 打开。配置文件含连接密钥或 API Key，不要随截图或问题报告分享。请备份整个数据目录，特别是 `downloads/`。

源码版默认在 `watchrec-server/data/`。设置环境变量 `WATCHREC_HOME` 可以改用其他独立目录；修改这个变量不自动搬移已有数据。

正常情况下关闭独立窗口会停止服务。若浏览器把窗口交给后台进程，或使用默认浏览器回退方式，日志会提示服务继续运行，可在任务管理器结束 **开源版的 WatchRec.exe**。不要结束其他版本的 Python 或浏览器进程。处理长录音时，先等任务完成再退出。

## 从源码运行

建议 Python 3.10 或 3.11。以下在 Windows PowerShell 中、源码 `watchrec-server` 文件夹执行：

```powershell
.\setup.bat
.\start.bat
```

`setup.bat` 创建当前文件夹的 `.venv`，安装 CPU PyTorch 和依赖。源码运行还需安装 [FFmpeg](https://ffmpeg.org/download.html)，并确认 `ffmpeg -version`、`ffprobe -version` 可用。静默启动可双击 `WatchRec.vbs`。

Linux/macOS 的源码运行方式：

```bash
cd watchrec-server
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server.py
```

另用系统包管理器安装 FFmpeg，然后在**同一台电脑**浏览器中打开 `http://127.0.0.1:18765`。本轮没有制作或验收 Linux/macOS 桌面安装包。

## NVIDIA GPU（可选）

在独立 `.venv` 中安装与你的驱动兼容的 GPU 版 PyTorch 与 torchaudio，命令以 [PyTorch 官方安装选择器](https://pytorch.org/get-started/locally/)为准。先运行：

```powershell
.venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available())"
```

显示 `True` 后，在连接设置的高级选项选「自动」或「NVIDIA CUDA」，保存并重启。CPU 便携包不能仅通过下拉框变成 GPU 版；需要对应的 GPU 运行库。

## 常见问题

端口被占用时，程序会报错并退出，不复用该端口上的未知服务。关闭重复启动的开源版，或编辑开源版数据目录 `connection.json` 中的 `local_port`，改为未占用的 1024–65535 端口后再打开。不要因此关闭旧版本。

启动或转写出错时，先看数据目录的 `watchrec.log`，或者左下角「设置 → 服务日志」。模型下载失败可以检查到 ModelScope 的网络连接；CPU 转写长录音比 GPU 慢。未配置 VPS 不影响导入音频。
