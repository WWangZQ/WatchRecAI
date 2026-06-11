"""
AI 整理：调用 OpenAI 兼容的在线 API。

两步：
  denoise(原文逐字稿) → 通顺可读的「全文」
  summarize(全文)     → 精炼有重点的「AI 总结」

配置见 config.py 的 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL（从 .env 读）。
未配置时 is_configured() 返回 False，调用方应跳过整条 AI 链路。
"""

import re

import requests

from settings import get_llm

# 内容可能长达数小时，输出给满。注意：1M 会被 API 拒（400 too large），
# 当前模型(mimo)上限是 131072(128K) completion tokens，换模型如报 too large 就改这里。
# 超时也相应放大，避免长内容生成被掐断。
MAX_TOKENS = 131072
REQUEST_TIMEOUT = 1800  # 秒

_DENOISE_SYS = (
    "你是中文语音转写整理助手。用户给你的是一段语音识别逐字稿（可能是长录音里的一段），"
    "通常有口头禅、重复、结巴、停顿词、同音错别字、缺标点，"
    "还可能混入大量与对话无关的环境噪声（地铁报站、广播、店员叫卖等）。"
    "请把【需要整理的本段】整理成通顺、可读、忠实原意的文字："
    "去掉口头禅和无意义重复，纠正明显的同音错别字，补全标点并合理分段；"
    "对与说话人无关、且大段重复的广播/报站/环境噪声，可大幅精简或用一句话概括标注"
    "（如「（地铁报站略）」），不必逐字保留；确实听不清、无法还原的内容保持原样即可。"
    "不要扩写、不要总结、不要加入原文没有的信息。"
    "若提供了【上文结尾】，仅用于理解衔接，请勿重复输出它。"
    "只输出整理后的正文，不要任何解释或前后缀。"
)

_SUMMARY_SYS = (
    "你是帮人整理语音笔记的助手。用户会给你一段已经整理通顺的口语正文。"
    "请用自然、好读的中文写一段总结，就像跟朋友平实地复述这段话的要点："
    "说清楚主要讲了什么、有哪些关键信息和值得记住的想法。"
    "要连贯成段、口语化、易读；不要用 Markdown，不要小标题，不要罗列一堆短分点。"
    "篇幅与内容长短相称，保持精炼。只输出总结本身。"
)

_HEADLINE_SYS = (
    "你是给语音笔记起标题的助手。根据内容起一个简短的中文标题，6 到 14 个字，"
    "概括主题，像聊天记录的标题那样。"
    "不要标点符号、不要引号、不要书名号，只输出标题本身。"
)


# SenseVoice 会把情绪/音乐事件渲染成 emoji（😊😔🎼…），逐字稿里属噪声。
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF️‍]"
)


def _strip_input(text: str) -> str:
    """去噪前清理输入：去掉 SenseVoice 的 emoji 噪声。"""
    return _EMOJI_RE.sub("", text)


def _strip_output(text: str) -> str:
    """去噪后清理输出：去掉模型臆造的省略号（源逐字稿本无）+ 残留 emoji。"""
    if not text:
        return text
    text = _EMOJI_RE.sub("", text)
    text = re.sub(r"…+", "", text)          # 中文省略号
    text = re.sub(r"\.{3,}", "", text)       # 英文三连点
    return text.strip()


def is_configured() -> bool:
    c = get_llm()
    return bool(c["llm_base_url"] and c["llm_api_key"])


