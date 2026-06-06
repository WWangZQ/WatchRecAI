# WatchRec VPS 接收服务

VPS 常驻中转：手表上传音频，电脑主动拉取转写并回报结果。不做转写。

## 快速开始（本地开发）

```bash
cp .env.example .env
# 编辑 .env 填入 APP_TOKEN
chmod +x start.sh && ./start.sh
```

## VPS 部署（systemd）

### 1. 上传项目到 VPS

```bash
scp -r watchrec-vps/ root@<VPS_IP>:/opt/watchrec-vps/
```

### 2. 安装依赖

```bash
ssh root@<VPS_IP>
apt update && apt install -y python3-pip python3-venv
cd /opt/watchrec-vps
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. 配置 Token

```bash
cat > /etc/watchrec-vps.env << 'EOF'
APP_TOKEN=CHANGE_ME
EOF
chmod 600 /etc/watchrec-vps.env
```

### 4. 放行防火墙

```bash
# 如果用 ufw
ufw allow 8765/tcp
ufw reload

# 如果用 iptables
iptables -A INPUT -p tcp --dport 8765 -j ACCEPT
```

> ⚠️ 必须放行，否则即使服务商端口转发配好了，VOS 自己的防火墙也会挡掉请求。

### 5. 创建 systemd 服务

```bash
cat > /etc/systemd/system/watchrec-vps.service << 'EOF'
[Unit]
Description=WatchRec VPS Receiver
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/watchrec-vps
EnvironmentFile=/etc/watchrec-vps.env
ExecStart=/opt/watchrec-vps/venv/bin/uvicorn server:app --host 0.0.0.0 --port 8765
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# ⚠️ 单 worker 约束：LAN 缓存在内存中，多 worker 会不共享。
# ExecStart 里不要加 --workers N，默认就是 1 个 worker。

systemctl daemon-reload
systemctl enable --now watchrec-vps
systemctl status watchrec-vps
```

### 6. 配置端口转发

在 VPS 服务商面板配置：

```
<SHARED_IP>:<FWD> → VPS内网IP:8765
```

配置完成后，外部通过 `http://<SHARED_IP>:<FWD>` 访问服务。

## 接口一览

| 方法 | 路径 | 谁用 | 说明 |
|------|------|------|------|
| GET | `/health` | 手表 | 健康检查 |
| POST | `/upload` | 手表 | 上传音频（流式写盘） |
| GET | `/lan-info` | 手表 | 查电脑局域网信息 |
| GET | `/pending` | 电脑 | 待转写列表 |
| GET | `/download?id=` | 电脑 | 下载音频 |
| POST | `/result?id=` | 电脑 | 提交转写结果 |
| POST | `/lan-info` | 电脑 | 上报局域网 IP |
| DELETE | `/lan-info` | 电脑 | 清除局域网信息 |

所有接口需 `Authorization: Bearer <APP_TOKEN>` 请求头。

## curl 验证

```bash
TOKEN="CHANGE_ME"
BASE="http://<SHARED_IP>:<FWD>"

# 0. 健康检查
curl -H "Authorization: Bearer $TOKEN" "$BASE/health"

# 1. 上传测试音频
# 准备一个测试文件：recording_1717500000000_5000.m4a（5秒录音）
dd if=/dev/urandom bs=1024 count=100 of=test.m4a
curl -X POST "$BASE/upload" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@test.m4a;filename=recording_1717500000000_5000.m4a"
# 预期：{"status":"ok","id":"2024-06-04/2024-06-04_19-20-00_5000.m4a"}

# 2. 无 token → 401
curl "$BASE/health"
# 预期：{"detail":"Unauthorized"}

# 3. 查看待转写列表
curl -H "Authorization: Bearer $TOKEN" "$BASE/pending"
# 预期：[{... "id":"2024-06-04/..."}]

# 4. 下载音频（id 需 URL 编码）
ID="2024-06-04/2024-06-04_19-20-00_5000.m4a"
ENCODED_ID=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$ID', safe=''))")
curl -H "Authorization: Bearer $TOKEN" "$BASE/download?id=$ENCODED_ID" -o out.m4a
ls -la out.m4a

# 5. 提交假转写结果
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"transcript":"这是一段测试转写文本","raw":"<raw>","language":"zh"}' \
  "$BASE/result?id=$ENCODED_ID"
# 再查 pending，该条应消失
curl -H "Authorization: Bearer $TOKEN" "$BASE/pending"

# 6. LAN info 上报 → 查询 → 清除
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"lan_ip":"192.168.1.100","port":8765}' \
  "$BASE/lan-info"

curl -H "Authorization: Bearer $TOKEN" "$BASE/lan-info"
# 预期：{"lan_ip":"192.168.1.100","port":8765}

curl -X DELETE -H "Authorization: Bearer $TOKEN" "$BASE/lan-info"

curl -H "Authorization: Bearer $TOKEN" "$BASE/lan-info"
# 预期：{"lan_ip":null}
```

## 约束

- **单 worker**：uvicorn 必须跑单 worker（默认），LAN 信息在内存中，多 worker 不共享。
- **流式写盘**：`/upload` 用 `shutil.copyfileobj` 流式拷贝，不读进内存，支持 255MB+ 大文件。
- **3 天清理**：只删 `status==transcribed` 且超过 3 天的文件，未转写的绝不删。
- **LAN 缓存**：内存存储，服务重启丢失，电脑下次开应用会重新上报。
