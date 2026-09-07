"""Run separately from test_vps.py: the two apps each have a config module."""
import json
import os
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ["WATCHREC_HOME"] = str(ROOT / ".verification" / "pytest-pc")
for name in ("APP_TOKEN", "VPS_BASE_URL", "LAN_ENABLED", "PORT"):
    os.environ.pop(name, None)
sys.path.insert(0, str(ROOT / "watchrec-server"))
from fastapi.testclient import TestClient
import connection_settings as settings
import server
from vps_client import safe_destination, VPSClient


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "FILE", tmp_path / "connection.json")


@pytest.fixture
def client():
    return TestClient(server.app, base_url="http://127.0.0.1", client=("127.0.0.1", 45123))


@pytest.mark.parametrize("url,expected", [
    (" https://rec.example.com/ ", "https://rec.example.com"),
    ("http://192.168.1.20:8765", "http://192.168.1.20:8765"),
    ("https://example.com:8443/rec/", "https://example.com:8443/rec"),
    ("http://[::1]:8765", "http://[::1]:8765"), ("", "")])
def test_urls(url, expected):
    assert settings.normalize_url(url) == expected


@pytest.mark.parametrize("url", ["example.com", "ftp://host", "http://u:p@host", "http://host:0", "http://host:99999", "https://host?q=x", "http://host/#", "http://host/a b", "http://host\\wrong"])
def test_bad_urls(url):
    with pytest.raises(ValueError): settings.normalize_url(url)


def test_first_run_no_token_and_no_outbound(client, monkeypatch):
    def unwanted(): raise AssertionError("unconfigured app contacted VPS")
    monkeypatch.setattr(server._vps, "get_pending", unwanted)
    server._poll_once("unused")
    data = client.get("/api/connection").json()
    assert data["vps_base_url"] == "" and data["token_set"] is False
    assert not data["restart_required"]
    assert client.get("/").status_code == 200
    assert client.get("/health", headers={"Authorization": "Bearer "}).status_code == 401


def test_save_preserves_secret_and_requires_restart(client):
    body = {"vps_base_url": "https://example.com/prefix/", "app_token": "test-secret-1234567890", "local_port": 19456}
    result = client.post("/api/connection", json=body)
    assert result.status_code == 200 and result.json()["restart_required"]
    assert "test-secret" not in result.text
    assert settings.read_connection()["local_port"] == 19456
    again = client.post("/api/connection", json={"app_token": ""})
    assert again.json()["token_set"]
    assert settings.read_connection()["app_token"] == body["app_token"]
    assert "app_token" not in client.get("/api/connection").json()
    assert not server._vps.configured  # current process retains its initial snapshot


def test_invalid_save_does_not_write(client):
    result = client.post("/api/connection", json={"vps_base_url": "http://localhost", "app_token": "short"})
    assert result.status_code == 400 and not settings.FILE.exists()
    assert client.post("/api/connection", json={"lan_enabled": True}).status_code == 400
    assert client.post("/api/connection", json={"local_port": 0}).status_code == 400


def test_local_ui_boundary(client):
    remote = TestClient(server.app, base_url="http://127.0.0.1", client=("192.168.1.50", 1234))
    assert remote.get("/").status_code == 403
    assert remote.get("/api/settings").status_code == 403
    assert remote.get("/health").status_code == 401
    assert client.get("/api/connection", headers={"Host": "evil.example"}).status_code == 403
    assert client.post("/api/connection", json={}, headers={"Origin": "https://evil.example"}).status_code == 403


@pytest.fixture
def relay():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.headers.get("Authorization") == "Bearer test-secret-1234567890" else 401)
            self.end_headers()
            self.wfile.write(json.dumps({"status": "alive", "service": "watchrec-vps" if self.path == "/prefix/health" else "webpage"}).encode())
        def log_message(self, *_): pass
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown(); httpd.server_close(); thread.join()


def test_connection_probe_prefix_token_and_service(client, relay):
    body = {"vps_base_url": relay + "/prefix", "app_token": "test-secret-1234567890"}
    assert client.post("/api/connection/test", json=body).json()["ok"]
    assert not settings.FILE.exists()  # testing must not save or enable synchronization
    body["app_token"] = "wrong-secret-12345678"
    assert "401" in client.post("/api/connection/test", json=body).json()["message"]
    body["app_token"] = "test-secret-1234567890"; body["vps_base_url"] = relay
    assert not client.post("/api/connection/test", json=body).json()["ok"]


@pytest.mark.parametrize("file_id", ["../secret.m4a", "../../secret.m4a", "../config.py", "settings.json"])
def test_remote_recording_paths_stay_inside_storage(tmp_path, file_id):
    with pytest.raises(ValueError): safe_destination(str(tmp_path), file_id)


def test_saved_pending_audio_keeps_integrity(tmp_path, monkeypatch):
    import vps_client
    monkeypatch.setattr(vps_client, "VPS_BASE_URL", "http://example.invalid")
    destination = tmp_path / "day" / "audio.m4a"
    destination.parent.mkdir()
    part = Path(str(destination) + ".part")
    part.write_bytes(b"abc")
    class Response:
        status_code = 206
        headers = {"Content-Range": "bytes 3-5/6"}
        def raise_for_status(self): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def iter_content(self, **_): yield b"def"
    c = VPSClient()
    def request(*args, **kwargs):
        assert kwargs["headers"] == {"Range": "bytes=3-"}
        return Response()
    monkeypatch.setattr(c.session, "get", request)
    assert Path(c.download("day/audio.m4a", str(tmp_path), 6)).read_bytes() == b"abcdef"
    assert not part.exists()
