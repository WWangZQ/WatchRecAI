# 从源码构建 Next 桌面

## 前置工具

- Node.js 22 与 npm，Rust stable。
- Python 3.10–3.12。先安装本地转写依赖，再在应用设置中指定该环境的 Python。
- FFmpeg 与 ffprobe 位于 PATH。macOS 从 Finder 打开的应用可用 PATH 较少；建议在终端启动或为运行环境提供可找到的工具路径。
- macOS：Xcode Command Line Tools。
- Windows：Visual Studio C++ Build Tools 与 WebView2。

## 本地 ASR 环境

macOS 示例：

```sh
python3 build/setup-local-asr.py ~/.watchrec-asr
```

Windows PowerShell 示例：

```powershell
py -3.12 build/setup-local-asr.py "$env:LOCALAPPDATA\WatchRec-ASR"
```

将脚本输出的 Python 路径填入 WatchRec 设置。桌面包附带处理脚本，转写依赖安装在上述 Python 环境中，模型在首次转写时下载。

## 开发与打包

```sh
cd watchrec-desktop/app
npm ci
npm run tauri dev
```

macOS 应用包：

```sh
npm run tauri build -- --bundles app
```

输出位于 `watchrec-desktop/target/release/bundle/macos/WatchRec.app`。麦克风用途声明和录音 entitlement 随应用打包。商业签名、公证和发行证书由维护者单独配置。

Windows 安装器：

```powershell
./build/build-windows.ps1
```

输出位于 `watchrec-desktop/target/release/bundle/nsis/`。Windows 和 Linux 的运行验证状态见 [验证记录](validation.md)。

## 验证与源码归档

```sh
python -m pip install pytest requests fastapi httpx python-multipart uvicorn tzdata python-dotenv
python -m pytest tests/test_desktop.py -q
python -m pytest tests/test_vps.py -q
cargo test --workspace --manifest-path watchrec-desktop/Cargo.toml
python build/package-release.py
```

桌面和 VPS Python 测试分别运行，避免二者的同名模块干扰。真实本地模型检查：

```sh
cargo run --manifest-path watchrec-desktop/Cargo.toml -p voiceink --example transcribe -- /tmp/watchrec-fresh-test /absolute/synthetic.wav /absolute/asr-env/bin/python /absolute/repo/watchrec-desktop
```

为测试指定一个新的数据目录和合成音频。此命令使用桌面的转写队列、处理脚本和结果保存流程。

`package-release.py` 在 `dist/` 中生成源码包、VPS 部署包和 SHA-256 校验文件。
