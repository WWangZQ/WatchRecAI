"""Run in a separate pytest process from the desktop tests."""
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ["APP_TOKEN"] = "test-secret-1234567890"
os.environ["UPLOAD_DIR"] = str(ROOT / ".verification" / "pytest-vps")
sys.path.insert(0, str(ROOT / "watchrec-vps"))
from fastapi.testclient import TestClient
import server

AUTH = {"Authorization": "Bearer test-secret-1234567890"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "upload_dir", tmp_path)
    with TestClient(server.app) as c:
        yield c


def test_full_relay_roundtrip_and_custom_lan_port(client):
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers=AUTH).json()["service"] == "watchrec-vps"
    data = b"synthetic-audio-payload"
    uploaded = client.post("/upload", headers=AUTH, files={"file": ("recording_1700000000000_1000.m4a", data, "audio/mp4")})
    assert uploaded.status_code == 200
    rid = uploaded.json()["id"]
    pending = client.get("/pending", headers=AUTH).json()
    assert len(pending) == 1 and pending[0]["size_bytes"] == len(data)
    downloaded = client.get("/download", headers=AUTH, params={"id": rid})
    assert downloaded.content == data
    partial = client.get("/download", headers={**AUTH, "Range": "bytes=5-"}, params={"id": rid})
    assert partial.status_code == 206 and partial.content == data[5:]
    assert client.post("/result", headers=AUTH, params={"id": rid}, json={"transcript": "测试"}).status_code == 200
    assert client.get("/pending", headers=AUTH).json() == []
    assert client.post("/lan-info", headers=AUTH, json={"lan_ip": "192.168.1.2", "port": 19999}).status_code == 200
    assert client.get("/lan-info", headers=AUTH).json()["port"] == 19999


def test_unsafe_paths_rejected(client):
    assert client.get("/download", headers=AUTH, params={"id": "../../.env"}).status_code == 400
    assert client.post("/result", headers=AUTH, params={"id": "../outside.m4a"}, json={}).status_code == 400


def test_cleanup_removes_completed_audio_and_keeps_pending(client):
    for status in ("transcribed", "uploaded"):
        audio = server.upload_dir / (status + ".m4a")
        audio.write_bytes(b"test")
        Path(str(audio) + ".meta.json").write_text(json.dumps({"status": status,
            "uploaded_at": (datetime.now(timezone.utc)-timedelta(days=10)).isoformat()}))
    server._cleanup_expired()
    assert not (server.upload_dir / "transcribed.m4a").exists()
    assert (server.upload_dir / "uploaded.m4a").exists()
