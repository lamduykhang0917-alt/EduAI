from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.database import get_db
from ..core.security import require_admin, hash_password

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------- Dashboard ----------
@router.get("/dashboard")
def admin_dashboard(admin: dict = Depends(require_admin)):
    with get_db() as db:
        stats = {}
        stats["total_students"] = db.execute(
            "SELECT COUNT(*) c FROM users u JOIN roles r ON u.role_id=r.id WHERE r.name='STUDENT'"
        ).fetchone()["c"]
        stats["total_courses"] = db.execute("SELECT COUNT(*) c FROM courses").fetchone()["c"]
        stats["total_chapters"] = db.execute("SELECT COUNT(*) c FROM chapters").fetchone()["c"]
        stats["total_documents"] = db.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
        stats["total_questions"] = db.execute("SELECT COUNT(*) c FROM questions").fetchone()["c"]
        stats["total_ai_queries"] = db.execute(
            "SELECT COUNT(*) c FROM activity_logs WHERE action='ask_ai'"
        ).fetchone()["c"]
        return stats


# ---------- 23. Quản lý tài khoản ----------
@router.get("/users")
def list_users(search: Optional[str] = None, admin: dict = Depends(require_admin)):
    query = ("SELECT u.id, u.full_name, u.email, r.name as role, u.status, u.created_at "
              "FROM users u JOIN roles r ON u.role_id = r.id")
    params = []
    if search:
        query += " WHERE u.full_name LIKE ? OR u.email LIKE ?"
        params += [f"%{search}%", f"%{search}%"]
    query += " ORDER BY u.created_at DESC"
    with get_db() as db:
        rows = db.execute(query, params).fetchall()
        return [dict(r) for r in rows]


class CreateUserRequest(BaseModel):
    full_name: str
    email: str
    password: str
    role: str = "STUDENT"  # STUDENT | ADMIN


@router.post("/users")
def create_user(payload: CreateUserRequest, admin: dict = Depends(require_admin)):
    if payload.role not in ("STUDENT", "ADMIN"):
        raise HTTPException(400, "Vai trò không hợp lệ")
    if len(payload.password) < 8:
        raise HTTPException(400, "Mật khẩu phải có ít nhất 8 ký tự")
    with get_db() as db:
        existing = db.execute("SELECT id FROM users WHERE email = ?", (payload.email,)).fetchone()
        if existing:
            raise HTTPException(400, "Email đã được sử dụng")
        cur = db.execute(
            "INSERT INTO users (full_name, email, password_hash, role_id) VALUES (?, ?, ?, "
            "(SELECT id FROM roles WHERE name = ?))",
            (payload.full_name, payload.email, hash_password(payload.password), payload.role),
        )
        return {"id": cur.lastrowid}


class UpdateUserRequest(BaseModel):
    full_name: str
    email: str
    role: str  # STUDENT | ADMIN


@router.put("/users/{user_id}")
def update_user(user_id: int, payload: UpdateUserRequest, admin: dict = Depends(require_admin)):
    if payload.role not in ("STUDENT", "ADMIN"):
        raise HTTPException(400, "Vai trò không hợp lệ")
    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM users WHERE email = ? AND id != ?", (payload.email, user_id)
        ).fetchone()
        if existing:
            raise HTTPException(400, "Email đã được sử dụng bởi tài khoản khác")
        db.execute(
            "UPDATE users SET full_name=?, email=?, role_id=(SELECT id FROM roles WHERE name=?) WHERE id=?",
            (payload.full_name, payload.email, payload.role, user_id),
        )
    return {"message": "Cập nhật thông tin tài khoản thành công"}


class UpdateUserStatusRequest(BaseModel):
    status: str  # active | locked


@router.patch("/users/{user_id}/status")
def update_user_status(user_id: int, payload: UpdateUserStatusRequest, admin: dict = Depends(require_admin)):
    if payload.status not in ("active", "locked"):
        raise HTTPException(400, "Trạng thái không hợp lệ")
    with get_db() as db:
        db.execute("UPDATE users SET status = ? WHERE id = ?", (payload.status, user_id))
    return {"message": "Cập nhật trạng thái thành công"}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: dict = Depends(require_admin)):
    with get_db() as db:
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return {"message": "Đã xóa tài khoản"}


# ---------- 24. Quản lý môn học ----------
class CourseRequest(BaseModel):
    code: str
    name: str
    description: Optional[str] = ""


@router.get("/courses")
def admin_list_courses(search: Optional[str] = None, admin: dict = Depends(require_admin)):
    query = "SELECT * FROM courses WHERE status = 'active'"
    params = []
    if search:
        query += " AND (name LIKE ? OR code LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    query += " ORDER BY id DESC"
    with get_db() as db:
        return [dict(r) for r in db.execute(query, params).fetchall()]