def _chat(system: str, user: str, max_tokens: int = MAX_TOKENS, temperature: float = 0.3) -> str:
    c = get_llm()
    url = c["llm_base_url"].rstrip("/") + "/chat/completions"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {c['llm_api_key']}",
            "Content-Type": "application/json",
        },
        json={
            "model": c["llm_model"],
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


# 长逐字稿分段去噪：整段重写会退化（前面认真改、后面照抄），切成 ~1000 字的小块
# 逐块清洗；每块附带前后约 200 字上下文（仅供衔接、不输出），块间并行处理（限并发）。
_CHUNK_TARGET = 1000
_CHUNK_CTX = 200
_DENOISE_CONCURRENCY = 4

_DENOISE_CHUNK_SYS = (
    "你是中文语音转写整理助手。下面给你一段语音逐字稿，其中【正文】是要整理的部分，"
    "【上文】【下文】只是前后文参考，用来让衔接自然 —— 绝对不要输出【上文】【下文】的内容。"
    "请把【正文】整理成通顺、可读、忠实于原意的中文："
    "去掉口头禅和无意义重复，纠正明显的同音错别字，补全标点并合理分段；"
    "不要增删原意、不要扩写、不要总结、不要加入原文没有的信息；"
    "遇到听不清或难懂的地方按原样保留，不要用省略号略过或省略，原文没有的省略号一律不要加。"
    "只输出整理后的【正文】，不要任何解释或标题。"
)


def _split_chunks(text: str, target: int = _CHUNK_TARGET) -> list[str]:
    """按句子边界切块，每块约 target 字；无标点的超长片段硬切。"""
    pieces = re.split(r"(?<=[。！？!?\n])", text)
    chunks: list[str] = []
    buf = ""
    for p in pieces:
        while len(p) > target:  # 罕见：单句超长，硬切
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(p[:target])
            p = p[target:]
        if buf and len(buf) + len(p) > target:
            chunks.append(buf)
            buf = p
        else:
            buf += p
    if buf.strip():
        chunks.append(buf)
    return chunks


def _denoise_chunk(prev: str, core: str, nxt: str) -> str:
    """带上下文清洗单块；只返回【正文】整理结果。"""
    parts = []
    if prev:
        parts.append(f"【上文】{prev}")
    parts.append(f"【正文】{core}")
    if nxt:
        parts.append(f"【下文】{nxt}")
    return _strip_output(_chat(_DENOISE_CHUNK_SYS, "\n".join(parts)))


def denoise(transcript: str, progress=None) -> str | None:
    """原文逐字稿 → 通顺可读的全文（长文本：小块 + 上下文 + 并行清洗）。

    progress(done, total): 可选回调，每完成一块调用一次，用于上报进度。
    """
    text = _strip_input((transcript or "").strip())
    if not text:
        return None
    chunks = _split_chunks(text, _CHUNK_TARGET)
    if len(chunks) == 1:
        if progress:
            progress(0, 1)
        out = _strip_output(_chat(_DENOISE_SYS, text))
        if progress:
            progress(1, 1)
        return out

    n = len(chunks)
    jobs = []
    for i, ch in enumerate(chunks):
        prev = chunks[i - 1][-_CHUNK_CTX:] if i > 0 else ""
        nxt = chunks[i + 1][:_CHUNK_CTX] if i < n - 1 else ""
        jobs.append((prev, ch, nxt))

    print(f"    … AI 去噪：{n} 段，并发 {_DENOISE_CONCURRENCY}")
    if progress:
        progress(0, n)

    import threading
    from concurrent.futures import ThreadPoolExecutor

    parts: list = [None] * n
    done = 0
    lock = threading.Lock()

    def run(i):
        nonlocal done
        res = _denoise_chunk(*jobs[i])
        with lock:
            parts[i] = res
            done += 1
            cur = done
        if progress:
            progress(cur, n)

    with ThreadPoolExecutor(max_workers=_DENOISE_CONCURRENCY) as ex:
        list(ex.map(run, range(n)))
    return "\n\n".join(p for p in parts if p)


def summarize(full_text: str) -> str | None:
    """全文 → 精炼总结。"""
    text = (full_text or "").strip()
    if not text:
        return None
    return _chat(_SUMMARY_SYS, text)


def headline(text: str) -> str | None:
    """内容 → 简短标题（6~14 字，无标点）。"""
    t = (text or "").strip()
    if not t:
        return None
    # 只取开头一段做依据即可（标题不需要全文）；max_tokens 用默认大值，
    # 推理模型会先耗 token 思考，给小了会导致 content 为空
    h = _chat(_HEADLINE_SYS, t[:2000], temperature=0.3)
    if not h:
        return None
    # 清理：取首行，去掉引号/书名号/首尾标点
    h = h.splitlines()[0].strip().strip("「」『』“”\"'《》 。.,，！!？?")
    return h[:20] or None


def enrich(transcript: str, progress=None) -> tuple[str | None, str | None, str | None]:
    """一步到位：返回 (全文, 总结, 短标题)。任一步失败会向上抛异常。

    progress(phase, done, total): 可选回调，phase ∈ {"denoise","summarize","headline"}。
    """
    full = denoise(transcript, progress=(lambda d, t: progress("denoise", d, t)) if progress else None)
    if progress:
        progress("summarize", 0, 1)
    summary = summarize(full) if full else None
    if progress:
        progress("headline", 0, 1)
    head = headline(summary or full) if (summary or full) else None
    return full, summary, head
