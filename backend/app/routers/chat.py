from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import sys, os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from ai_service.inference import generate_response  # noqa: E402

from ..core.database import get_db
from ..core.security import get_current_user

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[int] = None
    course: Optional[str] = None
    level: Optional[str] = "basic"


@router.post("")
def chat(payload: ChatRequest, user: dict = Depends(get_current_user)):
    with get_db() as db:
        session_id = payload.session_id
        if session_id is None:
            title = payload.message[:40]
            cur = db.execute(
                "INSERT INTO chat_sessions (user_id, course_id, title) VALUES (?, NULL, ?)",
                (user["id"], title),
            )
            session_id = cur.lastrowid

        db.execute(
            "INSERT INTO chat_messages (session_id, sender, content) VALUES (?, 'user', ?)",
            (session_id, payload.message),
        )

        course_rows = db.execute("SELECT name FROM courses WHERE status = 'active'").fetchall()
        course_list = [r["name"] for r in course_rows]

    result = generate_response(payload.message, course=payload.course, level=payload.level or "basic", course_list=course_list)

    with get_db() as db:
        db.execute(
            "INSERT INTO chat_messages (session_id, sender, content, intent, confidence) "
            "VALUES (?, 'ai', ?, ?, ?)",
            (session_id, result["answer"], result["intent"], result["confidence"]),
        )
        db.execute(
            "INSERT INTO activity_logs (user_id, action, detail) VALUES (?, 'ask_ai', ?)",
            (user["id"], payload.message[:100]),
        )

    return {
        "session_id": session_id,
        "answer": result["answer"],
        "intent": result["intent"],
        "confidence": result["confidence"],
        "source": result["source"],
        "needs_clarification": result["needs_clarification"],
    }


@router.get("/history")
def chat_history(session_id: Optional[int] = None, user: dict = Depends(get_current_user)):
    with get_db() as db:
        if session_id:
            rows = db.execute(
                "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

        sessions = db.execute(
            "SELECT * FROM chat_sessions WHERE user_id = ? ORDER BY created_at DESC",
            (user["id"],),
        ).fetchall()
        return [dict(s) for s in sessions]


@router.delete("/history/{session_id}")
def delete_history(session_id: int, user: dict = Depends(get_current_user)):
    with get_db() as db:
        owner = db.execute(
            "SELECT user_id FROM chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if owner is None or owner["user_id"] != user["id"]:
            raise HTTPException(404, "Không tìm thấy cuộc trò chuyện")
        db.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        db.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
    return {"message": "Đã xóa cuộc trò chuyện"}
