import json
import os
import sys
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.database import get_db
from ..core.security import get_current_user

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from ai_service import config as ai_config  # noqa: E402

router = APIRouter(prefix="/api/quizzes", tags=["quizzes"])


def _load_question_bank():
    with open(ai_config.QUIZ_FILE, "r", encoding="utf-8") as f:
        return json.load(f)["questions"]


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


@router.post("/generate")
def generate_quiz(payload: GenerateQuizRequest, user: dict = Depends(get_current_user)):
    bank = _load_question_bank()
    filtered = [q for q in bank if q["course"] == payload.course]
    if payload.chapter:
        filtered = [q for q in filtered if q["chapter"] == payload.chapter]
    if payload.difficulty:
        filtered = [q for q in filtered if q["difficulty"] == payload.difficulty]

    if not filtered:
        raise HTTPException(404, "Không tìm thấy câu hỏi phù hợp trong ngân hàng câu hỏi hiện có")

    selected = filtered[: payload.num_questions]

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
