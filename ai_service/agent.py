import json
import os
import sqlite3
from contextlib import contextmanager

import requests

from . import config
from . import tools

DB_PATH = os.path.join(config.BASE_DIR, "backend", "eduai.db")

MAX_STEPS = 5

AGENT_SYSTEM_PROMPT = (
    "Bạn là EduAI Agent, một AI Agent hỗ trợ học tập cho sinh viên. Bạn có thể dùng các "
    "tool được cung cấp để tra cứu tài liệu/knowledge base, tạo bài kiểm tra, nộp bài, "
    "xem tiến độ học tập và lấy đề xuất học tập. "
    "Luôn ưu tiên gọi tool để lấy thông tin thật trước khi trả lời các câu hỏi liên quan đến "
    "kiến thức, bài kiểm tra, điểm số hoặc tiến độ học tập — không tự bịa thông tin nếu tool "
    "không trả về kết quả phù hợp; khi đó hãy nói rõ là chưa tìm thấy và gợi ý sinh viên hỏi rõ "
    "hơn. Với hành động làm thay đổi dữ liệu kết quả học tập (nộp bài kiểm tra), hệ thống sẽ tự "
    "yêu cầu sinh viên xác nhận trước khi thực thi, bạn không cần nhắc lại điều này. "
    "Trả lời ngắn gọn, rõ ràng, bằng tiếng Việt."
)


class AgentError(Exception):
    pass


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _create_task(user_id: int, request_text: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO agent_tasks (user_id, request_text, status) VALUES (?, ?, 'running')",
            (user_id, request_text[:500]),
        )
        return cur.lastrowid


def _update_task_status(task_id: int, status: str, completed: bool = False):
    with _connect() as conn:
        if completed:
            conn.execute(
                "UPDATE agent_tasks SET status = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, task_id),
            )
        else:
            conn.execute("UPDATE agent_tasks SET status = ? WHERE id = ?", (status, task_id))


def _log_tool_call(task_id: int, tool_name: str, arguments: dict, result, success: bool):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO agent_tool_logs (task_id, tool_name, arguments, result, success) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, tool_name, json.dumps(arguments, ensure_ascii=False),
             json.dumps(result, ensure_ascii=False)[:4000], success),
        )


def _call_claude_messages(messages: list, system: str) -> dict:
    if not config.ANTHROPIC_API_KEY:
        raise AgentError(
            "Chưa cấu hình ANTHROPIC_API_KEY cho AI Agent. Xem hướng dẫn trong ai_service/config.py."
        )
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.ANTHROPIC_MODEL,
                "max_tokens": 1024,
                "system": system,
                "messages": messages,
                "tools": tools.TOOL_DEFINITIONS,
            },
            timeout=45,
        )
    except requests.RequestException as e:
        raise AgentError(f"Không thể kết nối tới Claude API: {e}")

    if resp.status_code != 200:
        raise AgentError(f"Claude API trả về lỗi ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def _extract_text(content_blocks: list) -> str:
    parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
    return "\n".join(parts).strip()


def run_agent(message: str, user_id: int, course: str = None, history: list = None) -> dict:
    task_id = _create_task(user_id, message)
    messages = list(history or [])
    messages.append({"role": "user", "content": message})

    tool_call_log = []

    for _ in range(MAX_STEPS):
        try:
            response = _call_claude_messages(messages, AGENT_SYSTEM_PROMPT)
        except AgentError as e:
            _update_task_status(task_id, "failed", completed=True)
            return {
                "task_id": task_id,
                "status": "failed",
                "answer": f"Agent chưa thể xử lý yêu cầu: {e}",
                "tool_calls": tool_call_log,
            }

        content_blocks = response.get("content", [])
        stop_reason = response.get("stop_reason")
        tool_use_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]

        if stop_reason != "tool_use" or not tool_use_blocks:
            answer = _extract_text(content_blocks) or "Xin lỗi, tôi chưa có câu trả lời phù hợp."
            _update_task_status(task_id, "completed", completed=True)
            return {
                "task_id": task_id,
                "status": "completed",
                "answer": answer,
                "tool_calls": tool_call_log,
            }

        gated = [b for b in tool_use_blocks if b["name"] in tools.CONFIRMATION_REQUIRED]
        if gated:
            block = gated[0]
            _update_task_status(task_id, "awaiting_confirmation")
            return {
                "task_id": task_id,
                "status": "awaiting_confirmation",
                "answer": "Hành động này sẽ nộp bài kiểm tra của bạn. Bạn xác nhận chứ?",
                "pending_action": {"tool": block["name"], "arguments": block["input"]},
                "tool_calls": tool_call_log,
            }

        messages.append({"role": "assistant", "content": content_blocks})
        tool_result_blocks = []
        for block in tool_use_blocks:
            try:
                result = tools.execute(block["name"], block["input"], user_id)
                success = True
            except tools.ToolError as e:
                result = {"error": str(e)}
                success = False
            _log_tool_call(task_id, block["name"], block["input"], result, success)
            tool_call_log.append({"tool": block["name"], "arguments": block["input"], "result": result})
            tool_result_blocks.append({
                "type": "tool_result",
                "tool_use_id": block["id"],
                "content": json.dumps(result, ensure_ascii=False),
            })
        messages.append({"role": "user", "content": tool_result_blocks})

    _update_task_status(task_id, "failed", completed=True)
    return {
        "task_id": task_id,
        "status": "failed",
        "answer": "Agent đã đạt giới hạn số bước xử lý cho yêu cầu này. Vui lòng thử diễn đạt lại câu hỏi.",
        "tool_calls": tool_call_log,
    }


def confirm_action(task_id: int, tool_name: str, arguments: dict, user_id: int, approve: bool) -> dict:
    if not approve:
        _update_task_status(task_id, "failed", completed=True)
        return {"task_id": task_id, "status": "failed", "answer": "Đã hủy hành động theo yêu cầu của bạn."}

    try:
        result = tools.execute(tool_name, arguments, user_id)
        success = True
    except tools.ToolError as e:
        result = {"error": str(e)}
        success = False

    _log_tool_call(task_id, tool_name, arguments, result, success)
    _update_task_status(task_id, "completed" if success else "failed", completed=True)

    if not success:
        return {"task_id": task_id, "status": "failed", "answer": result.get("error", "Có lỗi xảy ra.")}

    if tool_name == "submit_quiz":
        answer = (
            f"Đã nộp bài. Bạn đúng {result['correct_count']}/{result['total_count']} câu, "
            f"điểm số: {result['score']}/10."
        )
    else:
        answer = "Đã thực hiện hành động thành công."

    return {"task_id": task_id, "status": "completed", "answer": answer, "result": result}
