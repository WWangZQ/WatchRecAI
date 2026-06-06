"""WatchRec VPS 接收服务 — 配置"""

import os
import sys

PORT = 8765
TIMEZONE = "Asia/Shanghai"
RETENTION_DAYS = 3
LAN_TTL_SECONDS = 300  # 电脑上报局域网信息有效期（秒）

# Token 从环境变量读取，启动时校验
APP_TOKEN = os.environ.get("APP_TOKEN", "")
if not APP_TOKEN:
    print("ERROR: APP_TOKEN environment variable is not set.", file=sys.stderr)
    print("  Set it in /etc/watchrec-vps.env or export APP_TOKEN=xxx", file=sys.stderr)
    sys.exit(1)
