"""WatchRec server download/report cycle. No ASR or GPU modules are imported."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote, urlsplit

import requests
from tasks import read, save


def validate_id(file_id):
    if not isinstance(file_id, str) or not file_id:
        raise ValueError("服务器返回了无效的录音标识")
    parts = file_id.split("/")
    if any(p in ("", ".", "..") or p.endswith((".", " ")) or re.search(r'[<>:"\\|?*\x00-\x1f]', p) for p in parts):
        raise ValueError("服务器录音路径不安全")
    if any(p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]} for p in parts):
        raise ValueError("服务器录音名称无效")
    return file_id


class ServerClient:
    def __init__(self, base_url, token, certificate=""):
        self.base_url = base_url.rstrip("/")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("录音服务器地址无效")
        if not token:
            raise ValueError("请配置录音服务器访问令牌")
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["Authorization"] = "Bearer " + token
        self.session.verify = certificate or True

    def pending(self):
        response = self.session.get(self.base_url + "/pending", timeout=(15, 30))
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list):
            raise ValueError("服务器返回的录音列表无效")
        return rows

    def report(self, file_id, transcript):
        response = self.session.post(self.base_url + "/result", params={"id": quote(validate_id(file_id), safe="")},
            json={"transcript": transcript, "raw": transcript, "language": "auto"}, timeout=(15, 30))
        response.raise_for_status()

    def download(self, file_id, root, expected_size=None, progress=lambda done, total: None):
        file_id = validate_id(file_id)
        root = Path(root).resolve()
        destination = (root / file_id).resolve()
        destination.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if expected_size is not None and (not isinstance(expected_size, int) or expected_size <= 0):
            raise ValueError("服务器音频大小无效")
        marker = Path(str(destination) + ".download.json")
        verified_size = expected_size or read(marker).get("size_bytes")
        if destination.is_file() and verified_size and destination.stat().st_size == verified_size:
            progress(verified_size, verified_size)
            return destination
        partial = Path(str(destination) + ".part")
        last_error = None
        for attempt in range(3):
            offset = partial.stat().st_size if partial.exists() else 0
            # A response can finish just before the process is interrupted.
            if expected_size and offset == expected_size:
                os.replace(partial, destination)
                save(marker, {"size_bytes": expected_size})
                progress(expected_size, expected_size)
                return destination
            if expected_size and offset > expected_size:
                partial.write_bytes(b"")
                offset = 0
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            try:
                with self.session.get(self.base_url + "/download", params={"id": quote(file_id, safe="")},
                    headers=headers, stream=True, timeout=(15, 60)) as response:
                    if response.status_code == 416 and offset:
                        partial.write_bytes(b"")
                        raise IOError("断点已失效，重新下载")
                    response.raise_for_status()
                    total = expected_size
                    if response.status_code == 206:
                        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                        if not match or int(match[1]) != offset or int(match[2]) < offset or int(match[2]) >= int(match[3]):
                            raise IOError("服务器返回的下载断点不匹配")
                        header_total = int(match[3])
                        mode = "ab"
                    elif response.status_code == 200:
                        offset = 0
                        mode = "wb"
                        length = response.headers.get("Content-Length", "")
                        header_total = int(length) if length.isdigit() else None
                    else:
                        raise IOError("服务器返回了意外的下载响应")
                    if total and header_total and total != header_total:
                        raise IOError("录音大小与服务器列表不一致，稍后重新检查")
                    total = total or header_total
                    written = offset
                    progress(written, total or 0)
                    last_update = time.monotonic()
                    with partial.open(mode) as output:
                        for chunk in response.iter_content(256 * 1024):
                            if not chunk:
                                continue
                            output.write(chunk)
                            written += len(chunk)
                            if total and written > total:
                                raise IOError("下载大小超出预期")
                            if time.monotonic() - last_update >= .25:
                                progress(written, total or 0)
                                last_update = time.monotonic()
                        output.flush()
                        os.fsync(output.fileno())
                if not total or written != total:
                    raise IOError(f"下载尚未完整：{written}/{total or '未知'} 字节")
                os.replace(partial, destination)
                save(marker, {"size_bytes": total})
                progress(total, total)
                return destination
            except (requests.RequestException, OSError, ValueError) as error:
                last_error = error
                if attempt < 2:
                    time.sleep(1)
        raise IOError(f"音频下载失败，已保留断点：{last_error}") from last_error


def recorded_at(item):
    try:
        stamp = Path(item["id"]).name[:19]
        return datetime.strptime(stamp, "%Y-%m-%d_%H-%M-%S").replace(tzinfo=timezone(timedelta(hours=8))).isoformat()
    except ValueError:
        return item.get("uploaded_at") or datetime.now(timezone.utc).isoformat()


def sync_once(data, config, emit):
    data = Path(data)
    client = ServerClient(config["server_url"], config["server_token"], config.get("server_ca_cert", ""))
    namespace = hashlib.sha256(client.base_url.encode()).hexdigest()[:16]
    downloads = data / "server-downloads" / namespace
    state = {"status": "checking", "message": "正在检查服务器录音", "pending": 0, "downloaded": 0,
        "reported": 0, "bytes": 0, "total_bytes": 0, "last_checked": None, "error": None}

    def update(**values):
        state.update(values)
        emit({"type": "status", **state})

    jobs = {}
    for path in (data / "sessions").glob("*/job.json"):
        info = read(path)
        remote = info.get("remote") or {}
        if remote.get("server") == client.base_url:
            jobs[remote["id"]] = path.parent
    update()
    # A crash after download but before queue admission must not require the
    # server to be reachable again. Only complete, verified cached files qualify.
    ready_ids = set()
    for marker in downloads.glob("**/*.download.json"):
        saved = read(marker)
        item = saved.get("item")
        if not item or saved.get("server") != client.base_url:
            continue
        file_id = validate_id(item["id"])
        if file_id in jobs:
            continue
        path = (downloads / file_id).resolve()
        path.relative_to(downloads.resolve())
        if path.is_file() and path.stat().st_size == saved.get("size_bytes"):
            emit({"type": "ready", "server": client.base_url, "path": str(path), "item": item})
            ready_ids.add(file_id)
    pending = client.pending()
    update(pending=len(pending), last_checked=datetime.now(timezone.utc).isoformat())
    pending_ids = {validate_id(item["id"]) for item in pending}
    errors = []
    # Result reports are independent of AI: a completed raw transcript is enough.
    for file_id, directory in jobs.items():
        checkpoint = read(directory / "transcription.json")
        if not checkpoint.get("finished"):
            continue
        raw = checkpoint.get("raw", "")
        text = directory / "原始转写.txt"
        if not text.is_file() or text.read_text(encoding="utf-8") != raw:
            continue
        signature = hashlib.sha256(raw.encode()).hexdigest()
        marker = directory / "server-result.json"
        if read(marker).get("signature") == signature and file_id not in pending_ids:
            continue
        try:
            update(status="reporting", message="正在回报已完成的转写")
            client.report(file_id, raw)
            save(marker, {"server": client.base_url, "id": file_id, "signature": signature, "reported_at": datetime.now(timezone.utc).isoformat()})
            pending_ids.discard(file_id)
            update(reported=state["reported"] + 1)
        except Exception as error:
            errors.append(str(error))
    for item in pending:
        file_id = validate_id(item["id"])
        if file_id.endswith("_tmp.m4a") or file_id in jobs or file_id in ready_ids:
            continue
        try:
            update(status="downloading", message="正在下载 " + Path(file_id).name, bytes=0, total_bytes=item.get("size_bytes") or 0)
            path = client.download(file_id, downloads, item.get("size_bytes"), lambda done, total: update(bytes=done, total_bytes=total))
            cached_item = {**item, "recorded_at": recorded_at(item)}
            save(Path(str(path) + ".download.json"), {"size_bytes": path.stat().st_size, "server": client.base_url, "item": cached_item})
            emit({"type": "ready", "server": client.base_url, "path": str(path), "item": cached_item})
            update(downloaded=state["downloaded"] + 1)
        except Exception as error:
            errors.append(str(error))
            update(error=str(error))
    remaining = len(pending_ids)
    message = f"服务器已连接 · {remaining} 条录音等待处理" if remaining else "服务器已连接 · 暂无待处理录音"
    if state["downloaded"]:
        message += f" · 本次下载 {state['downloaded']} 条"
    if state["reported"]:
        message += f" · 已回报 {state['reported']} 条"
    update(status="error" if errors else "idle", pending=remaining, message=message,
        error="；".join(errors) if errors else None, bytes=0, total_bytes=0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--settings", required=True)
    args = parser.parse_args()
    def emit(value):
        print(json.dumps({"watchrec_sync": value}, ensure_ascii=False), flush=True)
    try:
        sync_once(args.data, read(args.settings), emit)
    except Exception as error:
        emit({"type": "status", "status": "error", "message": "服务器同步未完成，稍后自动重试", "error": str(error)})
        return 1
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
