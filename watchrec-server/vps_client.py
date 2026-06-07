"""
VPS HTTP 客户端。

所有请求：
- 带 Authorization: Bearer <APP_TOKEN>
- verify=<CA_CERT> 校验自签名证书
- trust_env=False 禁用系统代理（避免 Clash 等劫持）

⚠ 如果用 Clash TUN 模式，需在 Clash 配置里为 202.189.23.245 加直连规则，
  否则流量走代理会导致证书校验失败。
"""

import requests
from urllib.parse import quote

from config import APP_TOKEN, CA_CERT, VPS_BASE_URL


class VPSClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {APP_TOKEN}"
        self.session.trust_env = False  # 禁用系统代理
        self.session.verify = CA_CERT   # 自签名证书

    def get_pending(self) -> list[dict]:
        """GET /pending → 待转写列表。"""
        resp = self.session.get(f"{VPS_BASE_URL}/pending", timeout=30)
        resp.raise_for_status()
        return resp.json()

    def download(self, file_id: str, dest_dir: str) -> str:
        """
        流式下载音频到 dest_dir，返回本地文件路径。
        file_id 如 "2026-06-04/2026-06-04_18-53-50_486997.m4a"
        """
        from pathlib import Path
        encoded_id = quote(file_id, safe="")
        resp = self.session.get(
            f"{VPS_BASE_URL}/download",
            params={"id": encoded_id},
            timeout=300,
            stream=True,
        )
        resp.raise_for_status()

        dest_path = Path(dest_dir) / file_id
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=16384):
                f.write(chunk)

        return str(dest_path)

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
        try:
            resp = self.session.delete(f"{VPS_BASE_URL}/lan-info", timeout=10)
            resp.raise_for_status()
        except Exception as e:
            print(f"  ⚠ clear_lan_info failed: {e}")
