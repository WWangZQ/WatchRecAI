"""Opt-in local integration test with a synthetic M4A, never a live relay/watch.

Usage: python tests/e2e_preview.py synthetic.m4a model-cache-directory
Starts a temporary VPS and the built EXE on free loopback ports, verifies bytes,
real ASR output and result acknowledgement, then stops only its own processes.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import requests

ROOT = Path(__file__).resolve().parents[1]
audio = Path(sys.argv[1]).resolve()
models = Path(sys.argv[2]).resolve()
assert audio.is_relative_to(ROOT / ".verification") and models.is_relative_to(ROOT / ".verification")
run = ROOT / ".verification" / ("e2e-" + str(int(time.time())))
run.mkdir(parents=True)
pc_home = run / "pc"
pc_home.mkdir()
token = "integration-test-key-12345678"


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


vps_port, pc_port = port(), port()
assert vps_port != pc_port
vps_url, pc_url = f"http://127.0.0.1:{vps_port}", f"http://127.0.0.1:{pc_port}"
env = {k: v for k, v in os.environ.items() if k not in {"APP_TOKEN", "VPS_BASE_URL", "PYTHONPATH", "PYTHONHOME", "LLM_API_KEY", "LLM_BASE_URL"}}
env["PYTHONIOENCODING"] = "utf-8"
vps_env = {**env, "APP_TOKEN": token, "HOST": "127.0.0.1", "PORT": str(vps_port), "UPLOAD_DIR": str(run / "relay-recordings")}
pc_env = {**env, "WATCHREC_HOME": str(pc_home), "MODELSCOPE_CACHE": str(models), "PATH": str(Path(os.environ["SystemRoot"]) / "System32")}
(pc_home / "connection.json").write_text(json.dumps({"vps_base_url": vps_url, "app_token": token,
    "local_port": pc_port, "lan_enabled": False, "lan_ip_override": "", "asr_device": "cpu"}))
auth = {"Authorization": f"Bearer {token}"}
session = requests.Session(); session.trust_env = False
processes = []
logs = []


def start(args, cwd, environment, name):
    log = (run / (name + ".log")).open("wb"); logs.append(log)
    p = subprocess.Popen(args, cwd=cwd, env=environment, stdout=log, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    processes.append(p)
    return p


def wait_ready(url, process):
    for _ in range(90):
        if process.poll() is not None:
            raise RuntimeError(f"Test process exited: {process.returncode}; logs: {run}")
        try:
            if session.get(url + "/health", headers=auth, timeout=1).status_code == 200: return
        except requests.RequestException: pass
        time.sleep(1)
    raise RuntimeError("Test process did not become ready")


try:
    vps = start([sys.executable, "server.py"], ROOT / "watchrec-vps", vps_env, "vps")
    pc = start([str(ROOT / "dist/WatchRec/WatchRec.exe"), "--headless"], ROOT, pc_env, "desktop")
    wait_ready(vps_url, vps); wait_ready(pc_url, pc)
    raw = audio.read_bytes()
    uploaded = session.post(vps_url + "/upload", headers=auth, files={"file": (
        f"recording_{int(time.time()*1000)}_7070.m4a", raw, "audio/mp4")}, timeout=10)
    uploaded.raise_for_status(); rid = uploaded.json()["id"]
    deadline = time.monotonic() + 300
    transcript = None
    while time.monotonic() < deadline:
        try:
            response = session.get(pc_url + "/api/recording", params={"id": rid}, timeout=15)
        except requests.exceptions.ReadTimeout:
            continue  # first imports/model initialization can briefly occupy the CPU
        if response.status_code == 200:
            result = response.json()
            if result.get("error"): raise RuntimeError(f"Transcription failed: {result['error']}")
            transcript = result.get("transcript")
        if transcript and not session.get(vps_url + "/pending", headers=auth, timeout=2).json(): break
        time.sleep(2)
    else:
        raise RuntimeError(f"Pipeline timed out; logs in {run}")
    assert "recording" in transcript.lower(), transcript
    downloaded = session.get(pc_url + "/api/audio", params={"id": rid}, timeout=10)
    downloaded.raise_for_status()
    assert downloaded.content == raw
    summary = {"recording_id": rid, "audio_sha256": hashlib.sha256(raw).hexdigest(),
        "transcript": transcript, "vps_acknowledged": True, "standalone_exe": True,
        "model_cache": "seeded copy in verification directory", "run_dir": str(run)}
    (run / "result.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
finally:
    for p in processes:
        if p.poll() is None:
            p.terminate()
            try: p.wait(timeout=15)
            except subprocess.TimeoutExpired: p.kill(); p.wait()
    for log in logs: log.close()
    session.close()
