from __future__ import annotations

import json
import re

import httpx

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from app.content_loader import ContentLibrary, Question

SYSTEM_PROMPT = """你是「芯片设计入门手册」的导学助手，面向行业小白。
规则：
1. 只用检索到的教材内容回答，不要编造流程步骤、协议时序或工具命令。
2. 先给直观结论，再补流程位置和应用场景。
3. 每条关键判断后面用（出处：篇章 / 章节）标注。
4. 超纲就明确说教材还没讲到，并推荐最近的章节。
5. 中文回答，短段落，必要时用条目。
"""


def agent_enabled() -> bool:
    return bool(LLM_API_KEY)


def retrieve_context(library: ContentLibrary, query: str, limit: int = 4) -> list[dict]:
    hits = library.search(query, limit=limit)
    payload = []
    for chunk in hits:
        payload.append(
            {
                "chapter_id": chunk.chapter_id,
                "title": chunk.title,
                "part_title": chunk.part_title,
                "path": chunk.path,
                "excerpt": library.excerpt(chunk.chapter_id, 520),
            }
        )
    return payload


def next_unread(library: ContentLibrary, read_ids: set[str]) -> dict | None:
    for chapter in library.ordered:
        if chapter.id not in read_ids:
            return {
                "chapter_id": chapter.id,
                "title": chapter.title,
                "part_title": chapter.part_title,
                "minutes": chapter.minutes,
                "excerpt": library.excerpt(chapter.id, 360),
                "reason": "按手册顺序，这是下一章未读内容。",
            }
    return None


def pick_question(library: ContentLibrary, hint: str = "") -> Question | None:
    if hint:
        hits = library.search(hint, limit=3)
        for chunk in hits:
            questions = library.questions_by_chapter.get(chunk.chapter_id) or []
            if questions:
                return questions[0]
    for chapter in library.ordered:
        questions = library.questions_by_chapter.get(chapter.id) or []
        if questions:
            return questions[0]
    return None


async def reply(
    library: ContentLibrary,
    message: str,
    mode: str,
    read_ids: set[str],
    history: list[dict] | None = None,
) -> dict:
    mode = mode if mode in {"ask", "guide", "quiz"} else "ask"
    citations = retrieve_context(library, message or "芯片设计流程", limit=4)
    guide = next_unread(library, read_ids) if mode in {"guide", "ask"} else None
    question = pick_question(library, message) if mode == "quiz" else None

    if not agent_enabled():
        return _offline_reply(mode, message, citations, guide, question)

    user_block = _build_user_block(mode, message, citations, guide, question)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in (history or [])[-6:]:
        if item.get("role") in {"user", "assistant"} and item.get("content"):
            messages.append({"role": item["role"], "content": str(item["content"])[:2000]})
    messages.append({"role": "user", "content": user_block})

    try:
        text = await _chat(messages)
    except Exception as exc:
        return {
            "ok": False,
            "mode": mode,
            "answer": f"模型调用失败，已回退到教材检索。原因：{exc}",
            "citations": citations,
            "guide": guide,
            "question": _public_q(question) if question else None,
            "offline": True,
        }

    return {
        "ok": True,
        "mode": mode,
        "answer": text,
        "citations": citations,
        "guide": guide,
        "question": _public_q(question) if question else None,
        "offline": False,
    }


def _offline_reply(
    mode: str,
    message: str,
    citations: list[dict],
    guide: dict | None,
    question: Question | None,
) -> dict:
    lines = ["当前未配置 LLM_API_KEY，智能体以降级检索模式回答。", ""]
    if mode == "guide" and guide:
        lines.append(f"建议下一章：《{guide['part_title']} / {guide['title']}》。")
        lines.append(guide["excerpt"])
    elif mode == "quiz" and question:
        lines.append(f"自测：{question.question}")
        for idx, option in enumerate(question.options):
            lines.append(f"{idx}. {option}")
        lines.append("配置大模型后，我可以讲解对错并追问。")
    elif citations:
        lines.append("和你问题最接近的教材段落：")
        for item in citations:
            lines.append(f"- {item['part_title']} / {item['title']}：{item['excerpt'][:160]}")
    else:
        lines.append("没有检索到直接对应的章节。可以先从《行业全景 / 什么是芯片》读起。")
    if message and "axi" in message.lower() and citations:
        lines.append("若在对比 AXI 与 APB，请优先打开「片上互连 AXI AHB APB」。")
    return {
        "ok": True,
        "mode": mode,
        "answer": "\n".join(lines),
        "citations": citations,
        "guide": guide,
        "question": _public_q(question) if question else None,
        "offline": True,
    }


def _build_user_block(
    mode: str,
    message: str,
    citations: list[dict],
    guide: dict | None,
    question: Question | None,
) -> str:
    bits = [f"模式：{mode}", f"学员问题：{message or '（空）'}", "", "检索到的教材："]
    if not citations:
        bits.append("（无命中）")
    for item in citations:
        bits.append(f"### {item['part_title']} / {item['title']}（{item['path']}）")
        bits.append(item["excerpt"])
        bits.append("")
    if guide:
        bits.append(f"未读推荐：{guide['part_title']} / {guide['title']}")
        bits.append(guide["excerpt"])
    if question:
        bits.append("题库抽题（不要直接把答案选项编号当唯一讲解）：")
        bits.append(question.question)
        bits.append(json.dumps(question.options, ensure_ascii=False))
        bits.append(f"正确答案索引：{question.answer}；解析：{question.explanation}")
    if mode == "guide":
        bits.append("请根据未读推荐给出学习顺序、为什么先读它、读完应能回答的 2 个问题。")
    elif mode == "quiz":
        bits.append("请用这道题检验学员；若学员已作答，先判对错再讲。")
    else:
        bits.append("请直接回答学员问题，并引用上面的章节。")
    return "\n".join(bits)


async def _chat(messages: list[dict]) -> str:
    url = f"{LLM_BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"}
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.3,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    return data["choices"][0]["message"]["content"].strip()


def _public_q(question: Question | None) -> dict | None:
    if not question:
        return None
    return {
        "id": question.id,
        "chapter": question.chapter,
        "question": question.question,
        "options": question.options,
    }


def looks_like_choice(text: str) -> int | None:
    text = text.strip()
    if re.fullmatch(r"[0-3]", text):
        return int(text)
    mapping = {"a": 0, "b": 1, "c": 2, "d": 3, "甲": 0, "乙": 1}
    return mapping.get(text.lower())