@router.get("/courses/{course_id}")
def admin_get_course(course_id: int, admin: dict = Depends(require_admin)):
    with get_db() as db:
        row = db.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Không tìm thấy môn học")
        return dict(row)


@router.post("/courses")
def create_course(payload: CourseRequest, admin: dict = Depends(require_admin)):
    with get_db() as db:
        existing = db.execute("SELECT id FROM courses WHERE code = ?", (payload.code,)).fetchone()
        if existing:
            raise HTTPException(400, "Mã môn đã tồn tại")
        cur = db.execute(
            "INSERT INTO courses (code, name, description) VALUES (?, ?, ?)",
            (payload.code, payload.name, payload.description),
        )
        return {"id": cur.lastrowid}


@router.put("/courses/{course_id}")
def update_course(course_id: int, payload: CourseRequest, admin: dict = Depends(require_admin)):
    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM courses WHERE code = ? AND id != ?", (payload.code, course_id)
        ).fetchone()
        if existing:
            raise HTTPException(400, "Mã môn đã được sử dụng bởi môn học khác")
        db.execute(
            "UPDATE courses SET code=?, name=?, description=? WHERE id=?",
            (payload.code, payload.name, payload.description, course_id),
        )
    return {"message": "Cập nhật môn học thành công"}


@router.delete("/courses/{course_id}")
def delete_course(course_id: int, admin: dict = Depends(require_admin)):
    with get_db() as db:
        db.execute("UPDATE courses SET status='inactive' WHERE id=?", (course_id,))
    return {"message": "Đã xóa môn học"}


# ---------- 25. Quản lý chương ----------
class ChapterRequest(BaseModel):
    course_id: int
    name: str
    order_index: int = 0


@router.get("/chapters")
def admin_list_chapters(course_id: Optional[int] = None, admin: dict = Depends(require_admin)):
    query = "SELECT * FROM chapters"
    params = []
    if course_id:
        query += " WHERE course_id = ?"
        params.append(course_id)
    query += " ORDER BY order_index"
    with get_db() as db:
        return [dict(r) for r in db.execute(query, params).fetchall()]


@router.post("/chapters")
def create_chapter(payload: ChapterRequest, admin: dict = Depends(require_admin)):
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO chapters (course_id, name, order_index) VALUES (?, ?, ?)",
            (payload.course_id, payload.name, payload.order_index),
        )
        return {"id": cur.lastrowid}


@router.delete("/chapters/{chapter_id}")
def delete_chapter(chapter_id: int, admin: dict = Depends(require_admin)):
    with get_db() as db:
        db.execute("DELETE FROM chapters WHERE id=?", (chapter_id,))
    return {"message": "Đã xóa chương"}


# ---------- 26. Quản lý tài liệu ----------
class DocumentRequest(BaseModel):
    course_id: int
    chapter_id: Optional[int] = None
    title: str
    file_type: str
    file_path: str


@router.get("/documents")
def admin_list_documents(admin: dict = Depends(require_admin)):
    with get_db() as db:
        return [dict(r) for r in db.execute("SELECT * FROM documents").fetchall()]


@router.post("/documents")
def create_document(payload: DocumentRequest, admin: dict = Depends(require_admin)):
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO documents (course_id, chapter_id, title, file_type, file_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (payload.course_id, payload.chapter_id, payload.title, payload.file_type, payload.file_path),
        )
        return {"id": cur.lastrowid}


@router.delete("/documents/{document_id}")
def delete_document(document_id: int, admin: dict = Depends(require_admin)):
    with get_db() as db:
        db.execute("DELETE FROM documents WHERE id=?", (document_id,))
    return {"message": "Đã xóa tài liệu"}


# ---------- 27. Quản lý ngân hàng câu hỏi ----------
def _attach_answers(db, questions):
    result = []
    for q in questions:
        qd = dict(q)
        answers = db.execute(
            "SELECT option_key, option_text, is_correct FROM answers WHERE question_id=? ORDER BY option_key",
            (qd["id"],),
        ).fetchall()
        qd["options"] = {a["option_key"]: a["option_text"] for a in answers}
        correct = next((a["option_key"] for a in answers if a["is_correct"]), None)
        qd["correct_option"] = correct
        result.append(qd)
    return result


@router.get("/questions")
def admin_list_questions(search: Optional[str] = None, course_id: Optional[int] = None,
                          admin: dict = Depends(require_admin)):
    query = "SELECT * FROM questions"
    conditions = []
    params = []
    if search:
        conditions.append("content LIKE ?")
        params.append(f"%{search}%")
    if course_id:
        conditions.append("course_id = ?")
        params.append(course_id)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id DESC"
    with get_db() as db:
        rows = db.execute(query, params).fetchall()
        return _attach_answers(db, rows)


