"""可断点续跑的 AI 整理流水线。每个耗 token 的结果完成后立即原子落盘。"""

import hashlib
import json
import os
import threading
from pathlib import Path

from runtime_state import enrich_clear, enrich_get, enrich_set

_VERSION = 1
_active_guard = threading.Lock()
_active: set[str] = set()
_pipeline_lock = threading.Lock()  # 自动与手动请求共用一条流水线，避免叠加消耗 token。


def _atomic_json(path: Path, data: dict) -> None:
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def checkpoint_path(json_path: Path) -> Path:
    # 不使用 .json 后缀，避免被录音列表的 rglob("*.json") 当成录音边车。
    return json_path.with_suffix(".enrich-checkpoint")


def invalidate_enrichment(rid: str, json_path: Path) -> None:
    """原文被人工修改后废弃旧断点；下次生成基于新原文重新开始。"""
    checkpoint_path(json_path).unlink(missing_ok=True)
    enrich_clear(rid)


def _load_checkpoint(json_path: Path, transcript: str) -> tuple[Path, dict]:
    path = checkpoint_path(json_path)
    digest = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
    state = {}
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    if state.get("version") != _VERSION or state.get("transcript_sha256") != digest:
        state = {
            "version": _VERSION,
            "transcript_sha256": digest,
            "status": "idle",
            "phase": "AI 去噪",
            "done": 0,
            "total": 0,
            "parts": [],
            "error": None,
        }
        _atomic_json(path, state)
    return path, state


def persisted_status(rid: str, json_path: Path) -> dict:
    live = enrich_get(rid)
    if live:
        return live
    path = checkpoint_path(json_path)
    if not path.exists():
        return {"status": "idle"}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        # 进程退出时留下的 running 实际应显示为“可继续”，不能冒充仍在运行。
        if state.get("status") == "running":
            state["status"] = "paused"
        return {k: state.get(k) for k in ("status", "phase", "done", "total", "error")}
    except Exception:
        return {"status": "idle"}


def run_enrichment(
    rid: str, json_path: Path, force: bool = False, force_summary: bool = False
) -> bool:
    """整理一条录音。返回 False 表示同一录音已有任务在运行。"""
    with _active_guard:
        if rid in _active:
            return False
        _active.add(rid)
    try:
        with _pipeline_lock:
            return _run_locked(rid, json_path, force, force_summary)
    finally:
        with _active_guard:
            _active.discard(rid)


def _run_locked(rid: str, json_path: Path, force: bool, force_summary: bool) -> bool:
    try:
        from llm import denoise, headline, summarize

        data = json.loads(json_path.read_text(encoding="utf-8"))
        transcript = (data.get("transcript") or "").strip()
        if not transcript:
            raise ValueError("该录音没有原文，无法生成")

        cp_path, state = _load_checkpoint(json_path, transcript)
        if force:
            state.update({"parts": [], "done": 0, "total": 0, "error": None})
            data.update({"full_text": None, "summary": None, "headline": None})
            _atomic_json(json_path, data)
        elif force_summary:
            data.update({"summary": None, "headline": None})
            _atomic_json(json_path, data)

        def save_state(**changes):
            state.update(changes)
            _atomic_json(cp_path, state)
            enrich_set(rid, **{k: state.get(k) for k in ("status", "phase", "done", "total", "error")})

        save_state(status="running", phase="AI 去噪", error=None)

        if not data.get("full_text"):
            def progress(done, total):
                save_state(status="running", phase="AI 去噪", done=done, total=total, error=None)

            def checkpoint(index, value, total):
                parts = state.get("parts")
                if not isinstance(parts, list) or len(parts) != total:
                    parts = [None] * total
                parts[index] = value
                save_state(parts=parts, done=sum(1 for p in parts if p), total=total)

            full = denoise(
                transcript,
                progress=progress,
                resume_parts=state.get("parts"),
                checkpoint=checkpoint,
            )
            data["full_text"] = full
            _atomic_json(json_path, data)
        else:
            full = data["full_text"]

        if not data.get("summary"):
            save_state(status="running", phase="AI 总结", done=0, total=1, error=None)
            data["summary"] = summarize(full)
            _atomic_json(json_path, data)
            save_state(status="running", phase="AI 总结", done=1, total=1, error=None)

        if not data.get("headline"):
            save_state(status="running", phase="起标题", done=0, total=1, error=None)
            data["headline"] = headline(data.get("summary") or full)
            _atomic_json(json_path, data)

        save_state(status="done", phase="完成", done=1, total=1, error=None, parts=[])
        return True
    except Exception as e:
        try:
            state.update({"status": "error", "error": str(e)})
            _atomic_json(cp_path, state)
        except Exception:
            pass
        enrich_set(rid, status="error", error=str(e))
        raise
