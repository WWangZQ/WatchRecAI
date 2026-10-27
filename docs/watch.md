# 手表安装与连接

APK 适用于支持 Android 应用的手表，界面按 OPPO Watch 3 Pro 的方形屏幕设计。

## 安装已打包的 APK

以下命令在 **Windows 电脑的 PowerShell** 中运行。

1. 下载 Google 的 [SDK Platform Tools](https://developer.android.com/tools/releases/platform-tools)，解压，进入其中有 `adb.exe` 的文件夹。把 `WatchRec-Watch-1.1.0-preview.apk` 放进同一文件夹。
2. 手表设置 → 关于手表里连续点击版本号 7 次，回到开发者选项，开启 USB 调试。菜单名称随手表系统而异。
3. 用支持数据传输的底座或 USB 线连接手表和电脑，在手表上允许调试授权。

```powershell
.\adb.exe devices
```

设备列表里要有一行结尾为 `device`。`unauthorized` 表示还没在手表授权；没有设备则先检查底座、数据线和驱动。

```powershell
.\adb.exe install -r .\WatchRec-Watch-1.1.0-preview.apk
```

看到 `Success` 表示安装完成。若有多台设备，使用 `adb -s 设备序列号 install -r ...` 指定手表。

在手表应用列表打开 **WatchRec 开源版**，也可运行：

```powershell
.\adb.exe shell am start -n com.watchrec.opensource/com.watchrec.app.MainActivity
```

无线调试的配对和连接方式取决于手表系统，参考 [adb 官方说明](https://developer.android.com/tools/adb)。

## 第一次设置

顶部连接设置中填写 VPS 地址及连接密钥。支持域名、IP、自定义端口和反向代理前缀；始终带上 `http://` 或 `https://`。地址和密钥可通过手表系统键盘输入。

填写后点击测试连接，再点保存并返回。已保存的密钥以配置状态显示，再次编辑时留空可沿用；更换服务器时同时填写新密钥。

Next 桌面使用 VPS 同步，请在手表中填写 VPS 地址，并将旧电脑直传地址清空。旧版 v1.1.0-preview 桌面仍支持局域网直传，其安装包保留在历史发布页。

两种地址都填写时，优先尝试电脑直传，失败后回退 VPS。只填 VPS 时，也会尝试 VPS 返回的同网段电脑地址。离线时也可录音。

## 录音与保存

点击中央按钮开始录音，再点一次停止。首次使用需允许麦克风权限。列表可以播放原音，成功上传后显示标记。

应用打开时会重试待上传录音；后台任务约每小时通过Wi-Fi 等系统标记的非计费网络尝试上传，执行时间由 Android 调度。录音完成也会尝试上传，流量网络可产生流量消耗。

默认上传成功后保留天数为 `0`，表示长期保留。改为 1–365 天后，会清理已上传且录制时间超过设定天数的录音。待上传文件继续保留。开启前请确认电脑或 VPS 上已有副本。

包名为 `com.watchrec.opensource`，可与旧版 `com.watchrec.app` 同时安装。升级前请导出旧版录音；卸载 Android 应用会清除该应用的数据。

导出开源版手表里的录音到电脑：

```powershell
.\adb.exe pull /sdcard/Android/data/com.watchrec.opensource/files/recordings/ .\watchrec-backup\
```

## 首次录音

先录一条 10–20 秒语音，检查：停止后能在手表播放 → 上传标记出现 → 电脑列表出现 → 电脑播放正常并显示对应文字。再分别测试电脑关闭、手表断网后重新联网的情况。
