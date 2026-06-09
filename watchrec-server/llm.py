"""
AI 整理：调用 OpenAI 兼容的在线 API。

两步：
  denoise(原文逐字稿) → 通顺可读的「全文」
  summarize(全文)     → 精炼有重点的「AI 总结」

配置见 config.py 的 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL（从 .env 读）。
未配置时 is_configured() 返回 False，调用方应跳过整条 AI 链路。
"""

import requests

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

_DENOISE_SYS = (
    "你是中文语音转写整理助手。用户给你的是语音识别得到的逐字稿，"
    "通常有口头禅、重复、结巴、停顿词、同音错别字、缺标点。"
    "请把它整理成通顺、可读、忠实于原意的文字："
    "去掉口头禅和无意义重复，纠正明显的同音错别字，补全标点并合理分段；"
    "但不要增删原意、不要扩写、不要总结、不要加入原文没有的信息。"
    "只输出整理后的正文，不要任何解释或前后缀。"
)

_SUMMARY_SYS = (
    "你是笔记总结助手。用户会给你一段已经整理通顺的口语转写正文。"
    "请输出精炼的中文总结：提炼核心要点、关键信息，并点出其中有价值的想法（idea）。"
    "用简短的分点，突出重点，不要复述全文。只输出总结本身。"
)


def is_configured() -> bool:
    return bool(LLM_BASE_URL and LLM_API_KEY)


def _chat(system: str, user: str, max_tokens: int = 2048, temperature: float = 0.3) -> str:
    url = LLM_BASE_URL.rstrip("/") + "/chat/completions"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def denoise(transcript: str) -> str | None:
    """原文逐字稿 → 通顺可读的全文。"""
    text = (transcript or "").strip()
    if not text:
        return None
    return _chat(_DENOISE_SYS, text)


def summarize(full_text: str) -> str | None:
    """全文 → 精炼总结。"""
    text = (full_text or "").strip()
    if not text:
        return None
    return _chat(_SUMMARY_SYS, text)


def enrich(transcript: str) -> tuple[str | None, str | None]:
    """一步到位：返回 (全文, 总结)。任一步失败会向上抛异常。"""
    full = denoise(transcript)
    summary = summarize(full) if full else None
    return full, summary
