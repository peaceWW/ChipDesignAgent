from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from app import agent, progress
from app.config import HOST, PORT
from app.content_loader import get_library

APP_DIR = Path(__file__).resolve().parent
app = FastAPI(title="芯片设计入门智能体")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

COOKIE = "learner_id"


class LearnerCookieMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        uid = request.cookies.get(COOKIE) or progress.new_user_id()
        request.state.user_id = uid
        response = await call_next(request)
        response.set_cookie(COOKIE, uid, max_age=60 * 60 * 24 * 365, httponly=False, samesite="lax")
        return response


app.add_middleware(LearnerCookieMiddleware)


@app.on_event("startup")
def _startup() -> None:
    progress.init_db()
    get_library()


def _user_id(request: Request) -> str:
    return request.state.user_id


def _ctx(request: Request, extra: dict | None = None) -> dict:
    library = get_library()
    payload = {
        "request": request,
        "book": library.book,
        "parts": library.parts,
        "agent_ready": agent.agent_enabled(),
    }
    if extra:
        payload.update(extra)
    return payload


def _html(request: Request, name: str, extra: dict | None = None):
    return templates.TemplateResponse(request, name, _ctx(request, extra))


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    uid = _user_id(request)
    library = get_library()
    snap = progress.snapshot(uid)
    stats = _progress_stats(library, snap)
    return _html(request, "home.html", {"stats": stats, "mindmaps": library.mindmaps})


@app.get("/part/{part_id}", response_class=HTMLResponse)
def part_page(part_id: str, request: Request):
    uid = _user_id(request)
    library = get_library()
    try:
        part = library.part(part_id)
    except KeyError as exc:
        raise HTTPException(404, "篇目不存在") from exc
    snap = progress.snapshot(uid)
    mindmap = next((m for m in library.mindmaps.values() if m.part_id == part_id), None)
    return _html(
        request,
        "part.html",
        {
            "part": part,
            "snap": snap,
            "mindmap": mindmap,
            "exam_id": f"{part.id}-exam",
            "exam_count": len(library.questions_by_chapter.get(f"{part.id}-exam", [])),
        },
    )


@app.get("/chapter/{chapter_id}", response_class=HTMLResponse)
def chapter_page(chapter_id: str, request: Request):
    uid = _user_id(request)
    library = get_library()
    if chapter_id not in library.chapters:
        raise HTTPException(404, "章节不存在")
    chapter = library.chapters[chapter_id]
    part = library.part(chapter.part)
    prev_c, next_c = library.neighbors(chapter_id)
    progress.mark_read(uid, chapter_id)
    snap = progress.snapshot(uid)
    return _html(
        request,
        "chapter.html",
        {
            "chapter": chapter,
            "part": part,
            "html": library.chapter_html(chapter_id),
            "prev": prev_c,
            "next": next_c,
            "questions": library.public_questions(chapter_id),
            "quiz_result": snap["quizzes"].get(chapter_id),
            "prereqs": [library.chapters[pid] for pid in chapter.prereq if pid in library.chapters],
        },
    )


@app.get("/mindmap", response_class=HTMLResponse)
def mindmap_index(request: Request):
    library = get_library()
    return _html(
        request,
        "mindmap.html",
        {"current": library.mindmaps.get("overview"), "mindmaps": library.mindmaps},
    )


@app.get("/mindmap/{map_id}", response_class=HTMLResponse)
def mindmap_page(map_id: str, request: Request):
    library = get_library()
    current = library.mindmaps.get(map_id)
    if not current:
        raise HTTPException(404, "导图不存在")
    return _html(request, "mindmap.html", {"current": current, "mindmaps": library.mindmaps})


@app.get("/exam/{part_id}", response_class=HTMLResponse)
def exam_page(part_id: str, request: Request):
    _user_id(request)
    library = get_library()
    try:
        part = library.part(part_id)
    except KeyError as exc:
        raise HTTPException(404, "篇目不存在") from exc
    exam_id = f"{part_id}-exam"
    questions = library.public_questions(exam_id)
    if not questions:
        raise HTTPException(404, "本篇暂无综合测验")
    return _html(request, "exam.html", {"part": part, "questions": questions, "exam_id": exam_id})


@app.get("/api/progress")
def api_progress(request: Request):
    uid = _user_id(request)
    library = get_library()
    snap = progress.snapshot(uid)
    return {"user_id": uid, **snap, "stats": _progress_stats(library, snap)}


class ReadBody(BaseModel):
    chapter_id: str


@app.post("/api/progress/read")
def api_mark_read(body: ReadBody, request: Request):
    uid = _user_id(request)
    if body.chapter_id not in get_library().chapters:
        raise HTTPException(404, "章节不存在")
    progress.mark_read(uid, body.chapter_id)
    return {"ok": True}


class QuizSubmit(BaseModel):
    chapter_id: str
    answers: dict[str, int] = Field(default_factory=dict)


@app.post("/api/quiz/submit")
def api_quiz(body: QuizSubmit, request: Request):
    uid = _user_id(request)
    library = get_library()
    bank = library.questions_by_chapter.get(body.chapter_id)
    if not bank:
        raise HTTPException(404, "没有对应题目")
    detail = []
    score = 0
    for question in bank:
        choice = body.answers.get(question.id)
        if choice is None:
            detail.append({"id": question.id, "correct": False, "skipped": True, "explanation": question.explanation, "answer": question.answer})
            continue
        graded = library.grade(question.id, choice)
        if graded["correct"]:
            score += 1
        detail.append(graded)
    progress.save_quiz(uid, body.chapter_id, score, len(bank), detail)
    return {"ok": True, "score": score, "total": len(bank), "detail": detail}


class AgentBody(BaseModel):
    message: str = ""
    mode: str = "ask"
    history: list[dict] = Field(default_factory=list)


@app.post("/api/agent")
async def api_agent(body: AgentBody, request: Request):
    uid = _user_id(request)
    library = get_library()
    snap = progress.snapshot(uid)
    return await agent.reply(
        library,
        body.message,
        body.mode,
        set(snap["reads"].keys()),
        body.history,
    )


@app.get("/health")
def health():
    library = get_library()
    return {
        "ok": True,
        "chapters": len(library.ordered),
        "questions": len(library.questions),
        "agent": agent.agent_enabled(),
    }


def _progress_stats(library, snap: dict) -> dict:
    total = len(library.ordered)
    read_n = len(snap["reads"])
    quiz_n = sum(1 for cid in library.chapters if cid in snap["quizzes"])
    exam_ids = [f"{part.id}-exam" for part in library.parts]
    exam_done = sum(1 for eid in exam_ids if eid in snap["quizzes"])
    parts = []
    for part in library.parts:
        ids = [c.id for c in part.chapters]
        r = sum(1 for cid in ids if cid in snap["reads"])
        q = sum(1 for cid in ids if cid in snap["quizzes"])
        parts.append(
            {
                "id": part.id,
                "read": r,
                "quiz": q,
                "total": len(ids),
                "pct": int(round(100 * r / len(ids))) if ids else 0,
            }
        )
    return {
        "read": read_n,
        "total": total,
        "pct": int(round(100 * read_n / total)) if total else 0,
        "quiz": quiz_n,
        "exam_done": exam_done,
        "exam_total": len(exam_ids),
        "parts": {item["id"]: item for item in parts},
    }


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    run()
