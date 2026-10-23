"""Small on-demand helper. Startup never imports FunASR or PyTorch."""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import sys
import threading


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default if default is not None else {}


def save(path, data):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def emit(data):
    print(json.dumps({"watchrec": data}, ensure_ascii=False), flush=True)


def error_kind(error):
    import requests
    import llm
    if isinstance(error, requests.HTTPError):
        status = error.response.status_code if error.response is not None else None
        return "network" if status == 429 or status is not None and status >= 500 else ""
    if isinstance(error, (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError, llm.StreamInterruptedError)):
        return "network"
    return ""


def enrich(directory, report=emit, max_parts=None):
    import llm
    import composition
    import time
    from settings import get_llm
    directory = Path(directory)
    raw = (directory / "原始转写.txt").read_text(encoding="utf-8").strip()
    config = get_llm()
    if not config["ai_enabled"] or not config["llm_base_url"] or not config["llm_model"]:
        raise ValueError("请先在设置中启用 AI 并填写接口和模型")
    if not raw:
        raise ValueError("这条录音没有识别文字")
    signature = hashlib.sha256("\0".join((raw, config["llm_model"],
        config["llm_base_url"], Path(llm.__file__).read_text(encoding="utf-8"),
        Path(composition.__file__).read_text(encoding="utf-8"))).encode()).hexdigest()
    path = directory / "enrichment.json"
    state = read(path)
    if state.get("signature") != signature:
        if state:
            previous = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            backup = directory / "ai-history" / previous
            backup.mkdir(parents=True, exist_ok=True)
            for name in ("enrichment.json", "全文.txt", "总结.txt"):
                source = directory / name
                if source.is_file() and not (backup / name).exists():
                    shutil.copy2(source, backup / name)
        state = {"signature": signature, "parts": [], "full_text": "", "summary": "",
                 "headline": "", "source_preserved_parts": [], "draft_text": ""}
    state.update(pipeline=composition.VERSION)
    if not state.get("source_chunks"):
        state["source_chunks"] = composition.split_source(raw)

    def update(**values):
        state.update(values)
        save(path, state)
        report({k: v for k, v in state.items() if k not in ("parts", "signature", "source_chunks")})

    def checkpoint(index, value, total):
        parts = state.get("parts", [])
        if len(parts) != total:
            parts = [None] * total
        parts[index] = value
        preserved = set(state.get("source_preserved_parts", []))
        if isinstance(value, llm.PreservedSource):
            preserved.add(index)
        update(parts=parts, source_preserved_parts=sorted(preserved),
               draft_text=composition.render_parts(parts, preserved),
               done=sum(bool(p) for p in parts), total=total, retry_count=0, retry_after=0)

    last_preview = 0.0
    def preview(text, index, total):
        nonlocal last_preview
        state["draft_text"] = text
        now = time.monotonic()
        if now - last_preview >= 0.25:
            update(draft_text=text, done=index, total=total)
            last_preview = now

    try:
        update(status="running", phase="AI 对照校对", error=None, error_kind="", retry_after=0, draft_text="")
        if not state.get("full_text"):
            full = composition.compose(raw, resume_parts=state.get("parts"),
                                       checkpoint=checkpoint, on_text=preview, max_parts=max_parts,
                                       source_chunks=state["source_chunks"],
                                       source_preserved_parts=state.get("source_preserved_parts"))
            if full is None:
                update(status="queued", phase="本段已保存，等待继续校对",
                       draft_text=composition.render_parts(state["parts"], state.get("source_preserved_parts", [])))
                return state
            # full_text is committed only after every source passage is complete.
            update(full_text=full, summary=full, draft_text="")
        if not state.get("headline"):
            update(phase="生成标题", done=0, total=1)
            preserved = set(state.get("source_preserved_parts", []))
            title_input = "\n\n".join(p for i, p in enumerate(state.get("parts", [])) if p and i not in preserved) if preserved else state["full_text"]
            headline_source = "model"
            title_rejected = False
            try:
                headline = llm.headline(title_input) if title_input.strip() else None
            except llm.ContentRejectedError:
                headline = None
                title_rejected = True
            if not headline and (title_rejected or not title_input.strip()):
                job = read(directory / "job.json")
                headline = job.get("title") or "录音笔记"
                headline_source = "recording"
            if not headline:
                raise ValueError("AI 未返回标题，请重试")
            update(headline=headline, headline_source=headline_source)
        for name, key in (("全文.txt", "full_text"), ("总结.txt", "summary")):
            output = directory / name
            temporary = output.with_suffix(".txt.tmp")
            temporary.write_text(state[key], encoding="utf-8")
            os.replace(temporary, output)
        update(status="done", phase="已完成", done=1, total=1, parts=[], draft_text="", retry_count=0, retry_after=0)
        return state
    except Exception as error:
        update(status="error", error=str(error), error_kind=error_kind(error))
        raise


def local_transcribe(directory):
    directory = Path(directory)
    job = read(directory / "job.json")
    if job.get("microphone"):
        archive = read(directory / "recording.json")
        if not archive.get("closed"):
            raise ValueError("请先停止录音，再使用本地模型转写")
        paths = [directory / p["file"] for p in archive["parts"]]
    else:
        paths = [directory / job["source_file"]]
    emit({"status": "running", "phase": "正在加载本地模型"})
    # This is the sole entry point that loads the local model.
    from transcriber import transcribe_files
    results = []
    for index, path in enumerate(paths):
        emit({"status": "running", "phase": "本地转写", "done": index, "total": len(paths)})
        results.extend(transcribe_files([str(path)]))
    result = {"raw": "\n".join(r.get("transcript", "") for r in results),
              "duration_seconds": sum(r.get("duration_sec") or 0 for r in results)}
    save(directory / "local-result.json", result)
    emit({"status": "done", "phase": "本地转写完成", **result})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["enrich", "local", "translate"])
    parser.add_argument("--target", choices=["zh", "en"])
    parser.add_argument("--directory", required=True)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--max-parts", type=int)
    args = parser.parse_args()
    if args.max_parts is not None and args.max_parts < 1:
        parser.error("--max-parts must be positive")
    os.environ["WATCHREC_SETTINGS"] = args.settings
    try:
        if args.mode == "enrich":
            enrich(args.directory, max_parts=args.max_parts)
        elif args.mode == "translate":
            from translation import translate_recording
            translate_recording(args.directory, args.target)
        else:
            local_transcribe(args.directory)
    except Exception as error:
        emit({"status": "error", "error": str(error), "phase": "处理未完成"})
        return 1
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
