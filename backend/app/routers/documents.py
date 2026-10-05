from fastapi import APIRouter, Depends, HTTPException
from typing import Optional

from ..core.database import get_db
from ..core.security import get_current_user

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.get("")
def list_documents(course_id: Optional[int] = None, chapter_id: Optional[int] = None,
                    search: Optional[str] = None, user: dict = Depends(get_current_user)):
    query = "SELECT * FROM documents WHERE status = 'active'"
    params = []
    if course_id:
        query += " AND course_id = ?"
        params.append(course_id)
    if chapter_id:
        query += " AND chapter_id = ?"
        params.append(chapter_id)
    if search:
        query += " AND title LIKE ?"
        params.append(f"%{search}%")

    with get_db() as db:
        rows = db.execute(query, params).fetchall()
        return [dict(r) for r in rows]


@router.get("/{document_id}")
def get_document(document_id: int, user: dict = Depends(get_current_user)):
    with get_db() as db:
        row = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Không tìm thấy tài liệu")
        db.execute(
            "INSERT INTO activity_logs (user_id, action, detail) VALUES (?, 'view_document', ?)",
            (user["id"], row["title"]),
        )
        return dict(row)
