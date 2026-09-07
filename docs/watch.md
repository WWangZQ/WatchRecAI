# 手表安装与连接

这个 APK 安装在支持 Android 应用的手表上，没有单独的配套手机程序。录音界面主要按 OPPO Watch 3 Pro 的方形屏幕设计，其他手表请先用短录音验证权限、排版和后台上传。

## 安装已打包的 APK

以下命令都在 **Windows 电脑的 PowerShell** 中运行。无需 Android Studio 或 JDK；它们只在编译 APK 时需要。

1. 下载 Google 的 [SDK Platform Tools](https://developer.android.com/tools/releases/platform-tools)，解压，进入其中有 `adb.exe` 的文件夹。把 `WatchRec-Watch-1.1.0-preview.apk` 放进同一文件夹。
2. 手表「设置 → 关于手表」里连续点击版本号 7 次，回到开发者选项，开启 USB 调试。不同系统的名称可能不同。
3. 用支持数据传输的底座或 USB 线连接手表和电脑，在手表上允许调试授权。

```powershell
.\adb.exe devices
```

设备列表里要有一行结尾为 `device`。`unauthorized` 表示还没在手表授权；没有设备则先检查底座、数据线和驱动。

```powershell
.\adb.exe install -r .\WatchRec-Watch-1.1.0-preview.apk
```

看到 `Success` 表示安装完成。若有多台设备，使用 `adb -s 设备序列号 install -r ...`，不要把手表包装进错误的设备。

在手表应用列表打开 **WatchRec 开源版**，也可运行：

```powershell
.\adb.exe shell am start -n com.watchrec.opensource/com.watchrec.app.MainActivity
```

无线调试的配对和连接方式取决于手表系统，参考 [adb 官方说明](https://developer.android.com/tools/adb)。调试端口不是 WatchRec 服务器端口。

## 第一次设置

顶部「连接设置」中填写 VPS 地址及连接密钥。支持域名、IP、自定义端口和反向代理前缀；始终带上 `http://` 或 `https://`。输入困难时可通过手表系统支持的键盘输入，不需要重新编译。

点击「测试连接」不会保存配置；测试成功后再点「保存并返回」。密钥保存后不回显，再次打开留空会保留；更换服务器时同时填入新密钥。设置在手表重启后保留。

要只在家里用：电脑开启「允许手表在同一 Wi-Fi 下直接上传」，填好连接密钥并重启。手表可以留空 VPS 地址，只在「电脑直传地址」填 `http://电脑局域网IP:18765` 和相同密钥。电脑防火墙需允许该端口。电脑关闭或离开该 Wi-Fi 后，录音留在手表待上传。

两种地址都填写时，优先尝试电脑直传，失败后回退 VPS。只填 VPS 时，也会尝试 VPS 返回的同网段电脑地址。未配置时仍能正常录音。

## 录音与保存

点击中央按钮开始录音，再点一次停止。首次使用需允许麦克风权限。列表可以播放原音，成功上传后显示标记。

应用打开时会重试待上传录音；后台任务约每小时在不计费网络下尝试上传。Android 的省电和后台限制可能延迟任务，不能保证恰好每小时执行。录音完成也会尝试上传，流量网络可产生流量消耗。

默认「上传成功后保留天数」为 `0`，即不自动删除。改为 1–365 天后，只清理已经上传、且录制时间超过该天数的录音；不会清理待上传文件。开启前请确认电脑或 VPS 上已有副本。

新包名是 `com.watchrec.opensource`，与旧版 `com.watchrec.app` 分开。它不会自动读取或迁移旧版录音。不要为安装新版而卸载旧版；卸载 Android 应用会删除该应用自己的数据。

导出开源版手表里的录音到电脑：

```powershell
.\adb.exe pull /sdcard/Android/data/com.watchrec.opensource/files/recordings/ .\watchrec-backup\
```

## 验收

先录一条 10–20 秒语音，检查：停止后能在手表播放 → 上传标记出现 → 电脑列表出现 → 电脑播放正常并显示对应文字。再分别测试电脑关闭、手表断网后重新联网的情况。预览版编译成功不代表所有手表都已完成这些验证。
