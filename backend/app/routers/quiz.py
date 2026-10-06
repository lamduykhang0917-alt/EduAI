import json
import os
import random
import sys
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.database import get_db
from ..core.security import get_current_user

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from ai_service import config as ai_config  # noqa: E402

router = APIRouter(prefix="/api/quizzes", tags=["quizzes"])


def _read_questions(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("questions", [])


def _questions_from_knowledge():
    """Sinh câu hỏi trắc nghiệm từ knowledge base: câu hỏi = trường "question",
    đáp án đúng = "answer_basic", 3 đáp án nhiễu lấy từ các mục kiến thức khác.
    Vị trí đáp án đúng được cố định theo id nên không đổi giữa các lần gọi."""
    try:
        with open(ai_config.KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
            kb = json.load(f).get("knowledge_base", [])
    except OSError:
        return []

    def short(text, limit=200):
        text = " ".join(text.split())
        return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."

    result = []
    for idx, item in enumerate(kb):
        if not item.get("question") or not item.get("answer_basic"):
            continue
        rng = random.Random(item["id"])
        others = [o for o in kb if o["id"] != item["id"] and o.get("answer_basic")]
        same_course = [o for o in others if o.get("course") == item.get("course")]
        rng.shuffle(same_course)
        rest = [o for o in others if o not in same_course]
        rng.shuffle(rest)
        distractors = [short(o["answer_basic"]) for o in (same_course + rest)[:3]]
        if len(distractors) < 3:
            continue
        options = distractors
        pos = rng.randrange(4)
        options.insert(pos, short(item["answer_basic"]))
        result.append({
            "id": f"kbq_{item['id']}",
            "course": item["course"],
            "chapter": item.get("chapter", ""),
            "difficulty": "basic",
            "content": item["question"],
            "options": dict(zip("ABCD", options)),
            "correct_answer": "ABCD"[pos],
            "explanation": short(item.get("answer_detailed") or item["answer_basic"], 320),
        })
    return result


_BANK_CACHE = None


def _load_question_bank():
    """Ngân hàng câu hỏi = quiz.json (gốc) + quiz_extra.json (bổ sung) + câu hỏi sinh từ knowledge base."""
    global _BANK_CACHE
    if _BANK_CACHE is None:
        bank = _read_questions(ai_config.QUIZ_FILE) + _read_questions(ai_config.QUIZ_EXTRA_FILE)
        bank += _questions_from_knowledge()
        _BANK_CACHE = bank
    return _BANK_CACHE


class GenerateQuizRequest(BaseModel):
    course: str
    chapter: Optional[str] = None
    num_questions: int = 5
    difficulty: Optional[str] = None  # basic | medium | advanced


class SubmitQuizRequest(BaseModel):
    quiz_id: int
    answers: List[dict]  # [{"question_id": "q001", "selected_option": "B"}]
    duration_seconds: Optional[int] = 0


@router.get("")
def list_quizzes(user: dict = Depends(get_current_user)):
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM quizzes WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/meta")
def quiz_meta(user: dict = Depends(get_current_user)):
    """Số câu hỏi có sẵn theo môn và độ khó, để giao diện chỉ cho chọn những gì thực sự có."""
    meta = {}
    for q in _load_question_bank():
        m = meta.setdefault(q["course"], {"total": 0, "basic": 0, "medium": 0, "advanced": 0})
        m["total"] += 1
        if q["difficulty"] in m:
            m[q["difficulty"]] += 1
    return meta


@router.post("/generate")
def generate_quiz(payload: GenerateQuizRequest, user: dict = Depends(get_current_user)):
    bank = _load_question_bank()
    filtered = [q for q in bank if q["course"] == payload.course]
    if payload.chapter:
        filtered = [q for q in filtered if q["chapter"] == payload.chapter]
    if payload.difficulty:
        filtered = [q for q in filtered if q["difficulty"] == payload.difficulty]

    if not filtered:
        raise HTTPException(404, "Chưa có câu hỏi phù hợp cho lựa chọn này trong ngân hàng câu hỏi")

    wanted = max(1, min(payload.num_questions, 50))
    selected = random.sample(filtered, min(wanted, len(filtered)))

    with get_db() as db:
        course_row = db.execute("SELECT id FROM courses WHERE name = ?", (payload.course,)).fetchone()
        course_id = course_row["id"] if course_row else None
        cur = db.execute(
            "INSERT INTO quizzes (user_id, course_id, num_questions, difficulty) VALUES (?, ?, ?, ?)",
            (user["id"], course_id, len(selected), payload.difficulty or "mixed"),
        )
        quiz_id = cur.lastrowid

    return {
        "quiz_id": quiz_id,
        "requested": wanted,
        "available": len(filtered),
        "questions": [
            {
                "id": q["id"],
                "content": q["content"],
                "options": q["options"],
            }
            for q in selected
        ],
    }


@router.post("/submit")
def submit_quiz(payload: SubmitQuizRequest, user: dict = Depends(get_current_user)):
    bank = {q["id"]: q for q in _load_question_bank()}
    correct_count = 0
    detailed = []

    for ans in payload.answers:
        q = bank.get(ans.get("question_id"))
        if not q:
            continue
        is_correct = ans.get("selected_option") == q["correct_answer"]
        if is_correct:
            correct_count += 1
        detailed.append({
            "question_id": q["id"],
            "content": q["content"],
            "selected_option": ans.get("selected_option"),
            "correct_option": q["correct_answer"],
            "is_correct": is_correct,
            "explanation": q["explanation"],
        })

    total = len(payload.answers)
    score = round((correct_count / total) * 10, 2) if total else 0

    with get_db() as db:
        cur = db.execute(
            "INSERT INTO quiz_results (quiz_id, user_id, score, correct_count, total_count, duration_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (payload.quiz_id, user["id"], score, correct_count, total, payload.duration_seconds),
        )
        result_id = cur.lastrowid
        db.execute(
            "INSERT INTO activity_logs (user_id, action, detail) VALUES (?, 'submit_quiz', ?)",
            (user["id"], f"quiz_id={payload.quiz_id} score={score}"),
        )

    return {
        "result_id": result_id,
        "score": score,
        "correct_count": correct_count,
        "total_count": total,
        "details": detailed,
    }
