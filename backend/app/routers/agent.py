import os
import sys
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from ai_service import agent as ai_agent  # noqa: E402
from ai_service import rag as ai_rag  # noqa: E402

from ..core.database import get_db
from ..core.security import get_current_user, require_admin

router = APIRouter(prefix="/api/agent", tags=["agent"])
admin_router = APIRouter(prefix="/api/admin/agent", tags=["admin-agent"])


class AgentChatRequest(BaseModel):
    message: str
    session_id: Optional[int] = None
    course: Optional[str] = None


class AgentConfirmRequest(BaseModel):
    task_id: int
    tool: str
    arguments: dict
    approve: bool


def _load_history(session_id: int, limit: int = 8) -> list:
    if session_id is None:
        return []
    with get_db() as db:
        rows = db.execute(
            "SELECT sender, content FROM chat_messages WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    history = []
    for row in reversed(rows):
        role = "assistant" if row["sender"] == "ai" else "user"
        history.append({"role": role, "content": row["content"]})
    return history


def _ensure_session(session_id: Optional[int], user_id: int, title: str) -> int:
    with get_db() as db:
        if session_id is not None:
            return session_id
        cur = db.execute(
            "INSERT INTO chat_sessions (user_id, course_id, title) VALUES (?, NULL, ?)",
            (user_id, title[:40]),
        )
        return cur.lastrowid


def _save_message(session_id: int, sender: str, content: str):
    with get_db() as db:
        db.execute(
            "INSERT INTO chat_messages (session_id, sender, content) VALUES (?, ?, ?)",
            (session_id, sender, content),
        )


@router.post("/chat")
def agent_chat(payload: AgentChatRequest, user: dict = Depends(get_current_user)):
    session_id = _ensure_session(payload.session_id, user["id"], payload.message)
    history = _load_history(session_id)
    _save_message(session_id, "user", payload.message)

    result = ai_agent.run_agent(payload.message, user["id"], course=payload.course, history=history)

    _save_message(session_id, "ai", result["answer"])
    with get_db() as db:
        db.execute(
            "INSERT INTO activity_logs (user_id, action, detail) VALUES (?, 'agent_chat', ?)",
            (user["id"], payload.message[:100]),
        )

    return {"session_id": session_id, **result}


@router.post("/confirm")
def agent_confirm(payload: AgentConfirmRequest, user: dict = Depends(get_current_user)):
    result = ai_agent.confirm_action(
        payload.task_id, payload.tool, payload.arguments, user["id"], payload.approve
    )
    return result


@router.get("/tasks")
def agent_tasks(user: dict = Depends(get_current_user)):
    with get_db() as db:
        rows = db.execute(
            "SELECT id, request_text, status, created_at, completed_at FROM agent_tasks "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
            (user["id"],),
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/tasks/{task_id}/logs")
def agent_task_logs(task_id: int, user: dict = Depends(get_current_user)):
    with get_db() as db:
        task = db.execute(
            "SELECT id FROM agent_tasks WHERE id = ? AND user_id = ?", (task_id, user["id"])
        ).fetchone()
        if task is None:
            raise HTTPException(404, "Không tìm thấy task")
        rows = db.execute(
            "SELECT tool_name, arguments, result, success, created_at FROM agent_tool_logs "
            "WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]


@admin_router.get("/logs")
def admin_agent_logs(tool_name: Optional[str] = None, admin: dict = Depends(require_admin)):
    query = (
        "SELECT l.id, l.tool_name, l.arguments, l.result, l.success, l.created_at, "
        "t.user_id, u.full_name, t.request_text FROM agent_tool_logs l "
        "JOIN agent_tasks t ON l.task_id = t.id "
        "JOIN users u ON t.user_id = u.id"
    )
    params = []
    if tool_name:
        query += " WHERE l.tool_name = ?"
        params.append(tool_name)
    query += " ORDER BY l.created_at DESC LIMIT 200"
    with get_db() as db:
        return [dict(r) for r in db.execute(query, params).fetchall()]


@admin_router.get("/tasks")
def admin_agent_tasks(status: Optional[str] = None, admin: dict = Depends(require_admin)):
    query = (
        "SELECT t.id, t.request_text, t.status, t.created_at, t.completed_at, "
        "u.full_name, u.email FROM agent_tasks t JOIN users u ON t.user_id = u.id"
    )
    params = []
    if status:
        query += " WHERE t.status = ?"
        params.append(status)
    query += " ORDER BY t.created_at DESC LIMIT 200"
    with get_db() as db:
        return [dict(r) for r in db.execute(query, params).fetchall()]


class PromptTemplateRequest(BaseModel):
    name: str
    content: str


@admin_router.get("/prompts")
def list_prompts(admin: dict = Depends(require_admin)):
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM prompt_templates ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


@admin_router.post("/prompts")
def upsert_prompt(payload: PromptTemplateRequest, admin: dict = Depends(require_admin)):
    with get_db() as db:
        existing = db.execute(
            "SELECT id, version FROM prompt_templates WHERE name = ?", (payload.name,)
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE prompt_templates SET content = ?, version = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (payload.content, existing["version"] + 1, existing["id"]),
            )
            return {"id": existing["id"], "message": "Đã cập nhật prompt"}
        cur = db.execute(
            "INSERT INTO prompt_templates (name, content) VALUES (?, ?)",
            (payload.name, payload.content),
        )
        return {"id": cur.lastrowid, "message": "Đã tạo prompt mới"}


@admin_router.get("/rag/stats")
def rag_stats(admin: dict = Depends(require_admin)):
    return ai_rag.index_stats()


@admin_router.post("/rag/reindex")
def rag_reindex(admin: dict = Depends(require_admin)):
    return ai_rag.build_index()


@admin_router.get("/config")
def agent_config(admin: dict = Depends(require_admin)):
    from ai_service import config as ai_config
    return {
        "max_steps": ai_agent.MAX_STEPS,
        "tools": [t["name"] for t in ai_agent.tools.TOOL_DEFINITIONS],
        "confirmation_required_tools": sorted(ai_agent.tools.CONFIRMATION_REQUIRED),
        "llm_provider": ai_config.LLM_PROVIDER,
        "rag_index": ai_rag.index_stats(),
    }
