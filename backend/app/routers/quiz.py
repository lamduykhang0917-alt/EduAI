import random
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.database import get_db
from ..core.security import get_current_user

router = APIRouter(prefix="/api/quizzes", tags=["quizzes"])


def _db_questions(db, course=None, chapter=None, difficulty=None):
    """Đọc câu hỏi từ database (nguồn chính, admin có thể thêm/sửa/xóa)."""
    query = ("SELECT q.id, q.content, q.difficulty, q.explanation, c.name AS course, ch.name AS chapter "
             "FROM questions q JOIN courses c ON c.id = q.course_id AND c.status = 'active' "
             "LEFT JOIN chapters ch ON ch.id = q.chapter_id WHERE 1=1")
    params = []
    if course:
        query += " AND c.name = ?"
        params.append(course)
    if chapter:
        query += " AND ch.name = ?"
        params.append(chapter)
    if difficulty:
        query += " AND q.difficulty = ?"
        params.append(difficulty)
    rows = [dict(r) for r in db.execute(query, params).fetchall()]
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    options = {}
    for a in db.execute(
        f"SELECT question_id, option_key, option_text, is_correct FROM answers "
        f"WHERE question_id IN ({','.join('?' * len(ids))}) ORDER BY option_key", ids
    ).fetchall():
        options.setdefault(a["question_id"], []).append(a)
    result = []
    for r in rows:
        opts = options.get(r["id"], [])
        correct = next((o["option_key"] for o in opts if o["is_correct"]), None)
        if len(opts) < 2 or correct is None:
            continue  # câu hỏi chưa đủ đáp án thì không đưa vào bài kiểm tra
        r["options"] = {o["option_key"]: o["option_text"] for o in opts}
        r["correct_answer"] = correct
        r["explanation"] = r["explanation"] or ""
        result.append(r)
    return result


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
    with get_db() as db:
        bank = _db_questions(db)
    for q in bank:
        m = meta.setdefault(q["course"], {"total": 0, "basic": 0, "medium": 0, "advanced": 0})
        m["total"] += 1
        if q["difficulty"] in m:
            m[q["difficulty"]] += 1
    return meta


@router.post("/generate")
def generate_quiz(payload: GenerateQuizRequest, user: dict = Depends(get_current_user)):
    with get_db() as db:
        filtered = _db_questions(db, payload.course, payload.chapter, payload.difficulty)

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
    ids = [a.get("question_id") for a in payload.answers if isinstance(a.get("question_id"), int)]
    with get_db() as db:
        found = _db_questions(db)
    bank = {q["id"]: q for q in found if q["id"] in ids}
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
