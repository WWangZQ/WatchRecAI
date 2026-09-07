"""Generate a private .env once; never overwrite an existing installation."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parent
target = root / ".env"
if target.exists():
    raise SystemExit(".env 已存在，保留原配置。请使用文本编辑器修改。")
token = secrets.token_urlsafe(32)
with target.open("x", encoding="utf-8") as f:
    f.write(f"APP_TOKEN={token}\nHOST=127.0.0.1\nPORT=8765\nTIMEZONE=Asia/Shanghai\nRETENTION_DAYS=3\n")
target.chmod(0o600)
print("已生成 .env（仅保存在本机）。查看其中 APP_TOKEN，填到手表和电脑的连接密钥栏。")
