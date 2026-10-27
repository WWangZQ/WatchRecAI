# VPS 部署：先拿到地址和连接密钥

VPS 负责暂存手表上传的录音。电脑连接同一台 VPS，下载音频后在本地转写。

下面选一种部署方式即可。新手建议 **Debian 12 / Ubuntu 22.04 或更新版本，Python 3.10+**。命令中的示例域名、服务器 IP 要换成自己的。

## 方式 A：Python + systemd

### 1. 上传程序

先把 `WatchRec-VPS-1.1.0-preview.zip` 下载到电脑。在 **Windows PowerShell** 中，将命令中的 `你的服务器IP` 换成实际 IP；登录用户名按实际账号填写：

```powershell
scp .\WatchRec-VPS-1.1.0-preview.zip root@你的服务器IP:/tmp/
ssh root@你的服务器IP
```

从这一行起，直到教程说明退出，命令都在 **VPS 的 Linux 终端** 里运行。若以普通用户登录，请保留 `sudo`；root 同样可以去掉 `sudo`。

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv unzip curl
sudo mkdir -p /opt/watchrec-opensource
sudo unzip /tmp/WatchRec-VPS-1.1.0-preview.zip -d /opt/watchrec-opensource
cd /opt/watchrec-opensource/watchrec-vps
python3 --version
```

确认 Python 为 3.10 或更新版本。这个目录应包含 `server.py`、`config.py`、`requirements.txt`；如果你上传的是完整源码，则将完整源码解压到相同的 `/opt/watchrec-opensource`，再进入 `watchrec-vps`。

### 2. 安装依赖和生成密钥

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
python3 setup.py
cat .env
```

最后一条命令用于你自己查看生成的配置。把 `APP_TOKEN=` 后面的完整内容保管好，稍后填到手表和电脑。脚本会随机生成密钥；已有配置可直接编辑 `.env`。

默认配置：

```dotenv
APP_TOKEN=这里是脚本生成的随机密钥
HOST=127.0.0.1
PORT=8765
TIMEZONE=Asia/Shanghai
RETENTION_DAYS=3
```

`HOST=127.0.0.1` 适合下文的 HTTPS 反向代理。`PORT` 指定录音接收服务的监听端口。

### 3. 先在前台启动，确认可用

```bash
.venv/bin/python server.py
```

服务会保持运行。在另一个 SSH 窗口执行：

```bash
cd /opt/watchrec-opensource/watchrec-vps
TOKEN=$(sed -n 's/^APP_TOKEN=//p' .env)
curl --fail -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/health
```

应得到包含 `"status":"alive"` 和 `"service":"watchrec-vps"` 的 JSON。身份校验失败会返回 401。确认后回到第一个窗口按 Ctrl+C，停止刚才的前台测试进程。

### 4. 设置开机启动

以下服务使用专用用户，路径与前面一致：

```bash
id watchrec >/dev/null 2>&1 || sudo useradd --system --no-create-home --shell /usr/sbin/nologin watchrec
sudo mkdir -p /opt/watchrec-opensource/watchrec-vps/data
sudo chown -R watchrec:watchrec /opt/watchrec-opensource/watchrec-vps
sudo chmod 600 /opt/watchrec-opensource/watchrec-vps/.env
sudo cp watchrec-opensource.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now watchrec-opensource
sudo systemctl status watchrec-opensource --no-pager
```

状态应为 `active (running)`。启动失败时查看日志：

```bash
sudo journalctl -u watchrec-opensource -n 80 --no-pager
```

修改 `.env` 后执行 `sudo systemctl restart watchrec-opensource`。多个实例应分别配置服务名、端口和数据目录。

## 给手表和电脑一个可访问的地址

### 推荐：域名 + HTTPS

