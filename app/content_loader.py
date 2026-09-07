from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import markdown
import yaml

from app.config import CONTENT_DIR

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)
MERMAID_PRE_RE = re.compile(
    r'<pre><code class="language-mermaid">(.*?)</code></pre>',
    re.S,
)


@dataclass
class Chapter:
    id: str
    part: str
    part_title: str
    part_slug: str
    part_no: int
    file: str
    title: str
    minutes: int
    prereq: list[str]
    tags: list[str]
    body: str = ""
    index: int = 0


@dataclass
class Part:
    id: str
    no: int
    slug: str
    title: str
    summary: str
    minutes: int
    chapters: list[Chapter] = field(default_factory=list)


@dataclass
class Mindmap:
    id: str
    title: str
    html: str
    part_id: str | None = None


@dataclass
class Question:
    id: str
    chapter: str
    type: str
    question: str
    options: list[str]
    answer: int
    explanation: str
    difficulty: str
    part: str = ""


@dataclass
class Chunk:
    chapter_id: str
    title: str
    part_title: str
    path: str
    text: str
    tags: list[str]


class ContentLibrary:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or CONTENT_DIR
        raw = yaml.safe_load((self.root / "curriculum.yaml").read_text(encoding="utf-8"))
        self.book = raw["book"]
        self.parts: list[Part] = []
        self.chapters: dict[str, Chapter] = {}
        self.ordered: list[Chapter] = []
        self.questions: list[Question] = []
        self.questions_by_chapter: dict[str, list[Question]] = {}
        self.mindmaps: dict[str, Mindmap] = {}
        self.chunks: list[Chunk] = []
        self._load_parts(raw["parts"])
        self._load_quizzes()
        self._load_mindmaps()

    def _load_parts(self, part_defs: list[dict]) -> None:
        idx = 0
        for pdef in part_defs:
            part = Part(
                id=pdef["id"],
                no=pdef["number"],
                slug=pdef["slug"],
                title=pdef["title"],
                summary=pdef["summary"],
                minutes=pdef["minutes"],
            )
            for cdef in pdef["chapters"]:
                path = self.root / "parts" / pdef["slug"] / cdef["file"]
                meta, body = _split_frontmatter(path.read_text(encoding="utf-8"))
                chapter = Chapter(
                    id=cdef["id"],
                    part=pdef["id"],
                    part_title=pdef["title"],
                    part_slug=pdef["slug"],
                    part_no=pdef["number"],
                    file=cdef["file"],
                    title=cdef["title"],
                    minutes=cdef["minutes"],
                    prereq=list(cdef.get("prereq") or meta.get("prereq") or []),
                    tags=list(cdef.get("tags") or []),
                    body=body.strip(),
                    index=idx,
                )
                part.chapters.append(chapter)
                self.chapters[chapter.id] = chapter
                self.ordered.append(chapter)
                self.chunks.append(
                    Chunk(
                        chapter_id=chapter.id,
                        title=chapter.title,
                        part_title=part.title,
                        path=f"{pdef['slug']}/{cdef['file']}",
                        text=_plain(chapter.body),
                        tags=chapter.tags,
                    )
                )
                idx += 1
            self.parts.append(part)

    def _load_quizzes(self) -> None:
        quiz_dir = self.root / "quizzes"
        for path in sorted(quiz_dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            part_id = data.get("part", "")
            for item in data.get("questions", []):
                q = Question(
                    id=item["id"],
                    chapter=item["chapter"],
                    type=item.get("type", "single"),
                    question=item["question"],
                    options=list(item.get("options") or []),
                    answer=int(item["answer"]),
                    explanation=item.get("explanation", ""),
                    difficulty=item.get("difficulty", "easy"),
                    part=part_id,
                )
                self.questions.append(q)
                self.questions_by_chapter.setdefault(q.chapter, []).append(q)

    def _load_mindmaps(self) -> None:
        mapping = {
            "overview": None,
            "landscape": "landscape",
            "digital-flow": "digital-flow",
            "digital-blocks": "digital-blocks",
            "fpga": "fpga",
            "analog": "analog",
            "protocols": "protocols",
            "eda-cost": "eda-cost",
            "cases": "cases",
        }
        for path in sorted((self.root / "mindmaps").glob("*.md")):
            meta, body = _split_frontmatter(path.read_text(encoding="utf-8"))
            mid = meta.get("id") or path.stem.split("-", 1)[-1]
            self.mindmaps[mid] = Mindmap(
                id=mid,
                title=meta.get("title") or path.stem,
                html=render_markdown(body),
                part_id=mapping.get(mid),
            )

    def part(self, part_id: str) -> Part:
        for item in self.parts:
            if item.id == part_id:
                return item
        raise KeyError(part_id)

    def chapter_html(self, chapter_id: str) -> str:
        return render_markdown(self.chapters[chapter_id].body)

    def neighbors(self, chapter_id: str) -> tuple[Chapter | None, Chapter | None]:
        chapter = self.chapters[chapter_id]
        prev_c = self.ordered[chapter.index - 1] if chapter.index > 0 else None
        next_c = (
            self.ordered[chapter.index + 1]
            if chapter.index + 1 < len(self.ordered)
            else None
        )
        return prev_c, next_c

    def public_questions(self, chapter_id: str) -> list[dict]:
        return [_public_question(q) for q in self.questions_by_chapter.get(chapter_id, [])]

    def grade(self, question_id: str, choice: int) -> dict:
        question = next(q for q in self.questions if q.id == question_id)
        correct = int(choice) == question.answer
        return {
            "id": question.id,
            "correct": correct,
            "answer": question.answer,
            "explanation": question.explanation,
            "correct_option": question.options[question.answer] if question.options else "",
        }

    def search(self, query: str, limit: int = 5) -> list[Chunk]:
        tokens = _tokens(query)
        if not tokens:
            return []
        scored: list[tuple[float, Chunk]] = []
        for chunk in self.chunks:
            hay = f"{chunk.title} {chunk.part_title} {' '.join(chunk.tags)} {chunk.text}"
            score = 0.0
            for token in tokens:
                if token in chunk.title:
                    score += 8
                if token in chunk.part_title:
                    score += 3
                if token in chunk.tags:
                    score += 4
                score += hay.count(token) * 0.4
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [chunk for _, chunk in scored[:limit]]

    def excerpt(self, chapter_id: str, chars: int = 420) -> str:
        text = _plain(self.chapters[chapter_id].body)
        return text[:chars] + ("…" if len(text) > chars else "")


@lru_cache(maxsize=1)
def get_library() -> ContentLibrary:
    return ContentLibrary()


def render_markdown(source: str) -> str:
    html = markdown.markdown(
        source,
        extensions=["fenced_code", "tables", "sane_lists", "nl2br"],
    )
    return MERMAID_PRE_RE.sub(lambda m: f'<div class="mermaid">{m.group(1)}</div>', html)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    return yaml.safe_load(match.group(1)) or {}, match.group(2)


def _plain(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[#>*_`\\-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(query: str) -> list[str]:
    raw = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9+./-]{1,}", query)
    extras = re.findall(r"[\u4e00-\u9fff]", query)
    seen: list[str] = []
    for token in raw + extras:
        if token not in seen:
            seen.append(token)
    return seen[:24]


def _public_question(question: Question) -> dict:
    return {
        "id": question.id,
        "chapter": question.chapter,
        "type": question.type,
        "question": question.question,
        "options": question.options,
        "difficulty": question.difficulty,
    }
