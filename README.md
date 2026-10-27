# WatchRec AI

在手表或电脑上录音，用电脑本地模型转成文字，回听、整理和导出。

当前主分支为 **1.2.0-preview · Next 界面**。桌面改为 Tauri 原生窗口，保留手表端与 VPS 中继。语音识别统一使用本地 SenseVoice；AI 全文整理、标题和翻译可选，默认关闭。

| 组件 | 目录 | 用途 |
| --- | --- | --- |
| 桌面 | `watchrec-desktop/` | 录音、导入、播放、本地转写、历史记录与导出 |
| 手表 | `watchrec-watch/` | Android 手表录音、离线保存、上传到 VPS |
| 中继 | `watchrec-vps/` | 在自己的服务器上暂存音频、接收转写结果 |

桌面可以独立使用。手表同步需要自行部署 VPS，然后在两端填写地址和访问令牌。Next 桌面通过 VPS 拉取录音；旧版电脑局域网直传接口已退役。

## 启动桌面

需要 Node.js 22、Rust stable、Python 3.10–3.12、FFmpeg。macOS 需要 Xcode Command Line Tools；Windows 需要 Visual Studio C++ Build Tools 与 WebView2。

```sh
python3 build/setup-local-asr.py ~/.watchrec-asr
cd watchrec-desktop/app
npm ci
npm run tauri dev
```

打开设置，将 Python 路径填写为安装脚本最后输出的路径。Windows 示例及构建步骤见 [构建说明](docs/build.md)。首次转写会从 ModelScope 下载 SenseVoice 与 VAD 模型，需联网并预留数 GB 空间。模型下载完成后，语音识别在本机执行。

- **开始录音**：音频分段保存到本机，停止后自动排队转写。
- **导入音频**：保存独立音频副本后转写，也支持拖入文件。
- **导入旧版录音与稿件**：选择旧版 `downloads` 文件夹，保留原文、全文、摘要和标题；待识别的音频进入本地队列。按原路径去重。
- **服务器录音同步**：默认关闭，配置后每 30 秒检查并下载新录音，转写完成后回报 VPS。
- **清晰全文与译文**：在设置中主动启用 AI，填写自己的 OpenAI 兼容接口和模型。文字会发送到该接口；原音频留在本机。自建免密接口可留空 API Key。

[桌面使用说明](docs/desktop.md) · [手表安装](docs/watch.md) · [VPS 部署](watchrec-vps/README.md)

## 录音与数据

音频和文字保存在电脑的应用数据目录。本地转写按队列逐条处理，重启后接续任务进度。数据位置和备份方式见 [桌面使用说明](docs/desktop.md#数据目录)。

## 版本与下载

Next 桌面按 [构建说明](docs/build.md) 从源码运行或打包。手表 APK、VPS 部署包和旧版 Windows 便携包见 [v1.1.0-preview 下载页](https://github.com/WWangZQ/WatchRecAI/releases/tag/v1.1.0-preview)。

第三方组件及模型信息见 [第三方声明](THIRD_PARTY_NOTICES.md)。
