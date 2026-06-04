"""WatchRec 音频接收服务 — 配置文件"""

# 服务端口
PORT = 8765

# 上传文件存储目录（相对于本文件所在目录）
UPLOAD_DIR = "uploads"

# 时区
TIMEZONE = "Asia/Shanghai"

# ── 转写参数 ──────────────────────────────────────────────

# FunASR batch_size_s：按音频秒数控制批大小，越大 GPU 利用率越高，显存占用也越大
BATCH_SIZE_S = 300

# 单批最多取几个文件（防止显存溢出的硬上限）
MAX_BATCH_FILES = 16
