import os
import time
from pathlib import Path

import requests
from urllib.parse import quote

from config import APP_TOKEN, VPS_BASE_URL


def safe_destination(dest_dir: str, file_id: str) -> Path:
    root = Path(dest_dir).resolve()
    dest = (root / file_id).resolve()
    if not dest.is_relative_to(root) or dest.suffix.lower() != ".m4a":
        raise ValueError("VPS 返回了无效的录音相对路径")
    return dest


class VPSClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {APP_TOKEN}"
        self.session.trust_env = False  # 禁用系统代理
        self.session.verify = True  # 系统信任链与主机名校验

    @property
    def configured(self) -> bool:
        return bool(VPS_BASE_URL and APP_TOKEN)

    def get_pending(self) -> list[dict]:
        """GET /pending → 待转写列表。"""
        if not self.configured:
            return []
        resp = self.session.get(f"{VPS_BASE_URL}/pending", timeout=30, allow_redirects=False)
        resp.raise_for_status()
        return resp.json()

    def download(self, file_id: str, dest_dir: str, expected_size: int | None = None) -> str:
        """
        流式下载音频到 dest_dir，返回本地文件路径。
        file_id 如 "2026-06-04/2026-06-04_18-53-50_486997.m4a"
        """
        encoded_id = quote(file_id, safe="")
        dest_path = safe_destination(dest_dir, file_id)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        part_path = Path(str(dest_path) + ".part")

        # 公网 NAT 链路偶尔会在大文件中途断开。保留 .part 并使用 Range 续传，
        # 只有完整大小通过校验后才原子切换为最终文件。
        last_error: Exception | None = None
        for attempt in range(3):
            offset = part_path.stat().st_size if part_path.exists() else 0
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            try:
                with self.session.get(
                    f"{VPS_BASE_URL}/download",
                    params={"id": encoded_id},
                    headers=headers,
                    # 连续 60 秒没有新字节就重连；已写入的部分会通过 Range 续传。
                    timeout=(15, 60),
                    stream=True,
                ) as resp:
                    resp.raise_for_status()

                    # 若服务端忽略 Range 返回 200，必须从头重写，避免重复拼接。
                    resumed = offset > 0 and resp.status_code == 206
                    if offset and not resumed:
                        offset = 0
                    mode = "ab" if resumed else "wb"

                    content_range = resp.headers.get("Content-Range", "")
                    if "/" in content_range:
                        total_text = content_range.rsplit("/", 1)[-1]
                        if total_text.isdigit():
                            expected_size = int(total_text)
                    elif resp.headers.get("Content-Length", "").isdigit():
                        expected_size = offset + int(resp.headers["Content-Length"])

                    with open(part_path, mode) as f:
                        for chunk in resp.iter_content(chunk_size=256 * 1024):
                            if chunk:
                                f.write(chunk)

                actual_size = part_path.stat().st_size
                if expected_size is not None and actual_size != expected_size:
                    raise IOError(
                        f"下载不完整: {actual_size}/{expected_size} bytes"
                    )

                os.replace(part_path, dest_path)
                return str(dest_path)
            except Exception as e:
                last_error = e
                if attempt < 2:
                    time.sleep(1)

        raise IOError(f"下载失败（已保留断点 {part_path}）: {last_error}") from last_error

    def post_result(self, file_id: str, transcript: str, raw: str, language: str) -> bool:
        """POST /result?id= 回报转写结果，成功返回 True。"""
        encoded_id = quote(file_id, safe="")
        resp = self.session.post(
            f"{VPS_BASE_URL}/result",
            params={"id": encoded_id},
            json={"transcript": transcript, "raw": raw, "language": language},
            timeout=30,
        )
        resp.raise_for_status()
        return True

    def report_lan_info(self, lan_ip: str, port: int):
        """POST /lan-info 上报局域网信息。"""
        resp = self.session.post(
            f"{VPS_BASE_URL}/lan-info",
            json={"lan_ip": lan_ip, "port": port},
            timeout=10,
        )
        resp.raise_for_status()

    def clear_lan_info(self):
        """DELETE /lan-info 清除局域网信息。"""
        if not self.configured:
            return
        try:
            resp = self.session.delete(f"{VPS_BASE_URL}/lan-info", timeout=10)
            resp.raise_for_status()
        except Exception as e:
            print(f"  ⚠ clear_lan_info failed: {e}")