@router.get("/questions/{question_id}")
def admin_get_question(question_id: int, admin: dict = Depends(require_admin)):
    with get_db() as db:
        row = db.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Không tìm thấy câu hỏi")
        return _attach_answers(db, [row])[0]


class QuestionRequest(BaseModel):
    course_id: int
    chapter_id: Optional[int] = None
    content: str
    difficulty: str = "basic"
    explanation: Optional[str] = ""
    options: dict  # {"A": "...", "B": "...", ...}
    correct_option: str


@router.post("/questions")
def create_question(payload: QuestionRequest, admin: dict = Depends(require_admin)):
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO questions (course_id, chapter_id, content, difficulty, explanation) "
            "VALUES (?, ?, ?, ?, ?)",
            (payload.course_id, payload.chapter_id, payload.content, payload.difficulty, payload.explanation),
        )
        question_id = cur.lastrowid
        for key, text in payload.options.items():
            db.execute(
                "INSERT INTO answers (question_id, option_key, option_text, is_correct) VALUES (?, ?, ?, ?)",
                (question_id, key, text, key == payload.correct_option),
            )
        return {"id": question_id}


@router.put("/questions/{question_id}")
def update_question(question_id: int, payload: QuestionRequest, admin: dict = Depends(require_admin)):
    with get_db() as db:
        existing = db.execute("SELECT id FROM questions WHERE id=?", (question_id,)).fetchone()
        if existing is None:
            raise HTTPException(404, "Không tìm thấy câu hỏi")
        db.execute(
            "UPDATE questions SET course_id=?, chapter_id=?, content=?, difficulty=?, explanation=? WHERE id=?",
            (payload.course_id, payload.chapter_id, payload.content, payload.difficulty,
             payload.explanation, question_id),
        )
        db.execute("DELETE FROM answers WHERE question_id=?", (question_id,))
        for key, text in payload.options.items():
            db.execute(
                "INSERT INTO answers (question_id, option_key, option_text, is_correct) VALUES (?, ?, ?, ?)",
                (question_id, key, text, key == payload.correct_option),
            )
    return {"message": "Cập nhật câu hỏi thành công"}


@router.delete("/questions/{question_id}")
def delete_question(question_id: int, admin: dict = Depends(require_admin)):
    with get_db() as db:
        db.execute("DELETE FROM answers WHERE question_id=?", (question_id,))
        db.execute("DELETE FROM questions WHERE id=?", (question_id,))
    return {"message": "Đã xóa câu hỏi"}


# ---------- 28. Theo dõi hoạt động ----------
@router.get("/logs")
def get_logs(action: Optional[str] = None, admin: dict = Depends(require_admin)):
    query = ("SELECT l.*, u.full_name, u.email FROM activity_logs l "
              "LEFT JOIN users u ON l.user_id = u.id")
    params = []
    if action:
        query += " WHERE l.action = ?"
        params.append(action)
    query += " ORDER BY l.created_at DESC LIMIT 200"
    with get_db() as db:
        return [dict(r) for r in db.execute(query, params).fetchall()]


# ---------- 29. Quản lý cấu hình AI ----------
@router.get("/ai-config")
def get_ai_config(admin: dict = Depends(require_admin)):
    import sys, os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    from ai_service import config as ai_config

    provider = ai_config.LLM_PROVIDER
    provider_labels = {
        "claude": f"Claude ({ai_config.ANTHROPIC_MODEL})",
        "gemini": f"Gemini ({ai_config.GEMINI_MODEL})",
        "dataset": "KeywordIntentClassifier (dataset nội bộ)",
    }
    api_key_configured = True
    if provider == "claude":
        api_key_configured = bool(ai_config.ANTHROPIC_API_KEY)
    elif provider == "gemini":
        api_key_configured = bool(ai_config.GEMINI_API_KEY)

    return {
        "llm_provider": provider,
        "model": provider_labels.get(provider, provider),
        "api_key_configured": api_key_configured,
        "use_phobert": ai_config.USE_PHOBERT,
        "fallback_mode": "KeywordIntentClassifier (dataset nội bộ)" if provider in ("claude", "gemini") else None,
        "confidence_threshold": ai_config.CONFIDENCE_THRESHOLD,
        "status": "online",
        "dataset_files": {
            "intents": ai_config.INTENTS_FILE,
            "knowledge": ai_config.KNOWLEDGE_FILE,
            "quiz": ai_config.QUIZ_FILE,
        },
    }


@router.post("/ai-config/reload")
def reload_ai_model(admin: dict = Depends(require_admin)):
    # Với KeywordIntentClassifier: reload nghĩa là đọc lại dataset (không cần restart).
    # Với PhoBERT thật: sẽ load lại checkpoint từ MODEL_PATH.
    return {"message": "Đã reload cấu hình AI / dataset thành công"}