1. 将域名（例如 `rec.example.com`）的 DNS A 记录指向 VPS 公网 IPv4。启用 IPv6 时，再添加对应的 AAAA 记录。
2. 在 VPS 厂商安全组及系统防火墙放行 TCP 80、443。保留已有 SSH 放行规则。
3. 按 [Caddy 官方安装说明](https://caddyserver.com/docs/install#debian-ubuntu-raspbian)安装 Caddy。
4. 在 `/etc/caddy/Caddyfile` 中追加下面的站点配置，将域名换成你的。

```caddyfile
rec.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy 会为符合条件的域名申请和续期 HTTPS 证书；要求 DNS、端口和网络正确，见 [官方自动 HTTPS 说明](https://caddyserver.com/docs/automatic-https)。

在电脑或另一台外部设备测试 `https://你的域名/health`，请求带上 `Authorization: Bearer 你的密钥`，应获得同样的 JSON。之后两端统一填写 `https://你的域名`。8765 保持本机监听，由 Caddy 提供公网入口。

### IP + 端口

可以先通过 IP 和端口测试。编辑 `.env`，将 `HOST=127.0.0.1` 改为 `HOST=0.0.0.0`，按需更改 `PORT`，重启服务。在云安全组和系统防火墙放行该 TCP 端口，再填 `http://你的公网IP:端口`。

HTTP 会明文传输录音和密钥，公网部署建议使用 HTTPS。使用 `https://IP:端口` 时，在对应端口配置 HTTPS 代理和有效的 IP 证书。

NAT VPS 要先在厂商面板做端口映射。软件填写**外部公网 IP 和映射后的外部端口**，服务的 `PORT` 仍是内部端口。连接失败时，先核对内外端口的映射关系。

如果反向代理使用 `/watchrec` 子路径，软件可填写 `https://example.com/watchrec`。Caddy 使用 `handle_path /watchrec/* { reverse_proxy 127.0.0.1:8765 }` 去掉前缀后转发。

## 方式 B：Docker Compose

服务器已安装 [Docker Engine 与 Compose](https://docs.docker.com/engine/install/)时，也可以在 `watchrec-vps` 文件夹运行：

```bash
python3 setup.py
docker compose up -d --build
docker compose logs --tail=50
```

默认绑定 `127.0.0.1:8765`，按上面的 Caddy 步骤配置 HTTPS。要直接通过 IP 访问，在 `.env` 中增加 `WATCHREC_BIND=0.0.0.0` 和所需的 `WATCHREC_PORT` 后重新运行 `docker compose up -d`。Docker 内部端口固定 8765，由 Compose 的端口映射控制外部地址。

录音保存在 Docker 命名卷。使用 `docker compose down` 停止服务并保留数据；加上 `-v` 会删除卷和其中的录音。Python 与 Docker 选择一种部署方式即可。

## 设置说明

| 项目 | 含义 |
| --- | --- |
| `APP_TOKEN` | 三端共同使用的随机连接密钥，至少 16 位英文字母/数字及 `_ . ~ -` |
| `HOST` | Python 服务监听地址，默认 127.0.0.1；直连时可改 0.0.0.0 |
| `PORT` | Python 服务监听端口，默认 8765 |
| `TIMEZONE` | 日期归档时区，默认 Asia/Shanghai |
| `RETENTION_DAYS` | 已转写录音按上传日期计算保留天数，默认 3 天，0 表示关闭清理 |
| `LAN_TTL_SECONDS` | 电脑上报局域网信息的有效时间，默认 300 秒 |
| `UPLOAD_DIR` | 可选，录音保存目录；原生部署默认 data/uploads，若更改需同步修改 systemd 的 ReadWritePaths 并赋予服务用户写权限 |

待转写录音持续保留。按录音量预留磁盘空间，长期备份放在电脑或备份存储中。

## 检查录音同步

两端保存连接设置后，在手表录制一条十几秒的音频，依次检查上传标记、电脑录音列表、原音播放和文字转写。

接口全部使用 Bearer 密钥：`GET /health`、`POST /upload`、`GET /pending`、`GET /download?id=...`、`POST /result?id=...`、`GET/POST/DELETE /lan-info`。服务状态通过 `/health` 查看，录音在桌面端查看。
